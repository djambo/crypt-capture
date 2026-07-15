"""
Node-side textured-mesh crop tests (docs/textured_mesh.md "crop"): the subject
colour-space bbox projection (_subject_color_bbox) and the JPEG downscale
(_downscale_bgra). Headless — kinect_node is imported behind a pyk4a stub (only
the capture path touches the SDK; these helpers are pure NumPy).

The node crops the mesh JPEG to the subject's colour-space bbox so the texture
is subject-proportional (like the point/splat wire) instead of the whole colour
frame — the fix for the mesh being far heavier than points. The crop only has to
CONTAIN the subject (the relay computes the exact per-point UVs and clamps them
into it), so the test checks CONTAINMENT, not pixel-exactness.

Run: python3 -m tests.test_texture_crop
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

if "pyk4a" not in sys.modules:                 # same stub as test_ir
    stub = types.ModuleType("pyk4a")
    stub.PyK4A = type("PyK4A", (), {})
    stub.Config = type("Config", (), {})
    stub.DepthMode = types.SimpleNamespace(
        NFOV_UNBINNED=1, NFOV_2X2BINNED=2, WFOV_2X2BINNED=3, WFOV_UNBINNED=4)
    stub.ColorResolution = types.SimpleNamespace(
        RES_720P=1, RES_1080P=2, RES_1440P=3, RES_1536P=4, RES_2160P=5,
        RES_3072P=6)
    stub.FPS = types.SimpleNamespace(FPS_5=1, FPS_15=2, FPS_30=3)
    stub.ImageFormat = types.SimpleNamespace(COLOR_BGRA32=1)
    stub.WiredSyncMode = types.SimpleNamespace(
        STANDALONE=1, MASTER=2, SUBORDINATE=3)
    stub.CalibrationType = types.SimpleNamespace(ACCEL=1, DEPTH=2, COLOR=3)
    sys.modules["pyk4a"] = stub

from node.kinect_node import _subject_color_bbox, _downscale_bgra


def test_subject_bbox_contains_subject():
    # Colour camera == depth camera (identity extrinsic, no distortion, cw/ch =
    # grid size): the projected UV of depth pixel (u,v) is (u/w, v/h) at any
    # depth, so the subject's pixel block maps to a known normalised rect.
    w, h = 256, 192                            # realistic ratio of pad:subsample
    fx = fy = 200.0
    cx, cy = w / 2.0, h / 2.0
    depth = np.zeros((h, w), dtype=np.uint16)
    depth[40:120, 80:200] = 1000               # a subject block at 1 m
    plate = np.full((h, w), 5000, dtype=np.uint16)   # far empty scene
    cintr = (fx, fy, cx, cy, (0.0,) * 8)
    extr = ((1, 0, 0, 0, 1, 0, 0, 0, 1), (0, 0, 0))
    bb = _subject_color_bbox(depth, plate, 50, (fx, fy, cx, cy), cintr, extr,
                             w, h, sub=6, pad=0.08)
    assert bb is not None
    u0, v0, u1, v1 = bb
    # The crop must CONTAIN the true subject pixel extent (cols 80..200, rows
    # 40..120) — points outside would sample the crop edge. The pad (0.08 of the
    # frame) covers the sub-sampling undershoot (~sub px) at real resolutions.
    assert u0 * w <= 80 and u1 * w >= 200, (u0 * w, u1 * w)
    assert v0 * h <= 40 and v1 * h >= 120, (v0 * h, v1 * h)
    # ...but it must be a real crop, not the whole frame (that's the perf win).
    assert (u1 - u0) < 0.95 or (v1 - v0) < 0.95, bb


def test_subject_bbox_none_without_plate_or_subject():
    w, h = 32, 24
    cintr = (10.0, 10.0, 16.0, 12.0, (0.0,) * 8)
    extr = ((1, 0, 0, 0, 1, 0, 0, 0, 1), (0, 0, 0))
    depth = np.zeros((h, w), dtype=np.uint16)
    depth[5:15, 5:15] = 1000
    # No plate -> crop falls back to the whole frame (None).
    assert _subject_color_bbox(depth, None, 50, (10, 10, 16, 12),
                               cintr, extr, w, h) is None
    # Plate but empty foreground -> None.
    plate = np.full((h, w), 5000, dtype=np.uint16)
    assert _subject_color_bbox(np.zeros((h, w), np.uint16), plate, 50,
                               (10, 10, 16, 12), cintr, extr, w, h) is None


def test_downscale_bgra():
    img = np.zeros((200, 100, 4), dtype=np.uint8)     # h=200, w=100 BGRA
    out = _downscale_bgra(img, 50)                     # longest edge 200 -> 50
    assert out.shape[0] == 50 and out.shape[1] == 25, out.shape
    # No downscale needed / disabled -> unchanged object.
    assert _downscale_bgra(img, 500) is img
    assert _downscale_bgra(img, 0) is img


def run():
    test_subject_bbox_contains_subject()
    test_subject_bbox_none_without_plate_or_subject()
    test_downscale_bgra()
    print("texture crop tests: OK")


if __name__ == "__main__":
    run()
