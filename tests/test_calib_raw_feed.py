"""
Calibration is fed the RAW (pre-rig) cloud — regression test for the
fine-after-rough offset bug.

A fine pass runs AFTER rough, so a rough rig is already loaded. `solve_rig` must
compute the FULL raw->world transform and the LOCK marker applies the rig ONCE,
so `_feed_calibration` must receive the RAW view-frame cloud. The bug: the relay
applied the rig to the cloud BEFORE feeding calibration, so the fine solve saw
two already-registered tracks, produced a ~identity residual that REPLACED the
rough rig, and the clouds sprang apart (and the non-ref sensor's LOCK marker,
rig-applied twice, sat offset sideways).

This drives one frame through `_serve_node` with a fine session active, once with
NO rig and once with a pure-translation rig, and asserts the cloud fed to the
tracker is IDENTICAL both times (i.e. rig-independent = raw). With the bug the
second run's cloud would be shifted by the translation.
"""

import socket
import time

import numpy as np

from central.preview_server import PreviewServer
from protocol import rvl
from protocol.frame import Frame, encode_calib


class _CapturingTracker:
    """Minimal stand-in for a ball tracker: records every fed cloud and exposes
    the `.last` / `.tracks` surface `_feed_calibration` touches for a fine pass."""
    def __init__(self):
        self.fed = []
        self.last = {}
        self.tracks = {}

    def add(self, sensor_id, t_seconds, xyz):
        p = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
        self.fed.append(p.copy())
        self.last[sensor_id] = (p.mean(axis=0), 0.001, p.shape[0])
        return "ok"

    def counts(self):
        return {}


def _frame(sid, fid, w, h, seed):
    rng = np.random.RandomState(seed)
    depth = (1000 + rng.randint(0, 800, size=(h, w))).astype(np.uint16)
    return Frame(sensor_id=sid, frame_id=fid, width=w, height=h,
                 timestamp_ns=fid, depth=rvl.compress(depth), color=b"",
                 depth_rvl=True, color_aligned=False, stride=1).encode()


def _fed_cloud(rig):
    """Push one frame through the relay reader with a fine session active and the
    given per-sensor rig, returning the cloud handed to the tracker."""
    w, h = 24, 24
    server = PreviewServer(rig_calib="", workers=1)   # workers=1 => sequential
    server._broadcast = lambda sid, payload: None
    if rig is not None:
        server._rig[0] = rig
    tracker = _CapturingTracker()
    server._calib_session = {
        "tier": "fine", "tracker": tracker, "deadline": time.time() + 1e6,
        "ball_radius": 0.1, "min_pairs": 6, "mode": "stationary",
        "status_every": 0.25, "target_captures": 14}

    srv, cli = socket.socketpair()
    cli.sendall(encode_calib(0, w, h, 40.0, 40.0, w / 2.0, h / 2.0))
    cli.sendall(_frame(0, 0, w, h, seed=7))
    cli.shutdown(socket.SHUT_WR)
    server._serve_node(srv, ("test", 0))
    cli.close()
    assert tracker.fed, "the tracker was never fed a cloud"
    return tracker.fed[-1]


def test_calibration_fed_raw_cloud():
    t = np.array([1.0, -0.5, 0.25])          # a pure translation "rough" rig
    raw = _fed_cloud(rig=None)
    with_rig = _fed_cloud(rig=(np.eye(3), t))

    assert raw.shape == with_rig.shape
    # The wire cloud WOULD be shifted by t; the CALIBRATION feed must not be.
    shift = float(np.abs(with_rig.mean(axis=0) - raw.mean(axis=0)).max())
    assert shift < 1e-9, (
        "calibration was fed a rig-transformed cloud (shift=%.4f m) — it must "
        "get the RAW cloud so solve_rig computes the full transform" % shift)
    assert np.allclose(raw, with_rig), \
        "fed cloud changed with the rig — not raw"


if __name__ == "__main__":
    test_calibration_fed_raw_cloud()
    print("calibration raw-feed OK")
