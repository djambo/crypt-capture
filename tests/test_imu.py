"""
IMU / orientation path tests (headless; numpy required for the relay parts).

Covers the CIMU node->central message round-trip, the optical->view gravity
transform, and the CPV2 gravity block + flag in build_message.

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

    # CPV2 layout: 20B header + 24B bbox (origin+scale) + count×3 uint16.
    xyz = np.zeros((4, 3), dtype="<f4")
    pos_end = 20 + 24 + 4 * 6
    # No gravity -> no flag, no trailing block.
    plain = ps.build_message(0, 0, xyz)
    flags = struct.unpack_from("<I", plain, 4)[0]
    assert not (flags & ps.FLAG_GRAVITY)
    assert plain[:4] == b"CPV2"
    assert len(plain) == pos_end

    # With gravity -> flag set + a trailing 3×float32 block after the positions.
    g = (0.0, -1.0, 0.0)
    msg = ps.build_message(0, 0, xyz, None, g)
    flags = struct.unpack_from("<I", msg, 4)[0]
    assert flags & ps.FLAG_GRAVITY
    tail = struct.unpack_from("<3f", msg, pos_end)
    assert tuple(round(c, 5) for c in tail) == g, tail
    print("build_message gravity block: OK")


def test_build_message_quantization_roundtrip():
    try:
        import numpy as np
        from central import preview_server as ps
    except ImportError as exc:
        print("quantization roundtrip: skipped (%s)" % exc)
        return

    rng = np.random.RandomState(42)
    xyz = (rng.rand(1000, 3).astype(np.float32) * [2.0, 2.0, 3.0]
           + [-1.0, -1.0, -3.5])
    msg = ps.build_message(0, 0, xyz)
    origin = np.frombuffer(msg, dtype="<f4", count=3, offset=20)
    scale = np.frombuffer(msg, dtype="<f4", count=3, offset=32)
    delta = np.frombuffer(msg, dtype="<u2", count=3000, offset=44).reshape(-1, 3)
    # Positions are per-axis uint16 deltas (row 0 absolute); accumulate
    # with the same mod-2^16 wrap the encoder relies on.
    quant = np.cumsum(delta.astype(np.uint64), axis=0) % 65536
    back = origin + quant.astype(np.float32) * scale
    err = np.abs(back - xyz).max()
    # Worst-case per-axis error is scale/2 = bbox/65535/2 (~0.023 mm for a 3 m
    # bbox); allow a hair over for float32 arithmetic.
    assert err <= float(scale.max()) * 0.51 + 1e-7, err
    assert err < 5e-5, err                   # sub-0.05 mm in absolute terms

    # Degenerate axis (all points share a value) must come back exactly.
    flat = xyz.copy()
    flat[:, 1] = 0.25
    msg = ps.build_message(0, 0, flat)
    scale = np.frombuffer(msg, dtype="<f4", count=3, offset=32)
    assert scale[1] == 0.0
    origin = np.frombuffer(msg, dtype="<f4", count=3, offset=20)
    assert abs(origin[1] - 0.25) < 1e-7
    print("quantization roundtrip: OK (max err %.6f mm)" % (err * 1000.0))


if __name__ == "__main__":
    test_imu_roundtrip()
    test_dispatch_still_reads_calib()
    test_gravity_to_view()
    test_build_message_gravity_block()
    test_build_message_quantization_roundtrip()
    print("\nALL IMU TESTS PASSED")
