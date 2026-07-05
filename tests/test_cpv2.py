"""CPV2 compact wire format: quantised uint16 positions + valid-mask bitmap grid.

Validates that CPV2 carries the SAME point cloud as CPV1 to within a
quantisation step that is provably far below the Kinect's ToF noise, that the
bitmap reconstructs the exact CPV1 grid-index list (so the viewer's mesh is
unchanged), and that the payload is ~half the size. The parser here is the
reference the crypt viewer's CPV2 decoder must match (docs/preview_protocol.md).

Run: python3 -m tests.test_cpv2
"""
import struct

import numpy as np

from central import preview_server as ps
from central.preview_server import (
    build_message, _quantize_positions, CPV2_QUANT_LEVELS,
    FLAG_RGB, FLAG_GRAVITY, FLAG_GRID)

_HEADER = struct.Struct("<4sIIII")
_QUANT = struct.Struct("<ffff")


def parse_cpv2(buf):
    """Reference CPV2 decoder (what the viewer implements). Returns a dict with
    dequantised float32 positions, rgb, gravity, and the reconstructed ascending
    grid-index list (grid_w, grid_h, indices) — matching CPV1's grid semantics."""
    magic, flags, sensor, frame, count = _HEADER.unpack_from(buf, 0)
    assert magic == b"CPV2", magic
    off = _HEADER.size
    ox, oy, oz, scale = _QUANT.unpack_from(buf, off)
    off += _QUANT.size
    q = np.frombuffer(buf, dtype="<u2", count=count * 3, offset=off
                      ).reshape(count, 3).astype(np.float32)
    off += count * 3 * 2
    xyz = q * scale + np.array([ox, oy, oz], dtype=np.float32)
    rgb = None
    if flags & FLAG_RGB:
        rgb = np.frombuffer(buf, dtype=np.uint8, count=count * 3, offset=off
                            ).reshape(count, 3).copy()
        off += count * 3
    gravity = None
    if flags & FLAG_GRAVITY:
        gravity = np.frombuffer(buf, dtype="<f4", count=3, offset=off).copy()
        off += 12
    grid = None
    if flags & FLAG_GRID:
        grid_w, grid_h = struct.unpack_from("<HH", buf, off)
        off += 4
        nbytes = (grid_w * grid_h + 7) // 8
        packed = np.frombuffer(buf, dtype=np.uint8, count=nbytes, offset=off)
        mask = np.unpackbits(packed, bitorder="little")[:grid_w * grid_h]
        indices = np.flatnonzero(mask).astype(np.uint32)
        grid = (grid_w, grid_h, indices)
    return dict(sensor=sensor, frame=frame, count=count, xyz=xyz, rgb=rgb,
                gravity=gravity, grid=grid)


def _sample_cloud(n=5000, span=8.0, seed=1):
    rng = np.random.default_rng(seed)
    xyz = (rng.random((n, 3), dtype=np.float32) - 0.5) * span   # ~±span/2
    rgb = rng.integers(0, 256, size=(n, 3), dtype=np.uint8)
    # a plausible sub-grid the points came from (large enough to hold n cells)
    side = int(np.ceil(np.sqrt(n * 2.0)))
    grid_w, grid_h = side, side
    idx = np.sort(rng.choice(grid_w * grid_h, size=n, replace=False)
                  ).astype(np.uint32)
    return xyz, rgb, (grid_w, grid_h, idx)


def test_quant_step_below_noise():
    # The guarantee: quantum = span/65535, far below Kinect random noise.
    for span, limit_mm in ((2.0, 0.05), (8.0, 0.2)):
        xyz = np.array([[0, 0, 0], [span, span, span]], dtype=np.float32)
        _, scale, _ = _quantize_positions(xyz)
        step_mm = float(scale) * 1000.0
        assert step_mm < limit_mm, (span, step_mm)
        # worst-case round error is half a step, and Kinect best-case random
        # noise is ~2 mm — assert a >=10x margin.
        assert (step_mm / 2.0) < 2.0 / 10.0, step_mm
    print("ok: quant step below the ToF noise floor "
          "(2 m -> %.3f mm, 8 m -> %.3f mm)" % (
              _quantize_positions(np.array([[0, 0, 0], [2, 2, 2]], np.float32)
                                  )[1] * 1000,
              _quantize_positions(np.array([[0, 0, 0], [8, 8, 8]], np.float32)
                                  )[1] * 1000))


def test_roundtrip_matches_cpv1_within_quantum():
    xyz, rgb, grid = _sample_cloud(span=8.0)
    gravity = np.array([0.0, -1.0, 0.0], dtype=np.float32)
    msg = build_message(3, 42, xyz, rgb, gravity, grid, fmt="cpv2")
    got = parse_cpv2(msg)
    assert got["sensor"] == 3 and got["frame"] == 42 and got["count"] == len(xyz)
    # positions recovered to within one quantum
    span = float((xyz.max(0) - xyz.min(0)).max())
    quantum = span / CPV2_QUANT_LEVELS
    err = np.abs(got["xyz"] - xyz).max()
    assert err <= quantum, (err, quantum)
    assert err < 0.2e-3, err                  # < 0.2 mm absolute, always
    # rgb + gravity exact
    assert np.array_equal(got["rgb"], rgb)
    assert np.allclose(got["gravity"], gravity, atol=1e-6)
    # bitmap reconstructs the EXACT CPV1 index list -> identical mesh
    assert np.array_equal(got["grid"][2], grid[2])
    assert got["grid"][0] == grid[0] and got["grid"][1] == grid[1]
    print("ok: CPV2 round-trip matches CPV1 within one quantum "
          "(max err %.4f mm), grid indices identical" % (err * 1000))


def test_bitmap_matches_unproject_grid():
    # End-to-end: run the real unproject, encode CPV2, and confirm the decoded
    # grid indices equal what CPV1 would have sent (unproject's own output).
    w, h = 64, 48
    depth = np.zeros((h, w), dtype=np.uint16)
    depth[10:40, 12:50] = 1500
    depth[20, 30] = 0                          # a hole inside the blob
    rx, ry = ps.compute_ray_table(w, h, 40.0, 40.0, w / 2, h / 2, (0.0,) * 8)
    xyz, rgb, grid = ps.unproject(depth.tobytes(), w, h, rx, ry, 1, 1,
                                  None, None, None)
    msg = build_message(0, 0, xyz, None, None, grid, fmt="cpv2")
    got = parse_cpv2(msg)
    assert np.array_equal(got["grid"][2], grid[2])
    assert got["count"] == len(grid[2])
    print("ok: CPV2 bitmap grid == CPV1 unproject indices (%d pts)"
          % got["count"])


def test_size_reduction():
    xyz, rgb, grid = _sample_cloud(n=50000, span=8.0)
    v1 = build_message(0, 0, xyz, rgb, None, grid, fmt="cpv1")
    v2 = build_message(0, 0, xyz, rgb, None, grid, fmt="cpv2")
    ratio = len(v2) / len(v1)
    # 19 B/pt -> ~9 B/pt + small fixed bitmap; expect well under 60%.
    assert ratio < 0.60, ratio
    print("ok: CPV2 is %.0f%% of CPV1 (%d -> %d bytes, %d pts)" % (
        ratio * 100, len(v1), len(v2), len(xyz)))


def test_empty_frame():
    xyz = np.empty((0, 3), dtype=np.float32)
    msg = build_message(1, 7, xyz, None, None, None, fmt="cpv2")
    got = parse_cpv2(msg)
    assert got["count"] == 0 and got["xyz"].shape == (0, 3)
    print("ok: CPV2 empty frame encodes/decodes cleanly")


if __name__ == "__main__":
    test_quant_step_below_noise()
    test_roundtrip_matches_cpv1_within_quantum()
    test_bitmap_matches_unproject_grid()
    test_size_reduction()
    test_empty_frame()
    print("\nall CPV2 tests passed")
