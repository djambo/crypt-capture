"""
Real Azure Kinect capture node — drop-in replacement for node/sim_node.py.

Captures depth + color from an Azure Kinect via pyk4a, applies a cheap depth
range-clip (zeros everything outside a working distance — fast background
removal that also makes RVL compress well; AI matting with RVM/BGMv2 is the
later upgrade), RVL-compresses the masked depth, and streams Frames to the
central recorder using the exact same wire protocol as the simulator.

Color is pulled as BGRA from the sensor and warped to match the streamed point
grid (see "alignment" below).

**Live camera controls** (`set_camera` over the control channel, driven from the
UI): the depth FOV mode, color resolution, fps, and alignment direction can all
be changed while streaming.
  - depth FOV mode / color res / fps changes restart the sensor;
  - alignment is a free per-frame switch:
      color_to_depth — color warped into the DEPTH grid (1 pt / depth pixel);
      depth_to_color — depth warped into the COLOR grid (1 pt / color pixel) ->
                       much more color detail / a denser cloud.
The node re-reads its intrinsics (depth- or color-camera, per alignment) and
re-sends the CCAL handshake after any change, so the relay re-derives the cloud
correctly with zero viewer changes. See node/camera_modes.py for the tables.

Requires on the node: Azure Kinect SDK + depth engine installed (see
docs/jetson_setup.md), and `pip install pyk4a numpy`.

Single-sensor bring-up:
    # on the central machine:
    python3 -m central.recorder --port 9000 --sensors 1 --out takes/real1
    # on the Jetson (CENTRAL_IP = the recorder machine's LAN address):
    python3 -m node.kinect_node --host CENTRAL_IP --port 9000 --sensor 0 --frames 60

Multi-sensor (later): give the master sensor --sync master and the others
--sync sub, wire the 3.5mm sync cables, and trigger all nodes together.
"""

import argparse
import math
import socket
import threading
import time

import numpy as np
import pyk4a
from pyk4a import (
    PyK4A, Config, DepthMode, ColorResolution, FPS, ImageFormat, WiredSyncMode,
)

from protocol import control, rvl
from protocol.frame import Frame, encode_calib, encode_imu
from node import camera_modes
from node.background import BackgroundSubtractor, denoise_mask

# Map the string mode names (node/camera_modes.py) onto the pyk4a enums.
_DEPTH_ENUM = {
    "NFOV_UNBINNED": DepthMode.NFOV_UNBINNED,
    "NFOV_2X2BINNED": DepthMode.NFOV_2X2BINNED,
    "WFOV_2X2BINNED": DepthMode.WFOV_2X2BINNED,
    "WFOV_UNBINNED": DepthMode.WFOV_UNBINNED,
}
_COLOR_ENUM = {
    "720P": ColorResolution.RES_720P,
    "1080P": ColorResolution.RES_1080P,
    "1440P": ColorResolution.RES_1440P,
    "1536P": ColorResolution.RES_1536P,
    "2160P": ColorResolution.RES_2160P,
    "3072P": ColorResolution.RES_3072P,
}
_FPS_ENUM = {5: FPS.FPS_5, 15: FPS.FPS_15, 30: FPS.FPS_30}

# When IMU streaming is on, re-read + re-send the gravity vector this often (in
# frames) so the cloud reorients live as the camera turns, without spamming.
IMU_EVERY = 10


def _build_config(cfg, sync, sub_delay_us):
    mode = {
        "standalone": WiredSyncMode.STANDALONE,
        "master": WiredSyncMode.MASTER,
        "sub": WiredSyncMode.SUBORDINATE,
    }[sync]
    fps = camera_modes.clamp_fps(cfg["fps"], cfg["depth_mode"],
                                 cfg["color_resolution"])
    return Config(
        color_resolution=_COLOR_ENUM[cfg["color_resolution"]],
        color_format=ImageFormat.COLOR_BGRA32,      # raw pixels: needed to warp
                                                    # color into the depth grid
        depth_mode=_DEPTH_ENUM[cfg["depth_mode"]],
        camera_fps=_FPS_ENUM[fps],
        synchronized_images_only=True,
        wired_sync_mode=mode,
        subordinate_delay_off_master_usec=sub_delay_us,
    )


_IMU_EXTRINSIC_WARNED = [False]


def _accel_to_depth(k4a, x, y, z):
    """Rotate an accelerometer-frame vector into the depth-camera optical frame
    using the Kinect's factory ACCEL->DEPTH extrinsics. The Kinect IMU has its
    own axes (its "down" is NOT on the depth-Y axis), so skipping this leaves the
    floor sideways. We transform the tip and the origin and subtract, which
    cancels the translation and leaves a pure rotation (good for a direction).
    Falls back to identity (with a one-time warning) if pyk4a doesn't expose it.
    """
    try:
        cal = k4a.calibration
        accel = pyk4a.CalibrationType.ACCEL
        depth = pyk4a.CalibrationType.DEPTH
        p1 = cal.convert_3d_to_3d((x, y, z), accel, depth)
        p0 = cal.convert_3d_to_3d((0.0, 0.0, 0.0), accel, depth)
        return (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    except Exception as exc:
        if not _IMU_EXTRINSIC_WARNED[0]:
            print("IMU: no ACCEL->DEPTH extrinsic (%s); using raw axes — the "
                  "floor orientation may be wrong on this pyk4a build" % exc)
            _IMU_EXTRINSIC_WARNED[0] = True
        return x, y, z


def _read_gravity_optical(k4a, samples=5):
    """Average a few accelerometer samples into a GRAVITY (down) unit vector in
    the depth-camera optical frame (x right, y down, z forward), or None if the
    IMU yields nothing.

    A static accelerometer reports the +1g reaction force pointing UP, so the
    gravity (down) direction is the negated, normalized average — first rotated
    from the IMU's own axes into the depth frame (see `_accel_to_depth`). Called
    once at connect and then continuously while `set_imu` streaming is on, so the
    cloud reorients live as the camera is physically turned.
    """
    acc = [0.0, 0.0, 0.0]
    n = 0
    for _ in range(samples):
        try:
            sample = k4a.get_imu_sample()
        except Exception:
            break
        a = sample.get("acc_sample") if isinstance(sample, dict) else None
        if not a:
            continue
        acc[0] += a[0]; acc[1] += a[1]; acc[2] += a[2]
        n += 1
    if n == 0:
        return None
    ax, ay, az = _accel_to_depth(k4a, acc[0] / n, acc[1] / n, acc[2] / n)
    mag = math.sqrt(ax * ax + ay * ay + az * az)
    if mag < 1e-6:
        return None
    return (-ax / mag, -ay / mag, -az / mag)   # down = -reaction, normalized


def _read_intrinsics(k4a, align):
    """Intrinsics for the camera the streamed grid lives in: the COLOR camera in
    depth_to_color, else the DEPTH camera. Returns (fx, fy, cx, cy, dist8)."""
    ctype = (pyk4a.CalibrationType.COLOR if align == "depth_to_color"
             else pyk4a.CalibrationType.DEPTH)
    mat = k4a.calibration.get_camera_matrix(ctype)
    fx, fy, cx, cy = mat[0][0], mat[1][1], mat[0][2], mat[1][2]
    try:                                            # k1,k2,p1,p2,k3,k4,k5,k6
        dist = tuple(float(c) for c in
                     k4a.calibration.get_distortion_coefficients(ctype))
    except Exception:
        dist = (0.0,) * 8                           # fall back to pinhole
    return fx, fy, cx, cy, dist


def run(host, port, sensor_id, frames, min_depth, max_depth,
        sync="standalone", sub_delay_us=0, preview_stride=1, profile=False,
        depth_mode=None, color_resolution=None, fps=None, align=None):
    cfg = dict(camera_modes.DEFAULTS)
    if depth_mode:
        cfg["depth_mode"] = depth_mode
    if color_resolution:
        cfg["color_resolution"] = color_resolution
    if fps:
        cfg["fps"] = int(fps)
    if align:
        cfg["align"] = align

    k4a = PyK4A(_build_config(cfg, sync, sub_delay_us))
    k4a.start()
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s = max(1, preview_stride)

    # Live-tunable depth mask, adjustable via the control channel. Plain dict +
    # GIL = safe for these scalar reads/writes between the capture loop and the
    # control reader thread.
    rng = {"min": min_depth, "max": max_depth, "denoise": 2}
    bg = BackgroundSubtractor()
    # IMU orientation streaming, toggled live from the UI ("camera orientation").
    imu = {"stream": False}

    # Live camera reconfig (set_camera): the reader thread only *records* the
    # request under a lock; the capture loop performs the (re)start so pyk4a is
    # only ever touched from one thread.
    cfg_lock = threading.Lock()
    pending = {"reconfig": False, "restart": False}

    def on_command(cmd):
        c = cmd.get("cmd")
        if c == "set_denoise":
            rng["denoise"] = int(cmd.get("min_neighbors", 0))
            print("sensor %d: speckle filter -> min_neighbors=%d"
                  % (sensor_id, rng["denoise"]))
        elif c == "set_depth":
            if "min" in cmd:
                rng["min"] = int(cmd["min"])
            if "max" in cmd:
                rng["max"] = int(cmd["max"])
            print("sensor %d: depth mask -> [%d, %d] mm"
                  % (sensor_id, rng["min"], rng["max"]))
        elif c == "capture_bg":
            n = int(cmd.get("frames", 60))
            bg.start_capture(n)
            print("sensor %d: capturing background (%d frames)..." % (sensor_id, n))
        elif c == "clear_bg":
            bg.clear()
            print("sensor %d: background subtraction cleared" % sensor_id)
        elif c == "set_bg_margin":
            bg.margin = int(cmd.get("mm", bg.margin))
            print("sensor %d: bg margin -> %d mm" % (sensor_id, bg.margin))
        elif c == "set_imu":
            imu["stream"] = bool(cmd.get("enabled", False))
            print("sensor %d: imu streaming -> %s" % (sensor_id, imu["stream"]))
        elif c == "set_camera":
            with cfg_lock:
                changed = camera_modes.apply_camera_command(cfg, cmd)
                if changed.pop("restart", False):
                    pending["restart"] = True
                if changed:                         # any real field changed
                    pending["reconfig"] = True
                    print("sensor %d: set_camera %s -> mode=%s color=%s fps=%d "
                          "align=%s" % (
                              sensor_id, changed, cfg["depth_mode"],
                              cfg["color_resolution"],
                              camera_modes.clamp_fps(cfg["fps"], cfg["depth_mode"],
                                                     cfg["color_resolution"]),
                              cfg["align"]))

    control.start_reader(sock, on_command)

    # Read intrinsics for the active alignment; central keys them by sensor_id
    # (no manual calib files, scales to N cameras). (Re)sent before frames after
    # any reconfig via the calib_sent flag.
    align = cfg["align"]
    ifx, ify, icx, icy, idist = _read_intrinsics(k4a, align)
    calib_sent = False

    sent = 0
    t0 = time.time()
    win_t0 = t0                                 # windowed-fps marker
    acc = {"cap": 0.0, "depth": 0.0, "color": 0.0, "send": 0.0}  # profiling
    try:
        while frames <= 0 or sent < frames:
            # Apply any pending live camera reconfig before grabbing a frame, so
            # pyk4a start/stop happens only on this (capture) thread.
            with cfg_lock:
                do_reconfig = pending["reconfig"]
                do_restart = pending["restart"]
                pending["reconfig"] = False
                pending["restart"] = False
                cur = dict(cfg)
            if do_reconfig:
                if do_restart:
                    k4a.stop()
                    k4a = PyK4A(_build_config(cur, sync, sub_delay_us))
                    k4a.start()
                align = cur["align"]
                bg.clear()                          # plate is wrong-shaped now
                ifx, ify, icx, icy, idist = _read_intrinsics(k4a, align)
                calib_sent = False
                print("sensor %d: camera reconfigured (restart=%s) -> %s"
                      % (sensor_id, do_restart, cur))

            tc = time.time()
            cap = k4a.get_capture()
            # The streamed point grid is the depth image (color_to_depth) or the
            # depth warped into the color image (depth_to_color).
            if align == "depth_to_color":
                depth = cap.transformed_depth        # (Hc, Wc) uint16, mm
            else:
                depth = cap.depth                    # (Hd, Wd) uint16, mm
            if depth is None:
                continue
            if not calib_sent:                      # full-res grid dims + intrinsics
                sock.sendall(encode_calib(sensor_id, depth.shape[1],
                                          depth.shape[0], ifx, ify, icx, icy,
                                          idist))
                # Initial orientation: a gravity (down) vector from the IMU, sent
                # once per (re)connect/reconfig alongside the intrinsics so the
                # relay/viewer can level the cloud to the floor.
                g = _read_gravity_optical(k4a)
                if g is not None:
                    sock.sendall(encode_imu(sensor_id, g[0], g[1], g[2]))
                    print("sensor %d: gravity(optical) = (%.3f, %.3f, %.3f)"
                          % (sensor_id, g[0], g[1], g[2]))
                calib_sent = True
            if bg.capturing:                        # averaging the empty scene
                if bg.feed(depth):
                    print("sensor %d: background captured" % sensor_id)
            td = time.time()

            # Working-range mask, then (if a plate exists) keep only pixels
            # closer than the background — floor/walls at any distance drop out,
            # leaving just the subject.
            keep = (depth >= rng["min"]) & (depth <= rng["max"])
            fg = bg.foreground(depth)
            if fg is not None:
                keep &= fg
            keep = denoise_mask(keep, rng["denoise"])   # drop isolated ToF specks
            masked = np.where(keep, depth, 0).astype(np.uint16)
            # Preview downsample: stride on the node so RVL+color+wire all shrink
            # ~stride^2. The relay reverses it for metrically-correct unprojection
            # (frame.stride). Recording (later) keeps full res from `depth`.
            if s > 1:
                masked = masked[::s, ::s]
            h, w = masked.shape
            comp = rvl.compress(masked.ravel())
            tz = time.time()

            # Aligned color: the color image already in the SAME geometry as the
            # streamed depth grid (transformed_color for color_to_depth, the raw
            # color image for depth_to_color). Keep RGB for the foreground pixels
            # only, row-major, one triple per non-zero depth pixel (relay re-pairs
            # 1:1).
            color = b""
            color_aligned = False
            try:
                if align == "depth_to_color":
                    csrc = cap.color                 # (Hc, Wc, 4) BGRA
                else:
                    csrc = cap.transformed_color     # (Hd, Wd, 4) BGRA
            except Exception:
                csrc = None
            if csrc is not None:
                if s > 1:
                    csrc = csrc[::s, ::s]
                rgb = csrc[..., 2::-1]                # BGRA -> RGB
                color = np.ascontiguousarray(rgb[masked != 0]).tobytes()
                color_aligned = True
            tcol = time.time()

            frame = Frame(
                sensor_id=sensor_id, frame_id=sent,
                timestamp_ns=int(time.time() * 1e9), width=w, height=h,
                depth=comp, color=color, depth_rvl=True,
                color_aligned=color_aligned, stride=s,
            )
            sock.sendall(frame.encode())
            ts = time.time()

            acc["cap"] += td - tc; acc["depth"] += tz - td
            acc["color"] += tcol - tz; acc["send"] += ts - tcol
            sent += 1

            # Live orientation: while streaming is on, re-read the IMU and push a
            # fresh gravity vector so the viewer reorients as the camera turns.
            if imu["stream"] and sent % IMU_EVERY == 0:
                g = _read_gravity_optical(k4a, samples=3)
                if g is not None:
                    sock.sendall(encode_imu(sensor_id, g[0], g[1], g[2]))
            if sent % 30 == 0:
                now = time.time()
                fps_meas = 30.0 / (now - win_t0)     # windowed, not cumulative
                pts = int((masked != 0).sum())
                kb = (len(comp) + len(color)) / 1024.0
                msg = ("sensor %d: %d frames | %.1f fps | %d pts | %.0f KB/f"
                       % (sensor_id, sent, fps_meas, pts, kb))
                if profile:
                    msg += "  [cap %.0f depth %.0f color %.0f send %.0f ms/f]" % (
                        acc["cap"] / 30 * 1000, acc["depth"] / 30 * 1000,
                        acc["color"] / 30 * 1000, acc["send"] / 30 * 1000)
                print(msg)
                win_t0 = now
                for k in acc:
                    acc[k] = 0.0
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)   # wake the control reader + send FIN
        except OSError:
            pass
        sock.close()
        k4a.stop()
    print("sensor %d: streamed %d frames in %.1fs" % (sensor_id, sent, time.time() - t0))
    return sent


def main():
    ap = argparse.ArgumentParser(description="Azure Kinect capture node")
    ap.add_argument("--host", required=True, help="central recorder IP")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--sensor", type=int, default=0, help="sensor_id 0..N-1")
    ap.add_argument("--frames", type=int, default=60, help="0 = until Ctrl-C")
    ap.add_argument("--min-depth", type=int, default=500, help="mm")
    ap.add_argument("--max-depth", type=int, default=2500, help="mm")
    ap.add_argument("--sync", choices=["standalone", "master", "sub"],
                    default="standalone")
    ap.add_argument("--sub-delay-us", type=int, default=0,
                    help="subordinate delay off master (stagger IR; e.g. 160*index)")
    ap.add_argument("--preview-stride", type=int, default=1,
                    help="downsample the streamed cloud by this factor on the node "
                         "(2 = quarter the points; recommended for live preview)")
    ap.add_argument("--profile", action="store_true",
                    help="print per-stage timing (cap/depth/color/send)")
    ap.add_argument("--depth-mode", default="NFOV_UNBINNED",
                    choices=list(camera_modes.DEPTH_MODES),
                    help="initial depth FOV mode (live-changeable from the UI)")
    ap.add_argument("--color-resolution", default="720P",
                    choices=list(camera_modes.COLOR_RESOLUTIONS),
                    help="initial color resolution (live-changeable)")
    ap.add_argument("--camera-fps", type=int, default=30,
                    choices=list(camera_modes.FPS_CHOICES),
                    help="initial fps (auto-clamped per mode)")
    ap.add_argument("--align", default="color_to_depth",
                    choices=list(camera_modes.ALIGN_MODES),
                    help="initial alignment direction (live-changeable)")
    args = ap.parse_args()
    run(args.host, args.port, args.sensor, args.frames,
        args.min_depth, args.max_depth, args.sync, args.sub_delay_us,
        args.preview_stride, args.profile,
        depth_mode=args.depth_mode, color_resolution=args.color_resolution,
        fps=args.camera_fps, align=args.align)


if __name__ == "__main__":
    main()
