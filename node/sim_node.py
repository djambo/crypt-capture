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
import threading
import time
from array import array

from protocol import control, discovery, rvl
from protocol.frame import Frame, encode_calib, encode_imu, encode_extrinsic
from node import camera_modes

DEFAULT_W, DEFAULT_H = 640, 576   # Azure Kinect NFOV unbinned depth resolution

# Synthetic gravity (down) unit vector in the depth OPTICAL frame (x right,
# y down, z forward) — a slightly tilted, mostly-upright camera, so the viewer
# shows a non-trivial floor tilt (proving the IMU path end-to-end, no hardware).
SIM_GRAVITY_OPTICAL = (0.15, 0.98, 0.10)
# While IMU streaming is on, the sim wobbles the down vector so you can SEE the
# floor reorient live (mimics physically turning the camera). Cadence in frames.
IMU_EVERY = 10


def synth_frame(width, height, frame_id, sensor_id, stride=1):
    """A moving elliptical blob of smooth valid depth on a zero background, plus
    a depth-aligned RGB payload (one triple per valid pixel, row-major) so the
    color path is exercisable without hardware. When stride>1 the grid is
    generated directly at the downsampled resolution; blob geometry still uses
    original pixel coords so the relay reconstructs the same cloud.

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
                depth[gy * gw + gx] = z
                # simple gradient so the cloud isn't a flat color
                color += bytes((int(255 * x / width),
                                int(255 * y / height),
                                int(255 * (1.0 - min(1.0, r2)))))
    return depth, bytes(color), gw, gh


# --- Ball mode (rig-calibration testing, no hardware) ----------------------
# With --ball R (+ --pose "yaw,x,y,z"), the sim stops emitting the blob and
# instead ray-renders a small sphere moving through a WORLD-frame trajectory,
# as seen from this sensor's known pose. Run several sim nodes with different
# poses against one relay and every rig-calibration stage is testable
# headlessly: the solved transforms must recover the poses. The trajectory is
# a function of WALL CLOCK time so separate node processes agree on where the
# ball is (like a real shared ball).

def parse_pose(spec):
    """'yaw_deg,x,y,z[,pitch_deg]' -> (R (3x3 rows), t) — this sensor's
    view->world pose (R = R_y(yaw)·R_x(pitch), position in metres). The
    optional pitch tips the camera (e.g. mounted high looking down), giving
    each sensor's floor its own tilt in view space — what the per-sensor
    floor leveling exists to correct. Identity: '0,0,0,0'."""
    parts = [float(p) for p in spec.split(",")]
    if len(parts) not in (4, 5):
        raise ValueError("--pose wants 'yaw_deg,x,y,z[,pitch_deg]', got %r"
                         % (spec,))
    yaw, x, y, z = parts[:4]
    pitch = parts[4] if len(parts) == 5 else 0.0
    a, b = math.radians(yaw), math.radians(pitch)
    cy_, sy_ = math.cos(a), math.sin(a)
    cx_, sx_ = math.cos(b), math.sin(b)
    # R_y(yaw) · R_x(pitch), rows.
    return ((cy_, sy_ * sx_, sy_ * cx_),
            (0.0, cx_, -sx_),
            (-sy_, cy_ * sx_, cy_ * cx_)), (x, y, z)


def world_to_view(R, t, p):
    """view = R^T · (p - t) for a view->world pose (R rows, t)."""
    dx, dy, dz = p[0] - t[0], p[1] - t[1], p[2] - t[2]
    return (R[0][0] * dx + R[1][0] * dy + R[2][0] * dz,
            R[0][1] * dx + R[1][1] * dy + R[2][1] * dz,
            R[0][2] * dx + R[1][2] * dy + R[2][2] * dz)


def ball_world_pos(now):
    """The shared ball trajectory (world frame, metres): a slow Lissajous wave
    through the capture volume around the subject spot (z ~ -1.3)."""
    return (0.55 * math.sin(2 * math.pi * now / 7.3),
            0.35 * math.sin(2 * math.pi * now / 11.9),
            -1.3 + 0.40 * math.sin(2 * math.pi * now / 9.1))


def synth_ball_frame(width, height, frame_id, ball_view, radius, stride=1,
                     pose=None, floor=None):
    """Ray-render a sphere (center `ball_view` in the VIEW frame: x right,
    y up, z toward viewer) into a strided depth grid + aligned color payload,
    using the same synthetic pinhole intrinsics send_calib() announces.

    With `pose` (view->world R,t) and `floor` (a world-frame floor height in
    metres), also renders the floor plane world_y = floor as seen from this
    pose — so a posed multi-sensor sim rig exercises per-sensor floor leveling
    (each camera sees the SAME world floor at its own tilt). The color payload
    is emitted in row-major valid-pixel order, matching the depth mask.
    Returns (depth_array, color_bytes, grid_w, grid_h)."""
    xs = list(range(0, width, stride))
    ys = list(range(0, height, stride))
    gw, gh = len(xs), len(ys)
    depth = array("H", bytes(2 * gw * gh))
    color = bytearray()
    fx = (width / 2.0) / math.tan(math.radians(75.0) / 2.0)
    cx, cy = width / 2.0, height / 2.0
    # view -> optical (x right, y down, z forward): the relay flips y,z back.
    cox, coy, coz = ball_view[0], -ball_view[1], -ball_view[2]
    ball_visible = coz > radius + 0.05          # else behind/on the camera
    # Ball projection bounds (pure Python: keep the sphere scan tight).
    if ball_visible:
        pr = fx * radius / (coz - radius) + 2 * stride
        u0, u1 = cx + fx * cox / coz - pr, cx + fx * cox / coz + pr
        v0, v1 = cy + fx * coy / coz - pr, cy + fx * coy / coz + pr
        c2r2 = cox * cox + coy * coy + coz * coz - radius * radius
    do_floor = pose is not None and floor is not None
    if do_floor:
        R, t = pose
        # The ray's world-y rate is linear in (rx, ry): (R·d_view)_y uses
        # R's row 1 (d_view = (rx, -ry, -1) for optical ray (rx, ry, 1)).
        r10, r11, r12 = R[1][0], R[1][1], R[1][2]
        ty = t[1]
    jit = random.Random(frame_id * 131)
    for gy, y in enumerate(ys):
        ry = (y - cy) / fx
        for gx, x in enumerate(xs):
            rx = (x - cx) / fx
            z_ball = None
            if ball_visible and v0 <= y <= v1 and u0 <= x <= u1:
                dc = rx * cox + ry * coy + coz     # d·c for ray d=(rx,ry,1)
                dd = rx * rx + ry * ry + 1.0       # |d|^2
                disc = dc * dc - dd * c2r2
                if disc > 0.0:
                    z_ball = (dc - math.sqrt(disc)) / dd  # nearest hit (m)
            z_floor = None
            if do_floor:
                # view dir for optical ray (rx,ry,1) is (rx,-ry,-1); its
                # world-y rate: (R·(rx,-ry,-1))_y = r10·rx - r11·ry - r12.
                dy = r10 * rx - r11 * ry - r12
                if dy < -1e-6:                     # ray heads downward
                    zf = (floor - ty) / dy
                    if 0.25 < zf < 6.0:
                        z_floor = zf
            z = z_ball if z_floor is None else (
                z_floor if z_ball is None else min(z_ball, z_floor))
            if z is None:
                continue
            depth[gy * gw + gx] = max(1, int(z * 1000) + jit.randint(0, 2))
            color += bytes((240, 240, 200) if z == z_ball
                           else (90, 90, 100))     # pale ball / grey floor
    return depth, bytes(color), gw, gh


def run(host, port, sensor_id, frames, fps, width=DEFAULT_W, height=DEFAULT_H,
        preview_stride=1, rig_id=discovery.DEFAULT_RIG_ID,
        discovery_port=discovery.DISCOVERY_PORT, ball=0.0, pose=None,
        floor=None):
    if host == "auto":                          # find central by rig id (see discovery.py)
        found = discovery.discover_central(rig_id, port=discovery_port)
        if found is None:
            raise SystemExit("discovery: no central relay answered for rig '%s'"
                             % rig_id)
        host, port = found
        print("discovery: found central at %s:%d" % (host, port))
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    period = 1.0 / fps
    s = max(1, preview_stride)

    # Ball mode: a known view->world pose per sensor + a pose-consistent IMU
    # vector (world down seen from this camera), so rig calibration AND the
    # rough IMU leveling are both testable against ground truth.
    pose_R, pose_t = parse_pose(pose) if pose else (
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))
    if ball > 0 or pose:
        gvx, gvy, gvz = world_to_view(pose_R, (0.0, 0.0, 0.0), (0.0, -1.0, 0.0))
        gravity_optical = (gvx, -gvy, -gvz)     # view -> optical flip
    else:
        gravity_optical = SIM_GRAVITY_OPTICAL

    # Mutable camera config + current synthetic grid, so set_camera is testable
    # headless: changing the depth FOV mode / alignment switches the grid size
    # the sim emits, and re-sends the CCAL handshake (proving the relay rebuilds
    # its ray table at the new resolution). The sim has no real cameras, so it
    # just resizes the synthetic blob — geometry is illustrative, not metric.
    cfg = dict(camera_modes.DEFAULTS)
    state = {"w": width, "h": height, "resend_calib": True}
    imu_state = {"stream": False}              # live orientation toggle (set_imu)
    cfg_lock = threading.Lock()

    def on_command(cmd):
        c = cmd.get("cmd")
        if c == "set_camera":
            with cfg_lock:
                changed = camera_modes.apply_camera_command(cfg, cmd)
                changed.pop("restart", None)
                if changed:
                    w, h = camera_modes.grid_dims(
                        cfg["depth_mode"], cfg["color_resolution"], cfg["align"])
                    state["w"], state["h"] = w, h
                    state["resend_calib"] = True
                    print("sensor %d: set_camera %s -> grid %dx%d (align=%s)"
                          % (sensor_id, changed, w, h, cfg["align"]))
        elif c == "set_imu":
            imu_state["stream"] = bool(cmd.get("enabled", False))
            print("sensor %d: imu streaming -> %s"
                  % (sensor_id, imu_state["stream"]))
        else:
            # background commands etc. — sim has no real scene to subtract, so it
            # just acknowledges (the real node acts on them). Proves the
            # browser->relay->node command path.
            print("sensor %d: received command %r" % (sensor_id, cmd))

    control.start_reader(sock, on_command)

    def send_calib(w, h):
        # Synthetic intrinsics (a plausible ~75° FOV pinhole), so the
        # node-intrinsics path works headless and needs no --calib on the relay.
        fx = (w / 2.0) / math.tan(math.radians(75.0) / 2.0)
        sock.sendall(encode_calib(sensor_id, w, h, fx, fx, w / 2.0, h / 2.0))
        # A fixed synthetic gravity vector so the orientation path is exercised
        # without a real IMU (the relay re-expresses it in the cloud frame).
        gx, gy, gz = gravity_optical
        mag = math.sqrt(gx * gx + gy * gy + gz * gz) or 1.0
        sock.sendall(encode_imu(sensor_id, gx / mag, gy / mag, gz / mag))
        # Identity grid->depth extrinsic (the sim grid is already "depth"); proves
        # the registration path end-to-end without a real colour camera.
        sock.sendall(encode_extrinsic(sensor_id,
                                      (1, 0, 0, 0, 1, 0, 0, 0, 1), (0, 0, 0)))

    sent = 0
    try:
        while frames <= 0 or sent < frames:   # frames <= 0 => until Ctrl-C
            with cfg_lock:
                width, height = state["w"], state["h"]
                resend = state["resend_calib"]
                state["resend_calib"] = False
            if resend:                         # (re)announce grid + intrinsics
                send_calib(width, height)
            if ball > 0:
                bv = world_to_view(pose_R, pose_t, ball_world_pos(time.time()))
                depth, color, gw, gh = synth_ball_frame(
                    width, height, sent, bv, ball, s,
                    pose=(pose_R, pose_t), floor=floor)
            else:
                depth, color, gw, gh = synth_frame(
                    width, height, sent, sensor_id, s)
            comp = rvl.compress(depth)
            frame = Frame(
                sensor_id=sensor_id, frame_id=sent,
                timestamp_ns=int(time.time() * 1e9), width=gw, height=gh,
                depth=comp, color=color, depth_rvl=True, color_aligned=True,
                stride=s,
            )
            sock.sendall(frame.encode())
            sent += 1

            # Live orientation: wobble the synthetic down vector while streaming,
            # so the viewer's floor/gizmo visibly reorient (no real IMU). In
            # ball/pose mode the vector stays pose-true (a wobble would corrupt
            # the rough-align leveling the mode exists to test).
            if imu_state["stream"] and sent % IMU_EVERY == 0 and \
                    not (ball > 0 or pose):
                t = sent * 0.05
                gx, gy, gz = math.sin(t) * 0.3, 1.0, math.cos(t) * 0.3
                mag = math.sqrt(gx * gx + gy * gy + gz * gz)
                sock.sendall(encode_imu(sensor_id, gx / mag, gy / mag, gz / mag))

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
    ap.add_argument("--rig-id", default=discovery.DEFAULT_RIG_ID,
                    help="discovery rig id (with --host auto)")
    ap.add_argument("--discovery-port", type=int,
                    default=discovery.DISCOVERY_PORT)
    ap.add_argument("--ball", type=float, default=0.0,
                    help="rig-calibration test mode: emit a moving sphere of "
                         "this radius (m) instead of the blob (0 = off)")
    ap.add_argument("--pose", default=None,
                    help="this sensor's view->world pose 'yaw_deg,x,y,z' "
                         "(ball mode; default identity)")
    ap.add_argument("--floor", type=float, default=None,
                    help="ball mode: also render the world floor plane at "
                         "this world-frame height (m), e.g. -1.2 — exercises "
                         "per-sensor floor leveling headlessly")
    args = ap.parse_args()
    n = run(args.host, args.port, args.sensor, args.frames, args.fps,
            preview_stride=args.preview_stride, rig_id=args.rig_id,
            discovery_port=args.discovery_port, ball=args.ball,
            pose=args.pose, floor=args.floor)
    print("sensor %d: streamed %d frames" % (args.sensor, n))


if __name__ == "__main__":
    main()
