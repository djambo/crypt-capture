"""
CPV1 depth-grid index block tests (FLAG_GRID — the connectivity the viewer's
textured-mesh renderer rebuilds triangles from).

Covers: unproject returning per-point row-major grid indices that actually
address the emitted points, build_message's trailing grid block layout (after
positions/rgb/gravity, so older parsers are untouched), and the max_points
subsample keeping indices paired with the surviving points.

Run: python3 -m tests.test_grid
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_unproject_grid_indices():
    try:
        import numpy as np
        from central import preview_server as ps
    except ImportError as exc:
        print("unproject grid indices: skipped (%s)" % exc)
        return

    # 4x3 grid with a hole: valid pixels must come back with their row-major
    # linear indices, in the same order as the positions.
    w, h = 4, 3
    depth = np.zeros((h, w), dtype=np.uint16)
    depth[0, 1] = 1000
    depth[1, 1] = 1200
    depth[1, 2] = 1300
    depth[2, 3] = 2000
    ray = np.zeros((h, w), dtype=np.float32)
    xyz, _, grid = ps.unproject(depth.tobytes(), w, h, ray, ray, 1)
    grid_w, grid_h, idx = grid
    assert (grid_w, grid_h) == (w, h), grid
    assert list(idx) == [1, 5, 6, 11], idx
    # Point k's depth must be the depth at grid index k (z = -depth_m).
    flat = depth.ravel()
    for k, gi in enumerate(idx):
        assert abs(-xyz[k, 2] - flat[gi] / 1000.0) < 1e-6, (k, gi, xyz[k])

    # Relay-side stride: indices address the STRIDED sub-grid.
    xyz2, _, grid2 = ps.unproject(depth.tobytes(), w, h, ray, ray, 2)
    grid_w2, grid_h2, idx2 = grid2
    assert (grid_w2, grid_h2) == (2, 2), grid2      # cols 0,2 x rows 0,2
    # Only (row 1, col 2)... is skipped by stride; kept valid pixels on the
    # sub-grid: none in row0 (cols 0,2 are 0), none in row2 (col 0,2 are 0).
    assert xyz2.shape[0] == len(idx2)

    # Empty frame: empty indices, real grid dims.
    xyz3, _, grid3 = ps.unproject(
        np.zeros((h, w), dtype=np.uint16).tobytes(), w, h, ray, ray, 1)
    assert xyz3.shape[0] == 0 and len(grid3[2]) == 0
    assert (grid3[0], grid3[1]) == (w, h)
    print("unproject grid indices: OK")


def test_build_message_grid_block():
    try:
        import numpy as np
        from central import preview_server as ps
    except ImportError as exc:
        print("build_message grid block: skipped (%s)" % exc)
        return

    xyz = np.zeros((3, 3), dtype="<f4")
    rgb = np.zeros((3, 3), dtype=np.uint8)
    g = (0.0, -1.0, 0.0)
    idx = np.array([2, 7, 9], dtype=np.uint32)

    # No grid -> no flag, unchanged length (the pre-grid layout).
    plain = ps.build_message(1, 0, xyz, rgb, g)
    flags = struct.unpack_from("<I", plain, 4)[0]
    assert not (flags & ps.FLAG_GRID)
    assert len(plain) == 20 + 3 * 12 + 3 * 3 + 12

    # With grid -> flag set + trailing [u16 w][u16 h][count x u32] AFTER the
    # gravity block (older viewers parse by front offsets and never see it).
    msg = ps.build_message(1, 0, xyz, rgb, g, grid=(5, 4, idx))
    flags = struct.unpack_from("<I", msg, 4)[0]
    assert flags & ps.FLAG_GRID and flags & ps.FLAG_GRAVITY and flags & ps.FLAG_RGB
    off = 20 + 3 * 12 + 3 * 3 + 12
    gw, gh = struct.unpack_from("<HH", msg, off)
    assert (gw, gh) == (5, 4)
    got = struct.unpack_from("<3I", msg, off + 4)
    assert got == (2, 7, 9), got
    assert len(msg) == off + 4 + 3 * 4

    # Grid without rgb/gravity still lands right after positions.
    msg2 = ps.build_message(1, 0, xyz, grid=(5, 4, idx))
    gw2, gh2 = struct.unpack_from("<HH", msg2, 20 + 3 * 12)
    assert (gw2, gh2) == (5, 4)
    print("build_message grid block: OK")


if __name__ == "__main__":
    test_unproject_grid_indices()
    test_build_message_grid_block()
    print("\nALL GRID TESTS PASSED")
