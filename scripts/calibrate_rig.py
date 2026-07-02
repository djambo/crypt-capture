"""
Rig extrinsic calibration — the marker-ball ("wand") collection + solve.

Headless WebSocket client (same transport as scripts/preview_client.py):
connects to the running preview relay, collects every sensor's CPV1 frames for
--seconds, fits the ball center per sensor per frame (known --ball-radius),
then solves each sensor's rigid transform into the reference sensor's frame
(closed-form Kabsch over the shared trajectory) and writes rig_calib.json for
the relay to apply (--rig-calib / auto-reload). Full procedure + math:
docs/rig_calibration.md; the solver lives in central/calibration.py.

Operator flow (all nodes streaming, background captured on every sensor so the
ball + stick are the only foreground):

    python3 -m scripts.calibrate_rig --seconds 30 --ball-radius 0.05
    # wave the ball slowly through the whole capture volume...
    # -> per-sensor rms/pairs printed; millimetres = good, centimetres = re-run
    # -> rig_calib.json written; the relay picks it up (mtime watch) and the
    #    clouds register live.

Frames whose foreground point count is implausible for the ball alone (person
still in frame, empty frames) or whose sphere fit doesn't converge are skipped
— the gates are tunable (--min-points/--max-points/--max-fit-rms).
"""

import argparse
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from central.calibration import BallTracker, save_rig_calib, solve_rig
from protocol import websocket

_HEADER = struct.Struct("<4sIIII")


def parse_positions(payload):
    """CPV1 binary message -> (sensor_id, positions (N,3) float32) or None."""
    if len(payload) < _HEADER.size:
        return None
    magic, _flags, sensor, _frame_id, count = _HEADER.unpack_from(payload, 0)
    if magic != b"CPV1" or count == 0:
        return None
    if len(payload) < _HEADER.size + count * 12:
        return None
    pts = np.frombuffer(payload, dtype="<f4", count=count * 3,
                        offset=_HEADER.size).reshape(-1, 3)
    return sensor, pts


def collect(host, port, seconds, tracker, report_every=2.0):
    """Feed the tracker from the live stream for `seconds`. Arrival time is the
    correspondence timestamp: hardware-synced sensors emit together, and the
    ball is waved slowly precisely so residual skew stays in the residual."""
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if not websocket.client_handshake(sock, host, port):
        raise SystemExit("WebSocket handshake failed")
    sock.settimeout(1.0)
    print("connected to ws://%s:%d/ — collecting for %.0f s "
          "(wave the ball through the whole volume, slowly)"
          % (host, port, seconds))

    deadline = time.time() + seconds
    last_report = time.time()
    seen = set()
    try:
        while time.time() < deadline:
            try:
                msg = websocket.read_frame(sock)
            except socket.timeout:
                continue
            if msg is None:
                print("relay closed the connection")
                break
            opcode, payload = msg
            if opcode != websocket.OP_BINARY:
                continue
            parsed = parse_positions(payload)
            if parsed is None:
                continue
            sensor, pts = parsed
            seen.add(sensor)
            tracker.add(sensor, time.time(), pts)
            now = time.time()
            if now - last_report >= report_every:
                counts = tracker.counts()
                print("  %2ds left | centers: %s" % (
                    max(0, int(deadline - now)),
                    " ".join("s%d:%d" % (s, counts.get(s, 0))
                             for s in sorted(seen)) or "none yet"))
                last_report = now
    finally:
        try:
            sock.sendall(websocket.encode_frame(
                b"", opcode=websocket.OP_CLOSE, mask=True))
        except OSError:
            pass
        sock.close()
    return seen


def main():
    ap = argparse.ArgumentParser(
        description="marker-ball rig calibration (collect + solve + write)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080,
                    help="the relay's WebSocket port")
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="collection window (cover the whole volume)")
    ap.add_argument("--ball-radius", type=float, default=0.05,
                    help="calibration ball radius in metres")
    ap.add_argument("--ref", type=int, default=None,
                    help="reference sensor id (default: lowest seen)")
    ap.add_argument("--out", default="rig_calib.json")
    ap.add_argument("--min-points", type=int, default=40,
                    help="skip frames with fewer foreground points")
    ap.add_argument("--max-points", type=int, default=8000,
                    help="skip frames with more foreground points than the "
                         "ball can plausibly produce (person in frame)")
    ap.add_argument("--max-fit-rms", type=float, default=0.012,
                    help="skip frames whose sphere fit residual (m) exceeds this")
    ap.add_argument("--min-pairs", type=int, default=30,
                    help="minimum matched samples for a sensor to be solved")
    ap.add_argument("--max-dt", type=float, default=0.05,
                    help="max timestamp gap (s) when pairing tracks")
    args = ap.parse_args()

    tracker = BallTracker(args.ball_radius, min_points=args.min_points,
                          max_points=args.max_points,
                          max_fit_rms=args.max_fit_rms)
    seen = collect(args.host, args.port, args.seconds, tracker)
    if not tracker.tracks:
        for sid in sorted(seen):
            rej = tracker.rejected.get(sid, {})
            print("sensor %d: 0 ball centers (rejected: %d count, %d fit)"
                  % (sid, rej.get("count", 0), rej.get("fit", 0)))
        raise SystemExit(
            "no ball centers on any sensor — is the background captured and "
            "the ball (only) in frame? (--min/max-points gate: see above)")

    ref = args.ref if args.ref is not None else min(tracker.tracks)
    rig = solve_rig(tracker.tracks, ref=ref, max_dt=args.max_dt,
                    min_pairs=args.min_pairs)

    print("\nreference sensor: %d" % ref)
    for sid in sorted(seen):
        rej = tracker.rejected.get(sid, {"count": 0, "fit": 0})
        centers = len(tracker.tracks.get(sid, []))
        if sid in rig:
            s = rig[sid]
            print("sensor %d: rms %.1f mm over %d pairs "
                  "(%d centers; rejected %d count / %d fit)"
                  % (sid, s["rms"] * 1000, s["pairs"], centers,
                     rej["count"], rej["fit"]))
        else:
            print("sensor %d: NOT SOLVED — %d centers, rejected %d count / "
                  "%d fit (need >= %d matched pairs; did it see the ball?)"
                  % (sid, centers, rej["count"], rej["fit"], args.min_pairs))

    worst = max(s["rms"] for s in rig.values())
    if worst > 0.02:
        print("WARNING: worst rms %.0f mm is centimetre-scale — re-run "
              "(slower wave, or a sensor barely saw the ball)" % (worst * 1000))

    save_rig_calib(args.out, rig, tier="fine", ref=ref,
                   ball_radius=args.ball_radius)
    print("wrote %s (%d sensor(s)) — the relay auto-reloads it" %
          (args.out, len(rig)))


if __name__ == "__main__":
    main()
