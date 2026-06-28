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
import time

import numpy as np

from protocol import control, discovery, rvl, websocket
from protocol.frame import read_message

# Browser→node commands the relay will forward (everything else is ignored).
_FORWARDED_COMMANDS = ("set_depth", "capture_bg", "clear_bg", "set_bg_margin",
                       "set_denoise", "set_camera", "set_imu")

PREVIEW_MAGIC = b"CPV1"
_PREVIEW_HEADER = struct.Struct("<4sIIII")   # magic, flags, sensor, frame, count
FLAG_POSITIONS = 0x1
FLAG_RGB = 0x2
FLAG_GRAVITY = 0x4                            # trailing 3×float32 gravity (down) vec


def gravity_to_view(g_optical):
    """Re-express a gravity (down) unit vector from the depth OPTICAL frame
    (x right, y down, z forward) into the cloud/view frame the viewer renders in
    (x right, y up, z toward viewer). The unprojector negates Y and Z to build
    the cloud, so gravity transforms the same way. Returns a normalized tuple, or
    None if degenerate."""
    gx, gy, gz = g_optical
    vx, vy, vz = gx, -gy, -gz
    n = math.sqrt(vx * vx + vy * vy + vz * vz)
    if n < 1e-9:
        return None
    return (vx / n, vy / n, vz / n)


def default_intrinsics(w, h, hfov_deg=75.0):
    """Rough pinhole intrinsics from horizontal FOV (Azure Kinect NFOV ≈ 75°).

    Good enough to eyeball a live cloud; pass a real --calib for metric accuracy.
    """
    fx = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return fx, fx, w / 2.0, h / 2.0


def load_intrinsics(calib_path, w, h):
    if calib_path:
        c = json.load(open(calib_path))
        return (c["fx"], c["fy"], c["cx"], c["cy"],
                tuple(c.get("dist", (0.0,) * 8)))
    fx, fy, cx, cy = default_intrinsics(w, h)
    return fx, fy, cx, cy, (0.0,) * 8


def compute_ray_table(w, h, fx, fy, cx, cy, dist, iters=8):
    """Per-pixel viewing rays (X/Z, Y/Z) for a full-res grid, applying the
    Brown-Conrady distortion model via iterative undistortion. Without this, the
    Kinect's wide-FOV lens distortion bows flat surfaces into cones. With dist
    all-zero it reduces exactly to the pinhole (u-cx)/fx."""
    k1, k2, p1, p2, k3, k4, k5, k6 = (list(dist) + [0.0] * 8)[:8]
    uu, vv = np.meshgrid(np.arange(w, dtype=np.float64),
                         np.arange(h, dtype=np.float64))
    xd = (uu - cx) / fx          # distorted (observed) normalized coords
    yd = (vv - cy) / fy
    x, y = xd.copy(), yd.copy()
    for _ in range(iters):       # invert the distortion to recover true rays
        r2 = x * x + y * y
        radial = (1 + r2 * (k1 + r2 * (k2 + r2 * k3))) / \
                 (1 + r2 * (k4 + r2 * (k5 + r2 * k6)))
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x = (xd - dx) / radial
        y = (yd - dy) / radial
    return x.astype(np.float32), y.astype(np.float32)


def unproject(depth_u16, w, h, ray_x, ray_y, stride, node_stride=1,
              color_grid=None, extrinsic=None):
    """Depth grid -> (xyz, rgb) for the valid (non-zero) pixels, using the
    distortion-aware full-res ray table. The grid may be node-downsampled
    (`node_stride`): grid pixel (u,v) maps to ray_table[v*node_stride, u*node_stride].
    `stride` is an additional relay-side downsample.

    `extrinsic` (R 3x3, t 3) optionally rigid-transforms the points from the
    streamed grid's camera frame into the canonical DEPTH frame (P_depth = R·P+t),
    applied in optical space BEFORE the optical->view flip. This registers
    depth_to_color frames to the same frame as color_to_depth so switching
    alignment doesn't tilt/shift the cloud. None = no transform (identity).
    """
    d = np.frombuffer(depth_u16, dtype=np.uint16).reshape(h, w)
    us = np.arange(0, w, stride)
    vs = np.arange(0, h, stride)
    sub = d[vs][:, us]
    m = sub != 0
    if not m.any():
        return np.empty((0, 3), dtype=np.float32), (
            np.empty((0, 3), dtype=np.uint8) if color_grid is not None else None)
    rx = ray_x[np.ix_(vs * node_stride, us * node_stride)]
    ry = ray_y[np.ix_(vs * node_stride, us * node_stride)]
    z = sub[m].astype(np.float32) / 1000.0          # mm -> m
    xo = rx[m] * z                                   # optical: x right
    yo = ry[m] * z                                   #          y down
    zo = z                                           #          z forward
    if extrinsic is not None:
        R, t = extrinsic
        opt = np.column_stack((xo, yo, zo)) @ R.T + t   # P_depth = R·P + t
        xo, yo, zo = opt[:, 0], opt[:, 1], opt[:, 2]
    xyz = np.column_stack((xo, -yo, -zo)).astype(np.float32)  # optical->view flip
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


def build_message(sensor_id, frame_id, xyz, rgb=None, gravity=None):
    count = int(xyz.shape[0])
    flags = (FLAG_POSITIONS
             | (FLAG_RGB if rgb is not None else 0)
             | (FLAG_GRAVITY if gravity is not None else 0))
    header = _PREVIEW_HEADER.pack(
        PREVIEW_MAGIC, flags, sensor_id & 0xFFFFFFFF,
        frame_id & 0xFFFFFFFF, count)
    payload = header + np.ascontiguousarray(xyz, dtype="<f4").tobytes()
    if rgb is not None:
        payload += np.ascontiguousarray(rgb, dtype=np.uint8).tobytes()
    if gravity is not None:                  # trailing 12-byte block (after rgb)
        payload += np.asarray(gravity, dtype="<f4").tobytes()
    return payload


class PreviewServer:
    def __init__(self, calib=None, stride=2, max_points=200000):
        self.calib = calib
        self.stride = stride
        self.max_points = max_points
        self._clients = []                  # browser WebSocket sockets
        self._nodes = []                    # connected node TCP sockets
        self._lock = threading.Lock()
        self._intr = {}                     # (w,h) -> fallback intrinsics+dist
        self._sensor_intr = {}              # sensor_id -> (fx,fy,cx,cy,dist)
        self._ray = {}                      # sensor_id -> (w,h,ray_x,ray_y)
        self._sensor_gravity = {}           # sensor_id -> (gx,gy,gz) view-frame
        self._sensor_extrinsic = {}         # sensor_id -> (R 3x3, t 3) or None
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
        """Read browser messages: text = a JSON command to forward to nodes;
        also lets us notice disconnects/close promptly."""
        try:
            while True:
                msg = websocket.read_frame(conn)
                if msg is None or msg[0] == websocket.OP_CLOSE:
                    break
                opcode, payload = msg
                if opcode == websocket.OP_TEXT and payload:
                    try:
                        cmd = json.loads(payload.decode("utf-8"))
                    except ValueError:
                        continue
                    self._on_browser_command(cmd)
        finally:
            self._drop(conn)

    def _on_browser_command(self, cmd):
        if isinstance(cmd, dict) and cmd.get("cmd") in _FORWARDED_COMMANDS:
            n = self.send_to_nodes(cmd)
            print("[preview] forwarded %s to %d node(s)" % (cmd, n))

    def send_to_nodes(self, cmd):
        """Send a control command to every connected node. Returns count sent."""
        data = control.encode(cmd)
        with self._lock:
            nodes = list(self._nodes)
        sent = 0
        for conn in nodes:
            try:
                conn.sendall(data)
                sent += 1
            except OSError:
                pass
        return sent

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
    def _intrinsics(self, sensor_id, w, h):
        """Per-sensor (fx,fy,cx,cy,dist): the node's own (sent on connect) win;
        else the --calib override file; else a FOV estimate."""
        if sensor_id in self._sensor_intr:
            return self._sensor_intr[sensor_id]
        key = (w, h)
        if key not in self._intr:
            self._intr[key] = load_intrinsics(self.calib, w, h)
        return self._intr[key]

    def _ray_table(self, sensor_id, full_w, full_h):
        """Distortion-aware ray table for this sensor's full-res grid (cached)."""
        cached = self._ray.get(sensor_id)
        if cached and cached[0] == full_w and cached[1] == full_h:
            return cached[2], cached[3]
        fx, fy, cx, cy, dist = self._intrinsics(sensor_id, full_w, full_h)
        rx, ry = compute_ray_table(full_w, full_h, fx, fy, cx, cy, dist)
        self._ray[sensor_id] = (full_w, full_h, rx, ry)
        return rx, ry

    def _serve_node(self, conn, addr):
        print("[preview] node connected %s" % (addr[0],))
        with self._lock:
            self._nodes.append(conn)
        win = {"t": time.time(), "n": 0, "pts": 0, "bytes": 0}   # throughput log
        try:
            while True:
                msg = read_message(conn)
                if msg is None:
                    break
                kind, payload = msg
                if kind == "calib":
                    sid = payload["sensor_id"]
                    self._sensor_intr[sid] = (
                        payload["fx"], payload["fy"], payload["cx"],
                        payload["cy"], payload.get("dist", (0.0,) * 8))
                    self._ray.pop(sid, None)        # rebuild ray table on next frame
                    d = payload.get("dist", (0.0,) * 8)
                    print("[preview] sensor %d intrinsics from node: "
                          "fx=%.1f fy=%.1f cx=%.1f cy=%.1f dist=[%s]" % (
                              sid, payload["fx"], payload["fy"],
                              payload["cx"], payload["cy"],
                              " ".join("%.3f" % c for c in d)))
                    continue
                if kind == "imu":
                    sid = payload["sensor_id"]
                    g = gravity_to_view(payload["gravity"])
                    self._sensor_gravity[sid] = g
                    print("[preview] sensor %d gravity(view) = %s" % (
                        sid, None if g is None else
                        "(%.3f, %.3f, %.3f)" % g))
                    continue
                if kind == "extrinsic":
                    sid = payload["sensor_id"]
                    R = np.asarray(payload["R"], dtype=np.float32).reshape(3, 3)
                    t = np.asarray(payload["t"], dtype=np.float32)
                    # Skip the transform when it's identity+zero (color_to_depth),
                    # so the default path stays a pure no-op.
                    if np.allclose(R, np.eye(3), atol=1e-6) and \
                            np.allclose(t, 0.0, atol=1e-6):
                        self._sensor_extrinsic[sid] = None
                    else:
                        self._sensor_extrinsic[sid] = (R, t)
                    print("[preview] sensor %d grid->depth extrinsic %s" % (
                        sid, "identity" if self._sensor_extrinsic[sid] is None
                        else "set (registers to depth frame)"))
                    continue
                frame = payload
                if not frame.depth_rvl:
                    continue
                depth = rvl.decompress(frame.depth, frame.width * frame.height)
                # the node may have downsampled by frame.stride; the ray table is
                # full-res, so derive the original dimensions.
                ns = frame.stride or 1
                ray_x, ray_y = self._ray_table(frame.sensor_id,
                                               frame.width * ns, frame.height * ns)
                cgrid = None
                if frame.color_aligned and frame.color:
                    cgrid = aligned_color_grid(frame.color, depth,
                                               frame.width, frame.height)
                extrinsic = self._sensor_extrinsic.get(frame.sensor_id)
                xyz, rgb = unproject(depth, frame.width, frame.height,
                                     ray_x, ray_y, self.stride, ns, cgrid,
                                     extrinsic)
                if xyz.shape[0] > self.max_points:
                    idx = np.linspace(0, xyz.shape[0] - 1, self.max_points).astype(int)
                    xyz = xyz[idx]
                    if rgb is not None:
                        rgb = rgb[idx]
                gravity = self._sensor_gravity.get(frame.sensor_id)
                out = build_message(frame.sensor_id, frame.frame_id, xyz, rgb,
                                    gravity)
                self._broadcast(out)
                self.frames_relayed += 1

                win["n"] += 1; win["pts"] += xyz.shape[0]; win["bytes"] += len(out)
                if win["n"] >= 30:
                    dt = max(1e-6, time.time() - win["t"])
                    with self._lock:
                        nclients = len(self._clients)
                    print("[preview] sensor %d: %.1f fps in | %d pts | "
                          "%.0f KB/f | %d viewer(s)" % (
                              frame.sensor_id, win["n"] / dt,
                              win["pts"] // win["n"],
                              win["bytes"] / win["n"] / 1024.0, nclients))
                    win = {"t": time.time(), "n": 0, "pts": 0, "bytes": 0}
        finally:
            with self._lock:
                if conn in self._nodes:
                    self._nodes.remove(conn)
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
    ap.add_argument("--stride", type=int, default=1,
                    help="ADDITIONAL relay-side downsample on top of the node's "
                         "--preview-stride (1 = none; total = node*relay)")
    ap.add_argument("--max-points", type=int, default=200000)
    ap.add_argument("--rig-id", default=discovery.DEFAULT_RIG_ID,
                    help="discovery rig id nodes use to find this relay")
    ap.add_argument("--discovery-port", type=int, default=discovery.DISCOVERY_PORT)
    ap.add_argument("--no-discovery", action="store_true",
                    help="don't answer LAN discovery broadcasts")
    args = ap.parse_args()
    server = PreviewServer(calib=args.calib, stride=args.stride,
                           max_points=args.max_points)
    if not args.no_discovery:
        # Answer nodes broadcasting "where is central?" with our node port, so a
        # node configured with --host auto finds us regardless of our DHCP IP.
        discovery.start_responder(args.node_port, rig_id=args.rig_id,
                                  port=args.discovery_port)
        print("[preview] discovery responder on udp:%d (rig '%s')"
              % (args.discovery_port, args.rig_id))
    try:
        server.run(args.node_host, args.node_port, args.ws_host, args.ws_port)
    except KeyboardInterrupt:
        print("\n[preview] stopped (%d frames relayed)" % server.frames_relayed)


if __name__ == "__main__":
    main()
