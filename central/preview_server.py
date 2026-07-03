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

import os

import numpy as np

from central import calibration
from protocol import control, discovery, rvl, websocket
from protocol.frame import read_message

# Browser→node commands the relay will forward (everything else is ignored).
_FORWARDED_COMMANDS = ("capture_bg", "clear_bg", "set_bg_margin",
                       "set_denoise", "set_camera", "set_imu")
# Browser→RELAY commands, handled here (rig calibration; nothing goes to nodes).
_RELAY_COMMANDS = ("calibrate_fine", "calibrate_rough", "calibrate_floor",
                   "reload_rig_calib", "clear_rig_calib")

RIG_CALIB_POLL_S = 1.0        # how often the rig_calib.json mtime is checked
CALIB_STATUS_EVERY_S = 1.0    # progress broadcast cadence during collection

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
    def __init__(self, calib=None, stride=2, max_points=200000,
                 rig_calib="rig_calib.json"):
        self.calib = calib
        self.stride = stride
        self.max_points = max_points
        self._clients = []                  # browser WebSocket sockets
        # Per-client WRITE lock: node threads (one per sensor) and the calib
        # status/pose broadcasts all write to the same client sockets. sendall
        # of a large frame spans multiple send() syscalls, so concurrent
        # writers would interleave bytes MID-FRAME and the browser drops the
        # socket with "Invalid frame header".
        self._client_locks = {}             # conn -> threading.Lock
        self._nodes = []                    # connected node TCP sockets
        self._lock = threading.Lock()
        self._intr = {}                     # (w,h) -> fallback intrinsics+dist
        self._sensor_intr = {}              # sensor_id -> (fx,fy,cx,cy,dist)
        self._ray = {}                      # sensor_id -> (w,h,ray_x,ray_y)
        self._sensor_gravity = {}           # sensor_id -> (gx,gy,gz) view-frame
        self._sensor_extrinsic = {}         # sensor_id -> (R 3x3, t 3) or None
        self.frames_relayed = 0
        # Rig extrinsics (docs/rig_calibration.md): per-sensor (R,t) applied
        # AFTER unprojection so ONE canonical world frame goes out on the wire
        # (no CPV1 change). Loaded from rig_calib.json when present, watched
        # for changes, and (re)written by the viewer-driven calibration
        # sessions below. No file -> empty dict -> pure no-op (today's path).
        self.rig_calib_path = rig_calib or None
        self._rig = {}                      # sensor_id -> (R f32 3x3, t f32 3)
        self._rig_meta = None               # tier/ref/per-sensor rms+pairs
        self._rig_mtime = None
        self._calib_lock = threading.Lock()
        self._calib_session = None          # active wand/rough collection
        if self.rig_calib_path:
            self._load_rig_calib(announce=False)

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
                self._client_locks[conn] = threading.Lock()
            print("[preview] viewer connected %s (%d total)"
                  % (addr[0], len(self._clients)))
            # A calibrated rig greets each viewer with the camera poses so its
            # gizmos land at the sensors' true positions immediately.
            if self._rig:
                self._send_text(conn, self._rig_poses_message())
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
        if not isinstance(cmd, dict):
            return
        name = cmd.get("cmd")
        if name in _RELAY_COMMANDS:            # handled here, not forwarded
            if name == "reload_rig_calib":
                self._load_rig_calib(force=True)
            elif name == "clear_rig_calib":
                self._clear_rig_calib()
            else:
                self._start_calibration(cmd)
            return
        if name in _FORWARDED_COMMANDS:
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
            self._client_locks.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    def _send_frame(self, conn, frame):
        """Write one encoded WS frame to a client, serialised per client so
        concurrent writers never interleave bytes mid-frame."""
        lock = self._client_locks.get(conn)
        if lock is None:
            return
        try:
            with lock:
                conn.sendall(frame)
        except OSError:
            self._drop(conn)

    def _broadcast(self, payload):
        frame = websocket.encode_frame(payload, opcode=websocket.OP_BINARY)
        with self._lock:
            clients = list(self._clients)
        for conn in clients:
            self._send_frame(conn, frame)

    def _send_text(self, conn, obj):
        data = json.dumps(obj).encode("utf-8")
        self._send_frame(conn, websocket.encode_frame(
            data, opcode=websocket.OP_TEXT))

    def _broadcast_text(self, obj):
        frame = websocket.encode_frame(json.dumps(obj).encode("utf-8"),
                                       opcode=websocket.OP_TEXT)
        with self._lock:
            clients = list(self._clients)
        for conn in clients:
            self._send_frame(conn, frame)

    # --- rig extrinsics: one canonical world frame on the wire -----------
    # (docs/rig_calibration.md; solved by scripts/calibrate_rig.py or the
    # viewer-driven sessions below, stored in rig_calib.json.)

    def _load_rig_calib(self, announce=True, force=False):
        """(Re)load rig_calib.json if it appeared/changed; clear if it went
        away. Broadcasts the new camera poses to viewers when announcing."""
        path = self.rig_calib_path
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if self._rig:                     # file removed -> back to raw
                self._rig, self._rig_meta, self._rig_mtime = {}, None, None
                print("[preview] rig calib %s removed — raw sensor frames"
                      % path)
                if announce:
                    self._broadcast_text(self._rig_poses_message())
            return
        if not force and mtime == self._rig_mtime:
            return
        try:
            transforms, meta = calibration.load_rig_calib(path)
        except (ValueError, KeyError, TypeError, OSError) as e:
            print("[preview] rig calib %s unreadable (%s) — keeping previous"
                  % (path, e))
            self._rig_mtime = mtime
            return
        self._rig, self._rig_meta, self._rig_mtime = transforms, meta, mtime
        print("[preview] rig calib %s: tier=%s ref=%s, %d sensor(s)" % (
            path, meta.get("tier"), meta.get("ref"), len(transforms)))
        for sid in sorted(transforms):
            m = meta["sensors"].get(sid, {})
            print("[preview]   sensor %d: rms %.1f mm over %d pairs"
                  % (sid, m.get("rms", 0.0) * 1000, m.get("pairs", 0)))
        if announce:
            self._broadcast_text(self._rig_poses_message())

    def _rig_watch_loop(self):
        while True:
            time.sleep(RIG_CALIB_POLL_S)
            self._load_rig_calib()

    def _rig_poses_message(self):
        """The per-sensor [R|t] camera poses for the viewer's gizmos. Empty
        sensors {} tells the viewer to reset poses to the origin."""
        meta = self._rig_meta or {"sensors": {}}
        sensors = {}
        for sid, (R, t) in self._rig.items():
            m = meta["sensors"].get(sid, {})
            sensors[str(sid)] = {
                "R": np.asarray(R, dtype=float).tolist(),
                "t": np.asarray(t, dtype=float).tolist(),
                "rms": m.get("rms"), "pairs": m.get("pairs"),
            }
        return {"type": "rig_poses", "tier": meta.get("tier"),
                "ref": meta.get("ref"), "sensors": sensors}

    def _clear_rig_calib(self):
        """Reset alignment: cancel any running calibration session, delete
        rig_calib.json, and go back to raw per-camera frames (the viewer's
        Reset button / `clear_rig_calib`). Broadcasts empty poses so gizmos
        return to the origin."""
        with self._calib_lock:
            session = self._calib_session
            if session is not None:
                session["cancelled"] = True
                self._calib_session = None
        path = self.rig_calib_path or "rig_calib.json"
        try:
            os.remove(path)
        except OSError:
            pass
        self._rig, self._rig_meta, self._rig_mtime = {}, None, None
        print("[preview] rig calib cleared — raw sensor frames")
        self._broadcast_text(self._rig_poses_message())
        if session is not None:
            print("[preview] %s calibration cancelled" % session["tier"])
            # Sent AFTER the clear so it outruns any in-flight "collecting"
            # broadcast (per-connection ordering) — the viewer's status line
            # must not be left saying "collecting…" for a dead session.
            self._broadcast_text({"type": "calib_status",
                                  "state": "cancelled",
                                  "tier": session["tier"]})

    # --- viewer-driven calibration sessions (Fine/Rough Align buttons) ---

    def _start_calibration(self, cmd):
        tier = {"calibrate_fine": "fine", "calibrate_rough": "rough",
                "calibrate_floor": "floor"}[cmd.get("cmd")]
        seconds = float(cmd.get("seconds",
                                {"fine": 30.0, "rough": 10.0,
                                 "floor": 3.0}[tier]))
        with self._calib_lock:
            if self._calib_session is not None:
                self._broadcast_text({
                    "type": "calib_status", "state": "busy",
                    "tier": self._calib_session["tier"]})
                return
            radius = None
            min_pairs = 0
            if tier == "fine":
                radius = float(cmd.get("ball_radius", 0.05))
                tracker = calibration.BallTracker(
                    radius,
                    min_points=int(cmd.get("min_points", 40)),
                    max_points=int(cmd.get("max_points", 8000)),
                    max_fit_rms=float(cmd.get("max_fit_rms", 0.012)))
                min_pairs = int(cmd.get("min_pairs", 30))
            elif tier == "rough":
                tracker = calibration.CentroidTracker(
                    min_points=int(cmd.get("min_points", 300)))
                min_pairs = int(cmd.get("min_pairs", 15))
            else:                              # floor: raw point samples
                tracker = calibration.FloorSampler()
            session = {"tier": tier, "tracker": tracker, "seconds": seconds,
                       "deadline": time.time() + seconds,
                       "ball_radius": radius, "min_pairs": min_pairs}
            self._calib_session = session
        print("[preview] %s calibration: collecting for %.0f s%s" % (
            tier, seconds,
            " (ball r=%.3f m)" % radius if radius else ""))
        threading.Thread(target=self._calibration_loop, args=(session,),
                         daemon=True).start()

    def _calibration_loop(self, session):
        """Broadcast collection progress ~1 Hz, then solve when time is up."""
        while True:
            if session.get("cancelled"):       # Reset pressed mid-collection
                return
            left = session["deadline"] - time.time()
            if left <= 0:
                break
            self._broadcast_text({
                "type": "calib_status", "state": "collecting",
                "tier": session["tier"], "seconds_left": round(left, 1),
                "centers": {str(s): n
                            for s, n in session["tracker"].counts().items()}})
            time.sleep(min(CALIB_STATUS_EVERY_S, left))
        self._finish_calibration(session)

    def _feed_calibration(self, sensor_id, xyz):
        """Called from the node frame path with the RAW (pre-rig-transform)
        view-frame cloud. Each sensor's frames arrive on its own node thread
        and land in that sensor's own track list, so no lock is needed."""
        session = self._calib_session
        if session is None or time.time() > session["deadline"]:
            return
        session["tracker"].add(sensor_id, time.time(), xyz)

    def _finish_calibration(self, session):
        with self._calib_lock:
            if self._calib_session is not session:
                return
            self._calib_session = None
        tracker = session["tracker"]
        tier = session["tier"]
        if tier == "fine":
            rig = calibration.solve_rig(tracker.tracks,
                                        min_pairs=session["min_pairs"])
        elif tier == "rough":
            gravities = {sid: g for sid, g in self._sensor_gravity.items()
                         if g is not None}
            rig = calibration.solve_rough(tracker.tracks, gravities,
                                          min_pairs=session["min_pairs"])
        else:                                  # floor: level each sensor
            samples = tracker.stacked()
            # World-frame up hint per sensor: its IMU gravity rotated by its
            # current rig transform (identity if uncalibrated).
            hints = {}
            for sid in samples:
                g = self._sensor_gravity.get(sid) or (0.0, -1.0, 0.0)
                rig_i = self._rig.get(sid)
                up = -np.asarray(g, dtype=np.float64)
                if rig_i is not None:
                    up = np.asarray(rig_i[0], dtype=np.float64).dot(up)
                hints[sid] = up
            # Existing entries (with their reported quality) are kept for
            # sensors the floor fit can't solve.
            meta_sensors = (self._rig_meta or {}).get("sensors", {})
            prev = {sid: {"R": R, "t": t,
                          "rms": meta_sensors.get(sid, {}).get("rms", 0.0),
                          "pairs": meta_sensors.get(sid, {}).get("pairs", 0)}
                    for sid, (R, t) in self._rig.items()}
            rig = calibration.solve_floor_level(
                samples, hints, rig=prev,
                ref=(self._rig_meta or {}).get("ref"))
        if not rig:
            reason = {
                "fine": "no ball detections passed the gates — background "
                        "captured on every sensor, ball (only) in frame?",
                "rough": "no usable centroid tracks — is a subject in frame?",
                "floor": "no credible floor plane on any sensor — is the "
                         "floor in view (clear the background subtraction)?",
            }[tier]
            print("[preview] %s calibration failed: %s" % (tier, reason))
            self._broadcast_text({
                "type": "calib_status", "state": "failed", "tier": tier,
                "reason": reason,
                "centers": {str(s): n
                            for s, n in tracker.counts().items()}})
            return
        if tier == "floor":
            # A sensor is unsolved if its entry is still the pre-existing one
            # (or absent): the floor fit found no credible plane for it.
            unsolved = sorted(sid for sid in samples
                              if rig.get(sid) is prev.get(sid))
        else:
            unsolved = sorted(set(tracker.tracks) - set(rig))
        path = self.rig_calib_path or "rig_calib.json"
        ref = (self._rig_meta or {}).get("ref")
        if ref is None or ref not in rig:
            ref = min(rig)
        calibration.save_rig_calib(path, rig, tier=tier, ref=ref,
                                   ball_radius=session["ball_radius"])
        for sid in sorted(rig):
            print("[preview] %s calibration sensor %d: rms %.1f mm over %d "
                  "pairs" % (tier, sid, rig[sid]["rms"] * 1000,
                             rig[sid]["pairs"]))
        # Load-and-announce from the file we just wrote: viewers get the new
        # poses, and the mtime is consumed so the watcher doesn't double-fire.
        # (With --rig-calib '' the loader is disabled, so apply in-memory.)
        if self.rig_calib_path:
            self._load_rig_calib(announce=True, force=True)
        else:
            self._rig, self._rig_meta = calibration.load_rig_calib(path)
            self._broadcast_text(self._rig_poses_message())
        self._broadcast_text({
            "type": "calib_status", "state": "done", "tier": tier,
            "sensors": {str(sid): {"rms": s["rms"], "pairs": s["pairs"]}
                        for sid, s in rig.items()},
            "unsolved": [str(s) for s in unsolved]})

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
                # An active calibration session consumes the RAW view-frame
                # cloud (a re-run must not solve on already-transformed points).
                if self._calib_session is not None:
                    self._feed_calibration(frame.sensor_id, xyz)
                gravity = self._sensor_gravity.get(frame.sensor_id)
                # Rig extrinsic: P_world = R·P + t per sensor, so one canonical
                # world frame goes out on the wire. The gravity vector rides in
                # the same frame as the positions, so it rotates too.
                rig = self._rig.get(frame.sensor_id)
                if rig is not None:
                    R, t = rig
                    xyz = xyz.dot(R.T) + t
                    if gravity is not None:
                        g = R.dot(np.asarray(gravity, dtype=np.float32))
                        gravity = (float(g[0]), float(g[1]), float(g[2]))
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
        if self.rig_calib_path:
            # Live reload: a re-run of scripts/calibrate_rig.py just writes the
            # file; the relay notices and the clouds re-register in place.
            threading.Thread(target=self._rig_watch_loop, daemon=True).start()
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
    ap.add_argument("--rig-calib", default="rig_calib.json",
                    help="per-sensor rig extrinsics (from scripts/"
                         "calibrate_rig.py or the viewer's Align buttons); "
                         "loaded if present, watched for changes, and the "
                         "write target for viewer-driven calibration. "
                         "'' disables. Absent file = raw frames (as before)")
    ap.add_argument("--rig-id", default=discovery.DEFAULT_RIG_ID,
                    help="discovery rig id nodes use to find this relay")
    ap.add_argument("--discovery-port", type=int, default=discovery.DISCOVERY_PORT)
    ap.add_argument("--no-discovery", action="store_true",
                    help="don't answer LAN discovery broadcasts")
    args = ap.parse_args()
    server = PreviewServer(calib=args.calib, stride=args.stride,
                           max_points=args.max_points,
                           rig_calib=args.rig_calib)
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
