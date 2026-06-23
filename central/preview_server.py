"""
Central live-preview server (M2).

Bridges the capture side to the browser:

    node(s) ──TCP Frame stream──►  preview_server  ──WebSocket CPV1──►  browser

For each incoming frame it RVL-decodes the depth, unprojects the valid pixels to
a metric point cloud (camera intrinsics), downsamples by a pixel stride, and
broadcasts a compact binary `CPV1` message (see `docs/preview_protocol.md`) to
every connected browser. Geometry only in v0 (no color yet).

This is the *preview* path — lossy/downsampled and best-effort. Full-fidelity
recording is a separate mode (local-on-node, downloaded after); see
`docs/realtime_architecture.md`.

Run:
    python3 -m central.preview_server                       # nodes:9000 ws:8080
    python3 -m central.preview_server --calib takes/real1/calib.json --stride 2

Then point a node at it (simulated, no hardware needed):
    python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 300
And verify the stream with the headless client:
    python3 -m scripts.preview_client --frames 30
"""

import argparse
import json
import math
import socket
import struct
import threading

import numpy as np

from protocol import rvl, websocket
from protocol.frame import read_frame

PREVIEW_MAGIC = b"CPV1"
_PREVIEW_HEADER = struct.Struct("<4sIIII")   # magic, flags, sensor, frame, count
FLAG_POSITIONS = 0x1
FLAG_RGB = 0x2


def default_intrinsics(w, h, hfov_deg=75.0):
    """Rough pinhole intrinsics from horizontal FOV (Azure Kinect NFOV ≈ 75°).

    Good enough to eyeball a live cloud; pass a real --calib for metric accuracy.
    """
    fx = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return fx, fx, w / 2.0, h / 2.0


def load_intrinsics(calib_path, w, h):
    if calib_path:
        c = json.load(open(calib_path))
        return c["fx"], c["fy"], c["cx"], c["cy"]
    return default_intrinsics(w, h)


def unproject(depth_u16, w, h, fx, fy, cx, cy, stride, color_grid=None):
    """Depth grid -> (xyz, rgb) for the valid (non-zero) pixels.

    xyz is (N,3) float32. rgb is (N,3) uint8 if a color_grid is supplied (the
    per-pixel color image aligned to the depth grid), else None.
    """
    d = np.frombuffer(depth_u16, dtype=np.uint16).reshape(h, w)
    us = np.arange(0, w, stride)
    vs = np.arange(0, h, stride)
    sub = d[vs][:, us]
    uu, vv = np.meshgrid(us.astype(np.float32), vs.astype(np.float32))
    m = sub != 0
    if not m.any():
        return np.empty((0, 3), dtype=np.float32), (
            np.empty((0, 3), dtype=np.uint8) if color_grid is not None else None)
    z = sub[m].astype(np.float32) / 1000.0          # mm -> m
    x = (uu[m] - cx) * z / fx
    y = -((vv[m] - cy) * z / fy)                     # +Y up
    xyz = np.column_stack((x, y, -z)).astype(np.float32)   # camera looks down -Z
    rgb = None
    if color_grid is not None:
        rgb = color_grid[vs][:, us][m]
    return xyz, rgb


def aligned_color_grid(color_bytes, depth_u16, w, h):
    """Scatter the foreground RGB payload back onto a full (h,w,3) color grid.

    The node sent one RGB triple per non-zero depth pixel in row-major order, so
    the same valid mask (from the decoded depth) places each color correctly.
    Returns None if the payload count doesn't match the valid-pixel count.
    """
    d = np.frombuffer(depth_u16, dtype=np.uint16).reshape(h, w)
    valid = d != 0
    colors = np.frombuffer(color_bytes, dtype=np.uint8)
    if colors.size != int(valid.sum()) * 3:
        return None
    grid = np.zeros((h, w, 3), dtype=np.uint8)
    grid[valid] = colors.reshape(-1, 3)
    return grid


def build_message(sensor_id, frame_id, xyz, rgb=None):
    count = int(xyz.shape[0])
    flags = FLAG_POSITIONS | (FLAG_RGB if rgb is not None else 0)
    header = _PREVIEW_HEADER.pack(
        PREVIEW_MAGIC, flags, sensor_id & 0xFFFFFFFF,
        frame_id & 0xFFFFFFFF, count)
    payload = header + np.ascontiguousarray(xyz, dtype="<f4").tobytes()
    if rgb is not None:
        payload += np.ascontiguousarray(rgb, dtype=np.uint8).tobytes()
    return payload


class PreviewServer:
    def __init__(self, calib=None, stride=2, max_points=200000):
        self.calib = calib
        self.stride = stride
        self.max_points = max_points
        self._clients = []                  # list[socket]
        self._lock = threading.Lock()
        self._intr = {}                     # (w,h) -> intrinsics cache
        self.frames_relayed = 0

    # --- browser (WebSocket) side ---------------------------------------
    def _ws_accept_loop(self, host, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(8)
        print("[preview] browser WebSocket on ws://%s:%d/" % (host, port))
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not websocket.server_handshake(conn):
                conn.close()
                continue
            with self._lock:
                self._clients.append(conn)
            print("[preview] viewer connected %s (%d total)"
                  % (addr[0], len(self._clients)))
            threading.Thread(target=self._ws_reader, args=(conn,),
                             daemon=True).start()

    def _ws_reader(self, conn):
        """Drain client frames so we notice disconnects/close promptly."""
        try:
            while True:
                msg = websocket.read_frame(conn)
                if msg is None or msg[0] == websocket.OP_CLOSE:
                    break
        finally:
            self._drop(conn)

    def _drop(self, conn):
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    def _broadcast(self, payload):
        frame = websocket.encode_frame(payload, opcode=websocket.OP_BINARY)
        with self._lock:
            clients = list(self._clients)
        for conn in clients:
            try:
                conn.sendall(frame)
            except OSError:
                self._drop(conn)

    # --- node (TCP Frame) side ------------------------------------------
    def _intrinsics(self, w, h):
        key = (w, h)
        if key not in self._intr:
            self._intr[key] = load_intrinsics(self.calib, w, h)
        return self._intr[key]

    def _serve_node(self, conn, addr):
        print("[preview] node connected %s" % (addr[0],))
        try:
            while True:
                frame = read_frame(conn)
                if frame is None:
                    break
                if not frame.depth_rvl:
                    continue
                depth = rvl.decompress(frame.depth, frame.width * frame.height)
                fx, fy, cx, cy = self._intrinsics(frame.width, frame.height)
                cgrid = None
                if frame.color_aligned and frame.color:
                    cgrid = aligned_color_grid(frame.color, depth,
                                               frame.width, frame.height)
                xyz, rgb = unproject(depth, frame.width, frame.height,
                                     fx, fy, cx, cy, self.stride, cgrid)
                if xyz.shape[0] > self.max_points:
                    idx = np.linspace(0, xyz.shape[0] - 1, self.max_points).astype(int)
                    xyz = xyz[idx]
                    if rgb is not None:
                        rgb = rgb[idx]
                self._broadcast(build_message(frame.sensor_id, frame.frame_id,
                                              xyz, rgb))
                self.frames_relayed += 1
        finally:
            conn.close()
            print("[preview] node disconnected %s" % (addr[0],))

    def _node_accept_loop(self, host, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(8)
        print("[preview] node ingest on %s:%d" % (host, port))
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._serve_node, args=(conn, addr),
                             daemon=True).start()

    def run(self, node_host, node_port, ws_host, ws_port):
        threading.Thread(target=self._ws_accept_loop, args=(ws_host, ws_port),
                         daemon=True).start()
        self._node_accept_loop(node_host, node_port)   # blocks


def main():
    ap = argparse.ArgumentParser(description="crypt-capture live preview relay")
    ap.add_argument("--node-host", default="0.0.0.0")
    ap.add_argument("--node-port", type=int, default=9000)
    ap.add_argument("--ws-host", default="0.0.0.0")
    ap.add_argument("--ws-port", type=int, default=8080)
    ap.add_argument("--calib", help="calib.json (fx,fy,cx,cy); else FOV estimate")
    ap.add_argument("--stride", type=int, default=2,
                    help="pixel stride for downsampling the cloud (1 = full res)")
    ap.add_argument("--max-points", type=int, default=200000)
    args = ap.parse_args()
    server = PreviewServer(calib=args.calib, stride=args.stride,
                           max_points=args.max_points)
    try:
        server.run(args.node_host, args.node_port, args.ws_host, args.ws_port)
    except KeyboardInterrupt:
        print("\n[preview] stopped (%d frames relayed)" % server.frames_relayed)


if __name__ == "__main__":
    main()
