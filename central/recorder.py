"""
Central recorder.

Accepts a TCP connection from each capture node, reads the synchronized frame
stream, groups frames across sensors by their hardware-synced `frame_id`, and
writes a recording ("take") to disk. The depth payloads are stored as-is (RVL)
— no decode on the hot path — so recording is cheap; fusion/meshing happens
later, offline, from the recorded take.

Take layout on disk:
    <take_dir>/
        manifest.json                       # sensors, resolution, fps, frame index
        frames/<frame_id:06d>/sensorN.depth.rvl
        frames/<frame_id:06d>/sensorN.color.bin

This module deliberately knows nothing about Kinect/Jetson — it only speaks the
wire protocol, so simulated and real nodes are interchangeable.

Run standalone:
    python -m central.recorder --port 9000 --sensors 4 --out takes/mytake
"""

import argparse
import json
import os
import socket
import threading
import time
from collections import defaultdict

from protocol.frame import read_frame


class Recorder:
    def __init__(self, take_dir, num_sensors):
        self.take_dir = take_dir
        self.num_sensors = num_sensors
        self.frames_dir = os.path.join(take_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self._lock = threading.Lock()
        # frame_id -> set(sensor_id) seen
        self.seen = defaultdict(set)
        self.meta = {}        # per-sensor: width/height/first ts
        self.frame_count = 0

    def _handle_frame(self, frame):
        fdir = os.path.join(self.frames_dir, "%06d" % frame.frame_id)
        os.makedirs(fdir, exist_ok=True)
        with open(os.path.join(fdir, "sensor%d.depth.rvl" % frame.sensor_id), "wb") as f:
            f.write(frame.depth)
        with open(os.path.join(fdir, "sensor%d.color.bin" % frame.sensor_id), "wb") as f:
            f.write(frame.color)
        with self._lock:
            self.seen[frame.frame_id].add(frame.sensor_id)
            self.meta.setdefault(frame.sensor_id, {
                "width": frame.width, "height": frame.height,
                "depth_rvl": frame.depth_rvl,
            })

    def _serve_conn(self, conn):
        try:
            while True:
                frame = read_frame(conn)
                if frame is None:
                    break
                self._handle_frame(frame)
        finally:
            conn.close()

    def run(self, host, port, timeout=30.0):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(self.num_sensors)
        srv.settimeout(timeout)

        threads = []
        for _ in range(self.num_sensors):
            conn, _addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=self._serve_conn, args=(conn,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        srv.close()
        return self.finalize()

    def finalize(self):
        # A frame is "complete" only if every expected sensor delivered it.
        complete = sorted(fid for fid, s in self.seen.items()
                          if len(s) == self.num_sensors)
        self.frame_count = len(complete)
        manifest = {
            "format": "crypt-capture/take@1",
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "num_sensors": self.num_sensors,
            "sensors": {str(k): v for k, v in sorted(self.meta.items())},
            "frame_ids": complete,
            "complete_frames": len(complete),
            "partial_frames": sorted(set(self.seen) - set(complete)),
            "calibration": None,   # filled by the calibration step (extrinsics)
            "notes": "depth payloads are RVL-compressed u16; decode with protocol.rvl",
        }
        with open(os.path.join(self.take_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        return manifest


def main():
    ap = argparse.ArgumentParser(description="crypt-capture central recorder")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--sensors", type=int, required=True, help="number of nodes")
    ap.add_argument("--out", required=True, help="take output directory")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()
    rec = Recorder(args.out, args.sensors)
    manifest = rec.run(args.host, args.port, args.timeout)
    print("recorded %d complete frames from %d sensors -> %s"
          % (manifest["complete_frames"], args.sensors, args.out))


if __name__ == "__main__":
    main()
