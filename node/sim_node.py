"""
Simulated capture node.

Stands in for a real Jetson/x86 node so the whole spine (protocol -> central
recorder -> web preview) can be built and tested WITHOUT any Azure Kinect
hardware. It synthesizes a masked depth frame (a moving human-ish blob on a
zero background) plus a placeholder color payload, RVL-compresses the depth,
and streams frames to the central recorder using the real wire protocol.

When real hardware lands, this module is replaced by a node that pulls frames
from the Azure Kinect SDK (pyk4a), applies per-view AI matting (RVM/BGMv2),
RVL-encodes the masked depth, and NVENC-encodes color — emitting the exact
same Frame messages, so nothing downstream changes.

Run standalone:
    python -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 30
"""

import argparse
import math
import random
import socket
import time
from array import array

from protocol import control, rvl
from protocol.frame import Frame

DEFAULT_W, DEFAULT_H = 640, 576   # Azure Kinect NFOV unbinned depth resolution


def synth_frame(width, height, frame_id, sensor_id, dmin=0, dmax=65535, stride=1):
    """A moving elliptical blob of smooth valid depth on a zero background, plus
    a depth-aligned RGB payload (one triple per valid pixel, row-major) so the
    color path is exercisable without hardware. Pixels outside [dmin,dmax] mm are
    masked out (mirrors the real node's live depth control). When stride>1 the
    grid is generated directly at the downsampled resolution; blob geometry still
    uses original pixel coords so the relay reconstructs the same cloud.

    Returns (depth_array, color_bytes, grid_w, grid_h)."""
    xs = list(range(0, width, stride))
    ys = list(range(0, height, stride))
    gw, gh = len(xs), len(ys)
    depth = array("H", bytes(2 * gw * gh))
    color = bytearray()
    phase = frame_id * 0.1
    cx = width / 2 + math.sin(phase) * width * 0.12
    cy = height / 2 + math.cos(phase) * height * 0.06
    base = 1100 + sensor_id * 40  # each sensor sees the subject at a slight offset
    jit = random.Random(frame_id * 131 + sensor_id)
    rx, ry = width * 0.25, height * 0.34
    for gy, y in enumerate(ys):
        for gx, x in enumerate(xs):
            nx, ny = (x - cx) / rx, (y - cy) / ry
            r2 = nx * nx + ny * ny
            if r2 < 1.0:
                z = base + int(260 * math.sqrt(r2)) + jit.randint(0, 3)
                if z < dmin or z > dmax:           # depth-range mask
                    continue
                depth[gy * gw + gx] = z
                # simple gradient so the cloud isn't a flat color
                color += bytes((int(255 * x / width),
                                int(255 * y / height),
                                int(255 * (1.0 - min(1.0, r2)))))
    return depth, bytes(color), gw, gh


def run(host, port, sensor_id, frames, fps, width=DEFAULT_W, height=DEFAULT_H,
        preview_stride=1):
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    period = 1.0 / fps
    s = max(1, preview_stride)

    rng = {"min": 0, "max": 65535}             # live-tunable via control channel

    def on_command(cmd):
        if cmd.get("cmd") == "set_depth":
            if "min" in cmd:
                rng["min"] = int(cmd["min"])
            if "max" in cmd:
                rng["max"] = int(cmd["max"])
            print("sensor %d: depth mask -> [%d, %d] mm"
                  % (sensor_id, rng["min"], rng["max"]))

    control.start_reader(sock, on_command)

    sent = 0
    try:
        while frames <= 0 or sent < frames:   # frames <= 0 => until Ctrl-C
            depth, color, gw, gh = synth_frame(width, height, sent, sensor_id,
                                               rng["min"], rng["max"], s)
            comp = rvl.compress(depth)
            frame = Frame(
                sensor_id=sensor_id, frame_id=sent,
                timestamp_ns=int(time.time() * 1e9), width=gw, height=gh,
                depth=comp, color=color, depth_rvl=True, color_aligned=True,
                stride=s,
            )
            sock.sendall(frame.encode())
            sent += 1
            time.sleep(period)
    finally:
        # shutdown (not just close) so the control-reader thread's recv wakes
        # and the peer gets a clean FIN even with another thread on the socket.
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
    return sent


def main():
    ap = argparse.ArgumentParser(description="Simulated crypt-capture node")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--sensor", type=int, default=0, help="sensor_id 0..N-1")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--preview-stride", type=int, default=1,
                    help="downsample the streamed cloud by this factor on the node")
    args = ap.parse_args()
    n = run(args.host, args.port, args.sensor, args.frames, args.fps,
            preview_stride=args.preview_stride)
    print("sensor %d: streamed %d frames" % (args.sensor, n))


if __name__ == "__main__":
    main()
