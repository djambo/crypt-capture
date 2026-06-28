"""
IMU / orientation path tests (headless; numpy required for the relay parts).

Covers the CIMU node->central message round-trip, the optical->view gravity
transform, and the CPV1 gravity block + flag in build_message.

Run: python3 -m tests.test_imu
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import frame


class _FakeSock(object):
    """Minimal recv()-only socket over a fixed byte buffer (for read_message)."""

    def __init__(self, data):
        self.data = data
        self.i = 0

    def recv(self, n):
        chunk = self.data[self.i:self.i + n]
        self.i += len(chunk)
        return chunk


def test_imu_roundtrip():
    raw = frame.encode_imu(2, 0.1, 0.2, 0.3)
    assert raw[:4] == frame.IMU_MAGIC
    kind, payload = frame.read_message(_FakeSock(raw))
    assert kind == "imu", kind
    assert payload["sensor_id"] == 2
    gx, gy, gz = payload["gravity"]
    assert abs(gx - 0.1) < 1e-6 and abs(gy - 0.2) < 1e-6 and abs(gz - 0.3) < 1e-6
    print("imu roundtrip: OK")


def test_dispatch_still_reads_calib():
    # The new IMU branch must not break the existing calib dispatch.
    raw = frame.encode_calib(1, 640, 576, 500.0, 500.0, 320.0, 288.0)
    kind, payload = frame.read_message(_FakeSock(raw))
    assert kind == "calib" and payload["sensor_id"] == 1
    print("calib dispatch intact: OK")


def test_gravity_to_view():
    try:
        from central import preview_server as ps
    except ImportError as exc:
        print("gravity_to_view: skipped (%s)" % exc)
        return

    # A level, upright camera: gravity points straight down in the optical frame
    # (+Y down) -> straight down in the view frame (-Y, since view Y is up).
    v = ps.gravity_to_view((0.0, 1.0, 0.0))
    assert abs(v[0]) < 1e-6 and abs(v[1] + 1.0) < 1e-6 and abs(v[2]) < 1e-6, v

    # Output is always normalized.
    v = ps.gravity_to_view((0.0, 3.0, 4.0))
    n = (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5
    assert abs(n - 1.0) < 1e-6, n
    assert ps.gravity_to_view((0.0, 0.0, 0.0)) is None
    print("gravity_to_view: OK")


def test_build_message_gravity_block():
    try:
        import numpy as np
        from central import preview_server as ps
    except ImportError as exc:
        print("build_message gravity: skipped (%s)" % exc)
        return

    xyz = np.zeros((4, 3), dtype="<f4")
    # No gravity -> no flag, no trailing block.
    plain = ps.build_message(0, 0, xyz)
    flags = struct.unpack_from("<I", plain, 4)[0]
    assert not (flags & ps.FLAG_GRAVITY)
    assert len(plain) == 20 + 4 * 12

    # With gravity -> flag set + a trailing 3×float32 block after the positions.
    g = (0.0, -1.0, 0.0)
    msg = ps.build_message(0, 0, xyz, None, g)
    flags = struct.unpack_from("<I", msg, 4)[0]
    assert flags & ps.FLAG_GRAVITY
    tail = struct.unpack_from("<3f", msg, 20 + 4 * 12)
    assert tuple(round(c, 5) for c in tail) == g, tail
    print("build_message gravity block: OK")


if __name__ == "__main__":
    test_imu_roundtrip()
    test_dispatch_still_reads_calib()
    test_gravity_to_view()
    test_build_message_gravity_block()
    print("\nALL IMU TESTS PASSED")
