"""
grid->depth extrinsic registration tests (headless; numpy for the relay parts).

Covers the CEXT node->central message round-trip and that the relay's unproject
applies the rigid transform (P_depth = R·P + t) in optical space before the
optical->view flip — i.e. depth_to_color frames register to the depth frame.

Run: python3 -m tests.test_extrinsic
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import frame


class _FakeSock(object):
    def __init__(self, data):
        self.data = data
        self.i = 0

    def recv(self, n):
        chunk = self.data[self.i:self.i + n]
        self.i += len(chunk)
        return chunk


def test_extrinsic_roundtrip():
    R = (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)   # 90° about Z
    t = (0.01, -0.02, 0.03)
    raw = frame.encode_extrinsic(5, R, t)
    assert raw[:4] == frame.EXTRINSIC_MAGIC
    kind, payload = frame.read_message(_FakeSock(raw))
    assert kind == "extrinsic" and payload["sensor_id"] == 5
    assert all(abs(a - b) < 1e-6 for a, b in zip(payload["R"], R)), payload["R"]
    assert all(abs(a - b) < 1e-6 for a, b in zip(payload["t"], t)), payload["t"]
    print("extrinsic roundtrip: OK")


def test_unproject_applies_extrinsic():
    try:
        import numpy as np
        from central import preview_server as ps
    except ImportError as exc:
        print("unproject extrinsic: skipped (%s)" % exc)
        return

    # One valid pixel straight ahead (zero rays) at 2 m: optical point (0,0,2).
    w = h = 1
    depth = np.array([[2000]], dtype=np.uint16).tobytes()
    ray = np.zeros((1, 1), dtype=np.float32)

    # Identity -> view-frame flip only: (0,0,2)_opt -> (0,-0,-2)_view.
    xyz_id, _ = ps.unproject(depth, w, h, ray, ray, 1)
    assert np.allclose(xyz_id[0], [0, 0, -2], atol=1e-5), xyz_id

    # A 90° rotation about optical X: (0,0,2) -> (0,-2,0) in the depth frame,
    # then the optical->view flip (x,-y,-z) -> (0,2,0). Plus a translation.
    c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
    R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float32)
    t = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    xyz, _ = ps.unproject(depth, w, h, ray, ray, 1, extrinsic=(R, t))
    # P_depth = R·(0,0,2)+t = (0,-2,0)+t = (0.1,-1.8,0.3); view = (x,-y,-z).
    assert np.allclose(xyz[0], [0.1, 1.8, -0.3], atol=1e-4), xyz
    print("unproject applies extrinsic: OK")


if __name__ == "__main__":
    test_extrinsic_roundtrip()
    test_unproject_applies_extrinsic()
    print("\nALL EXTRINSIC TESTS PASSED")
