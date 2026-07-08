"""
CPV3 (browser-GPU-unproject wire, approach A / docs/gpu_unproject.md) tests.

The relay ships DEPTH + a valid-mask bitmap + per-sensor calibration instead of
XYZ; the browser unprojects on the GPU. These tests prove the representation is
LOSSLESS: reconstructing XYZ from the CPV3 frame (depth + bitmap + step) through
the same ray table + rig gives the EXACT points CPV1 emits — so no geometry is
lost by moving unprojection to the client.

Run: python3 -m tests.test_cpv3
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import central.preview_server as ps


def _rays(w, h, fx, fy, cx, cy):
    return ps.compute_ray_table(w, h, fx, fy, cx, cy, (0.0,) * 8)


def _blob_depth(w, h):
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = ((xx - w * 0.5) / (w * 0.25)) ** 2 + ((yy - h * 0.55) / (h * 0.3)) ** 2
    d = np.where(r2 < 1.0, 1200 + 800 * np.sqrt(np.clip(r2, 0, 1)), 0)
    return d.astype(np.uint16)


def _parse_cpv3(buf):
    magic, flags, sensor, frame, count = struct.unpack_from("<4sIIII", buf, 0)
    assert magic == b"CPV3"
    o = 20
    step_u, step_v = struct.unpack_from("<HH", buf, o); o += 4
    depth = np.frombuffer(buf, dtype="<u2", count=count, offset=o).copy(); o += count * 2
    rgb = None
    if flags & ps.FLAG_RGB:
        rgb = np.frombuffer(buf, dtype=np.uint8, count=count * 3, offset=o).reshape(-1, 3).copy()
        o += count * 3
    gravity = None
    if flags & ps.FLAG_GRAVITY:
        gravity = struct.unpack_from("<3f", buf, o); o += 12
    grid_w, grid_h = struct.unpack_from("<HH", buf, o); o += 4
    nbytes = (grid_w * grid_h + 7) // 8
    bits = np.frombuffer(buf, dtype=np.uint8, count=nbytes, offset=o); o += nbytes
    indices = np.flatnonzero(np.unpackbits(bits, bitorder="little")[:grid_w * grid_h])
    texture = None
    if flags & ps.FLAG_TEXTURE:
        fmt, tw, th, tlen = struct.unpack_from("<BHHI", buf, o); o += 9
        texture = {"format": fmt, "width": tw, "height": th,
                   "data": buf[o:o + tlen]}
    return {"count": count, "step_u": step_u, "step_v": step_v, "depth": depth,
            "grid_w": grid_w, "grid_h": grid_h, "indices": indices,
            "rgb": rgb, "gravity": gravity, "texture": texture}


def _reconstruct(f, ray_x, ray_y, rig=None):
    """Mirror the browser's GPU unprojection from a parsed CPV3 frame."""
    u = f["indices"] % f["grid_w"]
    v = f["indices"] // f["grid_w"]
    fu = v * f["step_v"]                     # full-res row
    fc = u * f["step_u"]                     # full-res col
    rx = ray_x[fu, fc]
    ry = ray_y[fu, fc]
    z = f["depth"].astype(np.float32) / 1000.0
    xyz = np.column_stack((rx * z, -ry * z, -z)).astype(np.float32)
    if rig is not None:
        R, t = rig
        xyz = xyz.dot(np.asarray(R).T) + np.asarray(t)
    return xyz


def test_extract_matches_unproject():
    w, h = 64, 48
    rx, ry = _rays(w, h, 40.0, 40.0, w / 2.0, h / 2.0)
    depth = _blob_depth(w, h)
    xyz_ref, _, (gw, gh, idx_ref) = ps.unproject(depth.tobytes(), w, h, rx, ry, 1)
    dvals, gw2, gh2, idx, su, sv, _rgb = ps.extract_depth_grid(depth.tobytes(), w, h, 1)
    assert (gw2, gh2) == (gw, gh) and su == 1 and sv == 1
    assert np.array_equal(idx, idx_ref)
    assert dvals.size == xyz_ref.shape[0]


def test_cpv3_reconstructs_cpv1_points():
    w, h = 128, 96
    rx, ry = _rays(w, h, 90.0, 90.0, w / 2.0, h / 2.0)
    depth = _blob_depth(w, h)
    xyz_ref, _, _ = ps.unproject(depth.tobytes(), w, h, rx, ry, 1)

    dvals, gw, gh, idx, su, sv, _rgb = ps.extract_depth_grid(depth.tobytes(), w, h, 1)
    buf = ps.build_message_v3(2, 9, dvals, gw, gh, idx, su, sv,
                              gravity=(0.0, -1.0, 0.0))
    f = _parse_cpv3(buf)
    assert f["count"] == xyz_ref.shape[0] and f["gravity"][1] == -1.0
    xyz_v3 = _reconstruct(f, rx, ry)
    assert np.allclose(xyz_v3, xyz_ref, atol=1e-4), \
        np.abs(xyz_v3 - xyz_ref).max()


def test_cpv3_reconstructs_with_stride_and_rig():
    # A relay stride > 1 (the browser must map grid cell -> full-res pixel via
    # step) plus a rig transform (applied in _reconstruct, i.e. on the GPU).
    w, h = 200, 150
    rx, ry = _rays(w, h, 120.0, 120.0, w / 2.0, h / 2.0)
    depth = _blob_depth(w, h)
    R = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    t = np.array([0.3, -0.1, 1.2])
    xyz_ref, _, _ = ps.unproject(depth.tobytes(), w, h, rx, ry, 2)
    xyz_ref = xyz_ref.dot(R.T) + t

    dvals, gw, gh, idx, su, sv, _rgb = ps.extract_depth_grid(depth.tobytes(), w, h, 2)
    assert su == 2 and sv == 2
    f = _parse_cpv3(ps.build_message_v3(0, 0, dvals, gw, gh, idx, su, sv))
    xyz_v3 = _reconstruct(f, rx, ry, rig=(R, t))
    assert np.allclose(xyz_v3, xyz_ref, atol=1e-4), \
        np.abs(xyz_v3 - xyz_ref).max()


def test_cpv3_carries_rgb():
    # Per-point rgb rides the CPV3 wire in the SAME order as depth, so the
    # browser colours the point render frame-locked (no texture). It must match
    # the rgb unproject() emits from the same aligned colour grid.
    w, h = 64, 48
    rx, ry = _rays(w, h, 40.0, 40.0, w / 2.0, h / 2.0)
    depth = _blob_depth(w, h)
    # A deterministic colour grid, only meaningful where depth is valid.
    yy, xx = np.mgrid[0:h, 0:w]
    cgrid = np.stack([(xx * 3) & 0xFF, (yy * 5) & 0xFF, (xx + yy) & 0xFF],
                     axis=-1).astype(np.uint8)
    _, rgb_ref, _ = ps.unproject(depth.tobytes(), w, h, rx, ry, 1, color_grid=cgrid)
    dvals, gw, gh, idx, su, sv, rgb = ps.extract_depth_grid(
        depth.tobytes(), w, h, 1, color_grid=cgrid)
    assert np.array_equal(rgb, rgb_ref)          # same order/values as unproject
    f = _parse_cpv3(ps.build_message_v3(1, 3, dvals, gw, gh, idx, su, sv, rgb=rgb))
    assert f["rgb"] is not None and f["rgb"].shape[0] == f["count"]
    assert np.array_equal(f["rgb"], rgb_ref)     # survives the wire, in order
    # And the geometry still reconstructs with the rgb block present.
    xyz_ref, _, _ = ps.unproject(depth.tobytes(), w, h, rx, ry, 1)
    assert np.allclose(_reconstruct(f, rx, ry), xyz_ref, atol=1e-4)


def test_cpv3_size_vs_cpv1():
    w, h = 640, 576
    rx, ry = _rays(w, h, 500.0, 500.0, w / 2.0, h / 2.0)
    depth = _blob_depth(w, h)
    xyz, rgb, grid = ps.unproject(depth.tobytes(), w, h, rx, ry, 1)
    cpv1 = ps.build_message(0, 0, xyz, np.zeros((xyz.shape[0], 3), np.uint8), None, grid)
    dvals, gw, gh, idx, su, sv, _rgb = ps.extract_depth_grid(depth.tobytes(), w, h, 1)
    cpv3 = ps.build_message_v3(0, 0, dvals, gw, gh, idx, su, sv)
    ratio = len(cpv3) / len(cpv1)
    assert ratio < 0.25, ratio            # depth+bitmap ≪ xyz+rgb+u32 grid
    print("  size: cpv1 %d B -> cpv3 %d B (%.0f%% of cpv1, %d pts)"
          % (len(cpv1), len(cpv3), ratio * 100, xyz.shape[0]))


def run():
    test_extract_matches_unproject()
    test_cpv3_reconstructs_cpv1_points()
    test_cpv3_reconstructs_with_stride_and_rig()
    test_cpv3_carries_rgb()
    test_cpv3_size_vs_cpv1()
    print("cpv3 tests: OK")


if __name__ == "__main__":
    run()
