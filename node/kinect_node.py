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
from pyk4a import (
    PyK4A, Config, DepthMode, ColorResolution, FPS, ImageFormat, WiredSyncMode,
)

from protocol import rvl
from protocol.frame import Frame


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
        sync="standalone", sub_delay_us=0):
    k4a = PyK4A(_build_config(sync, sub_delay_us))
    k4a.start()
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    sent = 0
    t0 = time.time()
    try:
        while frames <= 0 or sent < frames:
            cap = k4a.get_capture()
            if cap.depth is None:
                continue
            depth = cap.depth                       # uint16 (H, W), millimetres
            h, w = depth.shape

            # Cheap working-range mask: keep [min,max] mm, zero everything else.
            # This isolates the subject from far walls/floor and gives RVL its
            # long zero-runs. Replace with per-view AI matting for clean edges.
            masked = np.where((depth >= min_depth) & (depth <= max_depth),
                              depth, 0).astype(np.uint16)
            # Pass the array straight to RVL — its NumPy fast path consumes it
            # directly (no per-pixel .tolist() conversion on the hot path).
            comp = rvl.compress(masked.ravel())

            # Depth-aligned color: warp the color image into the depth camera's
            # geometry (640x576), then keep RGB for the foreground pixels only,
            # in the SAME row-major order as the non-zero depth pixels. The relay
            # pairs them back up 1:1. (transformed_color needs BGRA, not MJPG.)
            color = b""
            color_aligned = False
            try:
                tcolor = cap.transformed_color           # (H, W, 4) BGRA or None
            except Exception:
                tcolor = None
            if tcolor is not None:
                valid = masked != 0
                rgb = tcolor[..., 2::-1]                  # BGRA -> RGB
                color = np.ascontiguousarray(rgb[valid]).tobytes()
                color_aligned = True

            frame = Frame(
                sensor_id=sensor_id, frame_id=sent,
                timestamp_ns=int(time.time() * 1e9), width=w, height=h,
                depth=comp, color=color, depth_rvl=True,
                color_aligned=color_aligned,
            )
            sock.sendall(frame.encode())
            sent += 1
            if sent % 30 == 0:
                fps = sent / (time.time() - t0)
                print("sensor %d: %d frames (%.1f fps)" % (sensor_id, sent, fps))
    finally:
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
    args = ap.parse_args()
    run(args.host, args.port, args.sensor, args.frames,
        args.min_depth, args.max_depth, args.sync, args.sub_delay_us)


if __name__ == "__main__":
    main()
