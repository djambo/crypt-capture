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

from protocol import rvl
from protocol.frame import Frame

DEFAULT_W, DEFAULT_H = 640, 576   # Azure Kinect NFOV unbinned depth resolution


def synth_depth(width, height, frame_id, sensor_id):
    """A moving elliptical blob of smooth valid depth on a zero background."""
    depth = array("H", bytes(2 * width * height))
    phase = frame_id * 0.1
    cx = width / 2 + math.sin(phase) * width * 0.12
    cy = height / 2 + math.cos(phase) * height * 0.06
    base = 1100 + sensor_id * 40  # each sensor sees the subject at a slight offset
    rng = random.Random(frame_id * 131 + sensor_id)
    rx, ry = width * 0.25, height * 0.34
    for y in range(height):
        for x in range(width):
            nx, ny = (x - cx) / rx, (y - cy) / ry
            r2 = nx * nx + ny * ny
            if r2 < 1.0:
                depth[y * width + x] = base + int(260 * math.sqrt(r2)) + rng.randint(0, 3)
    return depth


def run(host, port, sensor_id, frames, fps, width=DEFAULT_W, height=DEFAULT_H):
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    period = 1.0 / fps
    sent = 0
    try:
        for frame_id in range(frames):
            depth = synth_depth(width, height, frame_id, sensor_id)
            comp = rvl.compress(depth)
            # Placeholder color payload (real node: NVENC H.26x). Size-realistic stub.
            color = bytes((frame_id + sensor_id) % 256 for _ in range(2048))
            frame = Frame(
                sensor_id=sensor_id, frame_id=frame_id,
                timestamp_ns=int(time.time() * 1e9), width=width, height=height,
                depth=comp, color=color, depth_rvl=True,
            )
            sock.sendall(frame.encode())
            sent += 1
            time.sleep(period)
    finally:
        sock.close()
    return sent


def main():
    ap = argparse.ArgumentParser(description="Simulated crypt-capture node")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--sensor", type=int, default=0, help="sensor_id 0..N-1")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()
    n = run(args.host, args.port, args.sensor, args.frames, args.fps)
    print("sensor %d: streamed %d frames" % (args.sensor, n))


if __name__ == "__main__":
    main()
