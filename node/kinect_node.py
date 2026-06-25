"""
Real Azure Kinect capture node — drop-in replacement for node/sim_node.py.

Captures depth + color from an Azure Kinect via pyk4a, applies a cheap depth
range-clip (zeros everything outside a working distance — fast background
removal that also makes RVL compress well; AI matting with RVM/BGMv2 is the
later upgrade), RVL-compresses the masked depth, and streams Frames to the
central recorder using the exact same wire protocol as the simulator.

Color is pulled as MJPG straight from the sensor (no re-encode needed for a
first bring-up; NVENC H.26x is the production upgrade).

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
import socket
import time

import numpy as np
import pyk4a
from pyk4a import (
    PyK4A, Config, DepthMode, ColorResolution, FPS, ImageFormat, WiredSyncMode,
)

from protocol import control, rvl
from protocol.frame import Frame, encode_calib
from node.background import BackgroundSubtractor, denoise_mask
from node import camera_modes

# Map the catalog's string names (node/camera_modes.py) onto pyk4a enums. Keeping
# the strings in a pyk4a-free module is what lets the control model be unit-tested
# without hardware; this is the only place the two worlds meet.
_DEPTH_ENUM = {
    "NFOV_UNBINNED": DepthMode.NFOV_UNBINNED,
    "NFOV_2X2BINNED": DepthMode.NFOV_2X2BINNED,
    "WFOV_UNBINNED": DepthMode.WFOV_UNBINNED,
    "WFOV_2X2BINNED": DepthMode.WFOV_2X2BINNED,
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


def _build_config(cfg, sync, sub_delay_us):
    """Build a pyk4a Config from a resolved camera_modes config dict."""
    mode = {
        "standalone": WiredSyncMode.STANDALONE,
        "master": WiredSyncMode.MASTER,
        "sub": WiredSyncMode.SUBORDINATE,
    }[sync]
    return Config(
        color_resolution=_COLOR_ENUM[cfg["color_resolution"]],
        color_format=ImageFormat.COLOR_BGRA32,      # raw pixels: needed to warp
                                                    # color into the depth grid
        depth_mode=_DEPTH_ENUM[cfg["depth_mode"]],
        camera_fps=_FPS_ENUM[cfg["fps"]],
        synchronized_images_only=True,
        wired_sync_mode=mode,
        subordinate_delay_off_master_usec=sub_delay_us,
    )


def _read_intrinsics(k4a, geometry):
    """Intrinsics for the camera the point cloud is built on: the COLOR camera in
    color geometry (depth warped into the color grid), else the DEPTH camera.
    Returns (fx, fy, cx, cy, dist[8])."""
    ctype = (pyk4a.CalibrationType.COLOR if geometry == "color"
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
        depth_mode="NFOV_UNBINNED", color_resolution="720P", fps=30,
        geometry="depth"):
    cfg, _, notes = camera_modes.resolve(
        camera_modes.DEFAULT_CONFIG,
        {"depth_mode": depth_mode, "color_resolution": color_resolution,
         "fps": fps, "geometry": geometry})
    for n in notes:
        print("sensor %d: %s" % (sensor_id, n))
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
    # Camera-reconfig requests land here; the capture loop applies them (all k4a
    # calls stay on one thread). `cfg` holds the live config.
    state = {"cfg": cfg, "pending": None}

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
        elif c == "set_camera":
            req = {k: cmd[k] for k in
                   ("depth_mode", "color_resolution", "fps", "geometry")
                   if k in cmd}
            try:
                new_cfg, _, ns = camera_modes.resolve(state["cfg"], req)
            except ValueError as exc:
                print("sensor %d: bad set_camera (%s)" % (sensor_id, exc))
                return
            for n in ns:
                print("sensor %d: %s" % (sensor_id, n))
            state["pending"] = new_cfg          # capture loop performs the switch
            print("sensor %d: camera reconfig queued -> %s" % (sensor_id, new_cfg))

    control.start_reader(sock, on_command)

    geom = state["cfg"]["geometry"]
    # Read this camera's own intrinsics (for the active geometry); central keys
    # them by sensor_id (no manual calib files, scales to N cameras). Re-read +
    # re-sent (calib_sent=False) whenever the geometry or sensor mode changes.
    intr = _read_intrinsics(k4a, geom)
    calib_sent = False

    sent = 0
    t0 = time.time()
    win_t0 = t0                                 # windowed-fps marker
    acc = {"cap": 0.0, "depth": 0.0, "color": 0.0, "send": 0.0}  # profiling
    try:
        while frames <= 0 or sent < frames:
            # --- apply a queued camera reconfig (control thread requested it) ---
            pending = state["pending"]
            if pending is not None:
                state["pending"] = None
                old = state["cfg"]
                restart = any(pending[k] != old[k] for k in
                              ("depth_mode", "color_resolution", "fps"))
                if restart:
                    try:
                        k4a.stop()
                        k4a = PyK4A(_build_config(pending, sync, sub_delay_us))
                        k4a.start()
                    except Exception as exc:    # revert to the last good config
                        print("sensor %d: reconfig failed (%s); reverting"
                              % (sensor_id, exc))
                        k4a = PyK4A(_build_config(old, sync, sub_delay_us))
                        k4a.start()
                        pending = old
                state["cfg"] = pending
                geom = pending["geometry"]
                bg.clear()                      # plate dims/geometry now stale
                intr = _read_intrinsics(k4a, geom)
                calib_sent = False              # re-handshake new intrinsics/dims
                print("sensor %d: camera now %s" % (sensor_id, pending))

            tc = time.time()
            cap = k4a.get_capture()
            if cap.depth is None:
                continue
            # Pick the depth grid + matching color image for the active geometry.
            # color geometry: depth warped into the (high-res) color grid -> one
            #   point per *color* pixel, full color detail. depth geometry: color
            #   warped into the depth grid -> one point per depth pixel.
            if geom == "color":
                try:
                    grid = cap.transformed_depth     # (Hc, Wc) depth in color geom
                except Exception:
                    grid = None
                color_img = cap.color                # (Hc, Wc, 4) BGRA, same grid
            else:
                grid = cap.depth                     # (Hd, Wd), millimetres
                try:
                    color_img = cap.transformed_color  # (Hd, Wd, 4) BGRA
                except Exception:
                    color_img = None
            if grid is None:
                continue
            if not calib_sent:                      # full-res dims for intrinsics
                sock.sendall(encode_calib(sensor_id, grid.shape[1],
                                          grid.shape[0], intr[0], intr[1],
                                          intr[2], intr[3], intr[4]))
                calib_sent = True
            if bg.capturing:                        # averaging the empty scene
                if bg.feed(grid):
                    print("sensor %d: background captured" % sensor_id)
            td = time.time()

            # Working-range mask, then (if a plate exists) keep only pixels
            # closer than the background — floor/walls at any distance drop out,
            # leaving just the subject.
            keep = (grid >= rng["min"]) & (grid <= rng["max"])
            fg = bg.foreground(grid)
            if fg is not None:
                keep &= fg
            keep = denoise_mask(keep, rng["denoise"])   # drop isolated ToF specks
            masked = np.where(keep, grid, 0).astype(np.uint16)
            # Preview downsample: stride on the node so RVL+color+wire all shrink
            # ~stride^2. The relay reverses it for metrically-correct unprojection
            # (frame.stride). Recording (later) keeps full res from `grid`.
            if s > 1:
                masked = masked[::s, ::s]
            h, w = masked.shape
            comp = rvl.compress(masked.ravel())
            tz = time.time()

            # Aligned color: the color image already shares the grid's geometry,
            # so keep RGB for the foreground pixels only, in the SAME row-major
            # order as the non-zero depth pixels (the relay re-pairs 1:1).
            color = b""
            color_aligned = False
            if color_img is not None:
                if s > 1:
                    color_img = color_img[::s, ::s]
                rgb = color_img[..., 2::-1]              # BGRA -> RGB
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
            if sent % 30 == 0:
                now = time.time()
                fps = 30.0 / (now - win_t0)          # windowed, not cumulative
                pts = int((masked != 0).sum())
                kb = (len(comp) + len(color)) / 1024.0
                msg = ("sensor %d: %d frames | %.1f fps | %d pts | %.0f KB/f"
                       % (sensor_id, sent, fps, pts, kb))
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
                    choices=sorted(camera_modes.DEPTH_MODES),
                    help="Azure Kinect depth/FOV mode (live-tunable via set_camera)")
    ap.add_argument("--color-resolution", default="720P",
                    choices=sorted(camera_modes.COLOR_RESOLUTIONS),
                    help="RGB sensor resolution (more color detail at higher res)")
    ap.add_argument("--fps", type=int, default=30, choices=camera_modes.FPS_OPTIONS,
                    help="camera frame rate (auto-clamped for WFOV_UNBINNED/3072P)")
    ap.add_argument("--geometry", default="depth", choices=camera_modes.GEOMETRIES,
                    help="point-cloud grid: 'depth' (1 pt/depth px) or 'color' "
                         "(1 pt/color px = denser, full-res color cloud)")
    args = ap.parse_args()
    run(args.host, args.port, args.sensor, args.frames,
        args.min_depth, args.max_depth, args.sync, args.sub_delay_us,
        args.preview_stride, args.profile,
        args.depth_mode, args.color_resolution, args.fps, args.geometry)


if __name__ == "__main__":
    main()
