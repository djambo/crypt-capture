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
from node.background import BackgroundSubtractor


def _build_config(sync, sub_delay_us):
    mode = {
        "standalone": WiredSyncMode.STANDALONE,
        "master": WiredSyncMode.MASTER,
        "sub": WiredSyncMode.SUBORDINATE,
    }[sync]
    return Config(
        color_resolution=ColorResolution.RES_720P,
        color_format=ImageFormat.COLOR_BGRA32,      # raw pixels: needed to warp
                                                    # color into the depth grid
        depth_mode=DepthMode.NFOV_UNBINNED,         # 640x576 depth grid
        camera_fps=FPS.FPS_30,
        synchronized_images_only=True,
        wired_sync_mode=mode,
        subordinate_delay_off_master_usec=sub_delay_us,
    )


def run(host, port, sensor_id, frames, min_depth, max_depth,
        sync="standalone", sub_delay_us=0, preview_stride=1, profile=False):
    k4a = PyK4A(_build_config(sync, sub_delay_us))
    k4a.start()
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s = max(1, preview_stride)

    # Live-tunable depth mask, adjustable via the control channel. Plain dict +
    # GIL = safe for these scalar reads/writes between the capture loop and the
    # control reader thread.
    rng = {"min": min_depth, "max": max_depth}
    bg = BackgroundSubtractor()

    def on_command(cmd):
        c = cmd.get("cmd")
        if c == "set_depth":
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

    control.start_reader(sock, on_command)

    # Read this camera's own depth intrinsics; central will key them by sensor_id
    # (no manual calib files, scales to N cameras). Sent once, before frames.
    mat = k4a.calibration.get_camera_matrix(pyk4a.CalibrationType.DEPTH)
    ifx, ify, icx, icy = mat[0][0], mat[1][1], mat[0][2], mat[1][2]
    calib_sent = False

    sent = 0
    t0 = time.time()
    win_t0 = t0                                 # windowed-fps marker
    acc = {"cap": 0.0, "depth": 0.0, "color": 0.0, "send": 0.0}  # profiling
    try:
        while frames <= 0 or sent < frames:
            tc = time.time()
            cap = k4a.get_capture()
            if cap.depth is None:
                continue
            depth = cap.depth                       # uint16 (H, W), millimetres
            if not calib_sent:                      # full-res dims for intrinsics
                sock.sendall(encode_calib(sensor_id, depth.shape[1],
                                          depth.shape[0], ifx, ify, icx, icy))
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
            masked = np.where(keep, depth, 0).astype(np.uint16)
            # Preview downsample: stride on the node so RVL+color+wire all shrink
            # ~stride^2. The relay reverses it for metrically-correct unprojection
            # (frame.stride). Recording (later) keeps full res from `depth`.
            if s > 1:
                masked = masked[::s, ::s]
            h, w = masked.shape
            comp = rvl.compress(masked.ravel())
            tz = time.time()

            # Depth-aligned color: warp the color image into the depth camera's
            # geometry, then keep RGB for the foreground pixels only, in the SAME
            # row-major order as the non-zero depth pixels (relay re-pairs 1:1).
            color = b""
            color_aligned = False
            try:
                tcolor = cap.transformed_color           # (H, W, 4) BGRA or None
            except Exception:
                tcolor = None
            if tcolor is not None:
                if s > 1:
                    tcolor = tcolor[::s, ::s]
                rgb = tcolor[..., 2::-1]                  # BGRA -> RGB
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
    args = ap.parse_args()
    run(args.host, args.port, args.sensor, args.frames,
        args.min_depth, args.max_depth, args.sync, args.sub_delay_us,
        args.preview_stride, args.profile)


if __name__ == "__main__":
    main()
