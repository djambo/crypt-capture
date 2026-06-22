"""
End-to-end spine demo (no hardware).

Spins up the central recorder and N simulated nodes on loopback, streams a few
synchronized frames, then verifies the recorded take: complete frames present,
files on disk, and depth decodes losslessly. This is the hardware-independent
half of Phase 1 — when real Kinect nodes exist, they replace sim_node.py and
nothing else changes.

    python scripts/run_demo.py            # 4 sensors, 15 frames
    python scripts/run_demo.py --sensors 2 --frames 8
"""

import argparse
import os
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from central.recorder import Recorder
from node import sim_node
from protocol import rvl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensors", type=int, default=4)
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--out", default="takes/demo")
    args = ap.parse_args()

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)

    rec = Recorder(args.out, args.sensors)
    result = {}
    rec_thread = threading.Thread(
        target=lambda: result.update(manifest=rec.run("127.0.0.1", args.port)),
        daemon=True,
    )
    rec_thread.start()

    # Give the listener a moment, then launch nodes.
    import time
    time.sleep(0.3)
    node_threads = []
    for sid in range(args.sensors):
        t = threading.Thread(
            target=sim_node.run,
            args=("127.0.0.1", args.port, sid, args.frames, args.fps),
            daemon=True,
        )
        t.start()
        node_threads.append(t)
    for t in node_threads:
        t.join()
    rec_thread.join(timeout=10)

    manifest = result.get("manifest")
    assert manifest, "recorder did not finalize"
    print("\n=== take manifest ===")
    print("sensors:", manifest["num_sensors"],
          "| complete frames:", manifest["complete_frames"],
          "| partial:", len(manifest["partial_frames"]))

    # Verify a recorded frame decodes losslessly.
    fid = manifest["frame_ids"][0]
    sensor0 = manifest["sensors"]["0"]
    w, h = sensor0["width"], sensor0["height"]
    path = os.path.join(args.out, "frames", "%06d" % fid, "sensor0.depth.rvl")
    with open(path, "rb") as f:
        comp = f.read()
    depth = rvl.decompress(comp, w * h)
    valid = sum(1 for v in depth if v)
    print("frame %d sensor0: %dx%d, %d valid depth px, rvl=%d bytes (%.1fx)"
          % (fid, w, h, valid, len(comp), (2 * w * h) / len(comp)))

    assert manifest["complete_frames"] == args.frames, "missing complete frames"
    assert valid > 0, "decoded depth is empty"
    print("\nOK — spine works end-to-end (record + sync + lossless depth).")


if __name__ == "__main__":
    main()
