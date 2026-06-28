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
from protocol.frame import Frame, encode_calib, encode_imu, encode_extrinsic
from node import camera_modes

_IDENTITY_EXTRINSIC = ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
                       (0.0, 0.0, 0.0))
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
IMU_EVERY = 5


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


def _default_accel_to_depth(x, y, z):
    """Azure Kinect DK accelerometer -> depth-camera optical axis convention.

    The IMU is rotated ~90° about the depth camera's X axis from the depth
    frame: a level camera's gravity, left raw, lands on depth +Z (forward) and
    tips the floor up onto the far wall. The depth frame is X right, Y down,
    Z forward, so the accel axes map (x, y, z) -> (x, z, -y), which puts gravity
    back on +Y (down). Verified against real hardware. This captures the axis
    convention shared by all Azure Kinect DK units; the small per-device
    calibration rotation (a few degrees) is negligible for floor leveling and is
    only recovered with --imu-extrinsic.
    """
    return (x, z, -y)


def _sdk_accel_to_depth(k4a, x, y, z):
    """Factory ACCEL->DEPTH extrinsic via pyk4a (transform tip and origin and
    subtract → pure rotation, translation cancels). Returns None if pyk4a doesn't
    expose it on this build."""
    try:
        cal = k4a.calibration
        accel = pyk4a.CalibrationType.ACCEL
        depth = pyk4a.CalibrationType.DEPTH
        p1 = cal.convert_3d_to_3d((x, y, z), accel, depth)
        p0 = cal.convert_3d_to_3d((0.0, 0.0, 0.0), accel, depth)
        return (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    except Exception:
        return None


_AXIS_IDX = {"x": 0, "y": 1, "z": 2}


def parse_imu_axes(spec):
    """Parse a manual IMU->depth axis remap like "-y,-x,-z" into a function
    (x,y,z)->(x,y,z). For when the factory ACCEL->DEPTH extrinsic isn't available
    and the floor comes out wrong: pass the permutation/signs that put gravity on
    depth +Y when the camera is level. Returns None for an empty spec."""
    if not spec:
        return None
    parts = [p.strip().lower() for p in spec.split(",")]
    if len(parts) != 3:
        raise ValueError("--imu-axes needs 3 comma-separated terms, e.g. -y,-x,-z")
    plan = []
    for p in parts:
        sign = -1.0 if p.startswith("-") else 1.0
        plan.append((sign, _AXIS_IDX[p.lstrip("+-")]))
    return lambda x, y, z: tuple(s * (x, y, z)[i] for s, i in plan)


def _drain_accel(k4a, max_drain=4000):
    """Return the FRESHEST accelerometer sample (x,y,z) by draining the IMU FIFO.

    The Kinect streams IMU at ~1.6 kHz into a queue; if we read only a sample or
    two per call we keep consuming *stale* ones and the orientation lags badly.
    So take one (briefly blocking, in case the queue is momentarily empty) then
    pull the rest non-blocking and keep the last. Returns None if nothing.
    """
    last = None
    try:
        s = k4a.get_imu_sample()            # prime: brief block for one sample
        a = s.get("acc_sample") if isinstance(s, dict) else None
        if a:
            last = a
    except Exception:
        pass
    for _ in range(max_drain):              # drain the backlog to the newest
        try:
            s = k4a.get_imu_sample(0)       # non-blocking; raises when empty
        except Exception:
            break
        if not s:
            break
        a = s.get("acc_sample") if isinstance(s, dict) else None
        if a:
            last = a
    return last


def _read_gravity_optical(k4a, axes=None, use_extrinsic=False):
    """Freshest GRAVITY (down) unit vector in the depth optical frame (x right,
    y down, z forward), plus the raw accel (for logging). A static accelerometer
    reports the +1g reaction pointing UP, so gravity (down) is the negated,
    normalized reading — first mapped from the IMU's own axes into the depth
    frame. Mapping precedence: an explicit `--imu-axes` remap, else (opt-in) the
    factory ACCEL->DEPTH extrinsic, else the built-in Azure Kinect axis
    convention. Returns (gravity, raw_accel), either of which may be None.
    """
    a = _drain_accel(k4a)
    if a is None:
        return None, None
    if axes is not None:
        dx, dy, dz = axes(a[0], a[1], a[2])
    elif use_extrinsic:
        r = _sdk_accel_to_depth(k4a, a[0], a[1], a[2])
        if r is None:
            if not _IMU_EXTRINSIC_WARNED[0]:
                print("IMU: --imu-extrinsic requested but pyk4a exposes no "
                      "ACCEL->DEPTH on this build; using the default axis map")
                _IMU_EXTRINSIC_WARNED[0] = True
            dx, dy, dz = _default_accel_to_depth(a[0], a[1], a[2])
        else:
            dx, dy, dz = r
    else:
        dx, dy, dz = _default_accel_to_depth(a[0], a[1], a[2])
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag < 1e-6:
        return None, a
    return (-dx / mag, -dy / mag, -dz / mag), a


_EXT_WARNED = [False]


def _grid_to_depth_extrinsic(k4a, align):
    """The rigid transform taking a streamed-grid point into the DEPTH optical
    frame, so every alignment registers to one canonical frame. Identity for
    color_to_depth (the grid IS the depth image); the factory COLOR->DEPTH
    extrinsic for depth_to_color (the grid is the colour image, whose camera is
    tilted ~a few° about X + offset ~a few cm from depth — that's the tilt you
    see when switching alignment). Returns (R 9 row-major, t 3 metres).

    Built from convert_3d_to_3d on basis vectors (translation cancels for the
    columns, then the origin gives t). Falls back to identity if pyk4a doesn't
    expose the COLOR/DEPTH extrinsic.
    """
    if align != "depth_to_color":
        return _IDENTITY_EXTRINSIC
    try:
        cal = k4a.calibration
        color = pyk4a.CalibrationType.COLOR
        depth = pyk4a.CalibrationType.DEPTH
        o = cal.convert_3d_to_3d((0.0, 0.0, 0.0), color, depth)
        ex = cal.convert_3d_to_3d((1000.0, 0.0, 0.0), color, depth)
        ey = cal.convert_3d_to_3d((0.0, 1000.0, 0.0), color, depth)
        ez = cal.convert_3d_to_3d((0.0, 0.0, 1000.0), color, depth)
        c0 = [(ex[i] - o[i]) / 1000.0 for i in range(3)]   # R columns
        c1 = [(ey[i] - o[i]) / 1000.0 for i in range(3)]
        c2 = [(ez[i] - o[i]) / 1000.0 for i in range(3)]
        R = (c0[0], c1[0], c2[0],                           # row-major 3x3
             c0[1], c1[1], c2[1],
             c0[2], c1[2], c2[2])
        t = (o[0] / 1000.0, o[1] / 1000.0, o[2] / 1000.0)   # mm -> m
        return R, t
    except Exception as exc:
        if not _EXT_WARNED[0]:
            print("extrinsic: no COLOR->DEPTH from pyk4a (%s); depth_to_color "
                  "frames won't register to the depth frame" % exc)
            _EXT_WARNED[0] = True
        return _IDENTITY_EXTRINSIC


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


def run(host, port, sensor_id, frames,
        sync="standalone", sub_delay_us=0, preview_stride=1, profile=False,
        depth_mode=None, color_resolution=None, fps=None, align=None,
        imu_axes=None, imu_extrinsic=False):
    imu_axes_fn = parse_imu_axes(imu_axes)
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

    # We stream the full depth range — culling is background subtraction + the
    # speckle filter, not a near/far clip (the depth-mask UI/command was removed).
    # Plain dict + GIL = safe between the capture loop and the control reader.
    rng = {"denoise": 2}
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
                # grid->depth extrinsic so depth_to_color registers to the same
                # frame as color_to_depth (no tilt/shift when switching align).
                R, t = _grid_to_depth_extrinsic(k4a, align)
                sock.sendall(encode_extrinsic(sensor_id, R, t))
                # Initial orientation: a gravity (down) vector from the IMU, sent
                # once per (re)connect/reconfig alongside the intrinsics so the
                # relay/viewer can level the cloud to the floor. The raw accel is
                # logged so a wrong floor can be diagnosed (and fixed via
                # --imu-axes) without guessing.
                g, raw = _read_gravity_optical(k4a, imu_axes_fn, imu_extrinsic)
                if g is not None:
                    sock.sendall(encode_imu(sensor_id, g[0], g[1], g[2]))
                    print("sensor %d: accel raw=(%.2f, %.2f, %.2f) -> "
                          "gravity(optical)=(%.3f, %.3f, %.3f)"
                          % (sensor_id, raw[0], raw[1], raw[2], g[0], g[1], g[2]))
                calib_sent = True
            if bg.capturing:                        # averaging the empty scene
                if bg.feed(depth):
                    print("sensor %d: background captured" % sensor_id)
            td = time.time()

            # Keep all valid pixels, then (if a plate exists) keep only those
            # closer than the background — floor/walls at any distance drop out,
            # leaving just the subject. No near/far clip: the full range streams.
            keep = depth > 0
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

            # Live orientation: while streaming is on, re-read the IMU (freshest
            # sample, FIFO drained) and push a fresh gravity vector so the viewer
            # reorients as the camera turns. Logged occasionally for diagnostics.
            if imu["stream"] and sent % IMU_EVERY == 0:
                g, raw = _read_gravity_optical(k4a, imu_axes_fn, imu_extrinsic)
                if g is not None:
                    sock.sendall(encode_imu(sensor_id, g[0], g[1], g[2]))
                    if sent % 60 == 0:
                        print("sensor %d: gravity(optical)=(%.3f, %.3f, %.3f) "
                              "[accel raw=(%.2f, %.2f, %.2f)]"
                              % (sensor_id, g[0], g[1], g[2],
                                 raw[0], raw[1], raw[2]))
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
    ap.add_argument("--align", default="depth_to_color",
                    choices=list(camera_modes.ALIGN_MODES),
                    help="initial alignment direction (live-changeable)")
    ap.add_argument("--imu-axes", default=None,
                    help="override the IMU->depth axis map, e.g. 'x,z,-y' (the "
                         "default) or '-y,-x,-z'; use the logged 'accel raw' to "
                         "pick the permutation that puts gravity on depth +Y when "
                         "the camera is level")
    ap.add_argument("--imu-extrinsic", action="store_true",
                    help="use pyk4a's factory ACCEL->DEPTH extrinsic instead of "
                         "the built-in axis convention (falls back to it if the "
                         "build doesn't expose the extrinsic)")
    args = ap.parse_args()
    run(args.host, args.port, args.sensor, args.frames,
        args.sync, args.sub_delay_us,
        args.preview_stride, args.profile,
        depth_mode=args.depth_mode, color_resolution=args.color_resolution,
        fps=args.camera_fps, align=args.align, imu_axes=args.imu_axes,
        imu_extrinsic=args.imu_extrinsic)


if __name__ == "__main__":
    main()
