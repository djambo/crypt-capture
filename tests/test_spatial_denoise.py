"""
EXPERIMENTAL spatial (within-frame) depth denoise tests
(central/spatial_denoise.py).

Headless, no camera/hardware. Covers the properties that make this filter
SAFE and USEFUL to run at the relay:
  - it measurably reduces spatial (within-frame) noise on an otherwise-flat
    surface (the actual point of the filter),
  - it is EDGE-PRESERVING: a real depth step (subject/background silhouette)
    is NOT blurred into a smear of phantom mid-depth points,
  - INVALID (zero) neighbours never pull a valid pixel — smoothing averages
    only real measurements,
  - the output's zero/non-zero mask is BYTE-IDENTICAL to the input's every
    frame (aligned_color_grid's RGB pairing depends on this exactly),
  - it is stateless: each frame is filtered on its own, and a frame-shape
    change (camera-mode switch) just works with no reset bookkeeping.

Run: python3 -m tests.test_spatial_denoise
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from central.spatial_denoise import SpatialDepthFilter


def test_reduces_noise_on_flat_surface():
    """A flat wall at a constant true depth with per-pixel gaussian jitter
    (the ToF grain this filter targets) must come out with substantially
    lower spatial variance while its mean stays put."""
    rng = np.random.RandomState(0)
    w, h = 40, 40
    true_depth = 1500.0
    noise_std = 8.0  # mm
    raw = np.clip(true_depth + rng.normal(0, noise_std, (h, w)),
                  1, 65535).astype(np.uint16)

    f = SpatialDepthFilter(radius=2, sigma_depth=30.0)
    out = f.filter(0, raw.tobytes(), w, h).astype(np.float64)

    # Judge the interior only (the padded border sees fewer neighbours).
    raw_std = np.std(raw[2:-2, 2:-2].astype(np.float64))
    filt_std = np.std(out[2:-2, 2:-2])
    assert filt_std < raw_std * 0.6, (raw_std, filt_std)
    # Mean must not drift (no bias introduced).
    assert abs(np.mean(out) - true_depth) < 2.0, np.mean(out)
    print("ok: flat-surface spatial noise reduced %.2fmm -> %.2fmm std" %
          (raw_std, filt_std))


def test_preserves_a_depth_edge():
    """A clean silhouette — left half subject @1200mm, right half wall
    @2500mm — must stay a sharp step. The bilateral range weight excludes
    across-edge neighbours, so no phantom mid-depth (~1850mm) bridge points
    appear at the boundary, and each side keeps its own depth."""
    w, h = 40, 40
    d = np.empty((h, w), dtype=np.uint16)
    d[:, :w // 2] = 1200
    d[:, w // 2:] = 2500

    f = SpatialDepthFilter(radius=2, sigma_depth=30.0)
    out = f.filter(0, d.tobytes(), w, h)

    # No pixel should land in the "smear" band a plain blur would create.
    smear = (out > 1400) & (out < 2300)
    assert not smear.any(), out[smear][:8]
    # Interior of each side keeps its own value exactly (all same-depth
    # neighbours, diff 0 -> just averages the constant).
    assert np.all(out[:, :w // 2 - 2] == 1200), "left side moved"
    assert np.all(out[:, w // 2 + 2:] == 2500), "right side moved"
    print("ok: 1200/2500mm depth edge preserved (no phantom bridge points)")


def test_invalid_neighbours_do_not_pull():
    """Zero pixels are MISSING measurements, not depth 0 — a valid pixel
    surrounded by holes must not be dragged toward 0. Here a lone valid
    pixel with only invalid neighbours must keep exactly its own value."""
    w, h = 5, 5
    d = np.zeros((h, w), dtype=np.uint16)
    d[2, 2] = 1800  # single valid pixel in a sea of holes

    f = SpatialDepthFilter(radius=1, sigma_depth=30.0)
    out = f.filter(0, d.tobytes(), w, h)

    assert out[2, 2] == 1800, out[2, 2]           # self-weight only, unchanged
    assert np.count_nonzero(out) == 1, out         # no holes got filled in
    print("ok: invalid (hole) neighbours excluded — valid pixel unchanged")


def test_mask_is_exactly_preserved():
    """The output's zero/non-zero pattern must match the input's EXACTLY,
    every frame — aligned_color_grid's RGB pairing depends on this."""
    rng = np.random.RandomState(1)
    w, h = 16, 12
    f = SpatialDepthFilter(radius=2, sigma_depth=25.0)
    for i in range(20):
        mask = rng.rand(h, w) > 0.3
        raw = np.where(mask, rng.randint(500, 3000, (h, w)), 0).astype(np.uint16)
        out = f.filter(0, raw.tobytes(), w, h)
        assert np.array_equal(out != 0, raw != 0), (i, out, raw)
    print("ok: valid/invalid mask preserved exactly across 20 random frames")


def test_stateless_shape_change():
    """No per-sensor memory: filtering one shape then a different shape must
    just work (a camera-mode switch changes the depth grid dimensions)."""
    f = SpatialDepthFilter()
    a = f.filter(0, np.full((4, 4), 1000, dtype=np.uint16).tobytes(), 4, 4)
    assert a.shape == (4, 4)
    b = f.filter(0, np.full((6, 8), 2000, dtype=np.uint16).tobytes(), 8, 6)
    assert b.shape == (6, 8)
    # Uniform-depth interior is unchanged (all neighbours equal).
    assert b[3, 4] == 2000, b[3, 4]
    print("ok: stateless across a frame-shape change")


def test_accepts_array_input_from_temporal_filter():
    """In the relay the temporal filter runs first and hands its uint16 array
    straight to this one; .filter must accept a numpy array (buffer protocol),
    not only raw bytes."""
    w, h = 8, 8
    arr = np.full((h, w), 1500, dtype=np.uint16)
    f = SpatialDepthFilter()
    out = f.filter(0, arr, w, h)          # pass the array itself, not .tobytes()
    assert out.shape == (h, w)
    assert out[4, 4] == 1500, out[4, 4]
    print("ok: accepts a numpy uint16 array (temporal filter's output) directly")


if __name__ == "__main__":
    test_reduces_noise_on_flat_surface()
    test_preserves_a_depth_edge()
    test_invalid_neighbours_do_not_pull()
    test_mask_is_exactly_preserved()
    test_stateless_shape_change()
    test_accepts_array_input_from_temporal_filter()
    print("all spatial denoise tests passed")
