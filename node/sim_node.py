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

from protocol import control, rvl
from protocol.frame import Frame, encode_calib, encode_imu
from node import camera_modes

DEFAULT_W, DEFAULT_H = 640, 576   # Azure Kinect NFOV unbinned depth resolution

# Synthetic gravity (down) unit vector in the depth OPTICAL frame (x right,
# y down, z forward) — a slightly tilted, mostly-upright camera, so the viewer
# shows a non-trivial floor tilt (proving the IMU path end-to-end, no hardware).
SIM_GRAVITY_OPTICAL = (0.15, 0.98, 0.10)
# While IMU streaming is on, the sim wobbles the down vector so you can SEE the
# floor reorient live (mimics physically turning the camera). Cadence in frames.
IMU_EVERY = 10


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
        if c == "set_depth":
            if "min" in cmd:
                rng["min"] = int(cmd["min"])
            if "max" in cmd:
                rng["max"] = int(cmd["max"])
            print("sensor %d: depth mask -> [%d, %d] mm"
                  % (sensor_id, rng["min"], rng["max"]))
        elif c == "set_camera":
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
        gx, gy, gz = SIM_GRAVITY_OPTICAL
        mag = math.sqrt(gx * gx + gy * gy + gz * gz) or 1.0
        sock.sendall(encode_imu(sensor_id, gx / mag, gy / mag, gz / mag))

    sent = 0
    try:
        while frames <= 0 or sent < frames:   # frames <= 0 => until Ctrl-C
            with cfg_lock:
                width, height = state["w"], state["h"]
                resend = state["resend_calib"]
                state["resend_calib"] = False
            if resend:                         # (re)announce grid + intrinsics
                send_calib(width, height)
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

            # Live orientation: wobble the synthetic down vector while streaming,
            # so the viewer's floor/gizmo visibly reorient (no real IMU).
            if imu_state["stream"] and sent % IMU_EVERY == 0:
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
    args = ap.parse_args()
    n = run(args.host, args.port, args.sensor, args.frames, args.fps,
            preview_stride=args.preview_stride)
    print("sensor %d: streamed %d frames" % (args.sensor, n))


if __name__ == "__main__":
    main()
