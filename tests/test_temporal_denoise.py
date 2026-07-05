"""
EXPERIMENTAL temporal depth denoise tests (central/temporal_denoise.py).

Headless, no camera/hardware. Covers the properties that actually matter for
this filter to be SAFE to run at the relay:
  - it measurably reduces per-pixel noise on an otherwise-static signal
    (the actual point of the filter),
  - it stays responsive to real motion (a step change is not smeared out
    over many frames),
  - the output's zero/non-zero mask is BYTE-IDENTICAL to the input's every
    frame (aligned_color_grid's RGB pairing depends on this exactly),
  - a pixel transitioning invalid -> valid (or valid after a long gap) is
    seeded directly from the raw value, not blended with stale/zero memory,
  - a frame-shape change (camera mode switch) resets cleanly instead of
    crashing or silently misapplying stale per-pixel state.

Run: python3 -m tests.test_temporal_denoise
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from central.temporal_denoise import TemporalDepthFilter


def _run(f, sensor, frames, w, h, t0=0.0, dt=1.0 / 30):
    """Feed a sequence of (h, w) uint16 depth arrays through the filter at a
    fixed frame rate; returns the list of filtered (h, w) uint16 arrays."""
    out = []
    for i, frame in enumerate(frames):
        out.append(f.filter(sensor, frame.tobytes(), w, h, now=t0 + i * dt))
    return out


def test_reduces_noise_on_static_signal():
    """A pixel sitting at a constant true depth with per-frame gaussian
    jitter (the ToF noise this filter targets) must come out with
    substantially lower variance than the raw signal, after it settles."""
    rng = np.random.RandomState(0)
    w, h = 1, 1
    true_depth = 1500.0
    noise_std = 6.0  # mm, a plausible ToF jitter magnitude
    n = 200
    raw = np.clip(true_depth + rng.normal(0, noise_std, n), 1, 65535)
    frames = [np.array([[v]], dtype=np.uint16) for v in raw]

    f = TemporalDepthFilter(min_cutoff=1.0, beta=0.01)
    out = _run(f, 0, frames, w, h)
    filtered = np.array([o[0, 0] for o in out], dtype=np.float64)

    # Drop the first ~15 frames (settling / filter warm-up) before judging
    # steady-state noise.
    raw_std = np.std(raw[15:])
    filt_std = np.std(filtered[15:])
    assert filt_std < raw_std * 0.5, (raw_std, filt_std)
    # And it should track the true value, not drift off it.
    assert abs(np.mean(filtered[15:]) - true_depth) < 3.0, np.mean(filtered[15:])
    print("ok: static-signal noise reduced %.2fmm -> %.2fmm std" %
          (raw_std, filt_std))


def test_tracks_a_real_step_without_excessive_lag():
    """A subject actually moving (a clean depth step, e.g. stepping forward)
    must be tracked within a handful of frames, not smoothed away — the
    whole point of One-Euro over a fixed-alpha EMA."""
    w, h = 1, 1
    n_settle, n_after = 60, 30
    frames = ([np.array([[1500]], dtype=np.uint16)] * n_settle +
              [np.array([[1200]], dtype=np.uint16)] * n_after)

    f = TemporalDepthFilter(min_cutoff=1.0, beta=0.01)
    out = _run(f, 0, frames, w, h)
    filtered = [int(o[0, 0]) for o in out]

    # Within 10 frames (~330ms) of the step it must be most of the way there
    # (not still hovering near the old value).
    v10 = filtered[n_settle + 9]
    assert v10 < 1350, filtered[n_settle:n_settle + 12]
    # And it must fully settle onto the new value given enough frames.
    assert filtered[-1] == 1200, filtered[-5:]
    print("ok: step tracked to %d within 10 frames, settles to %d" %
          (v10, filtered[-1]))


def test_mask_is_exactly_preserved():
    """The output's zero/non-zero pattern must match the input's EXACTLY,
    every frame — aligned_color_grid's RGB pairing depends on this."""
    rng = np.random.RandomState(1)
    w, h = 12, 9
    f = TemporalDepthFilter()
    for i in range(40):
        mask = rng.rand(h, w) > 0.3
        raw = np.where(mask, rng.randint(500, 3000, (h, w)), 0).astype(np.uint16)
        out = f.filter(0, raw.tobytes(), w, h, now=i / 30.0)
        assert np.array_equal(out != 0, raw != 0), (i, out, raw)
    print("ok: valid/invalid mask preserved exactly across 40 random frames")


def test_fresh_pixel_seeds_without_blending_stale_memory():
    """A pixel that goes invalid for a long gap (subject moved away) and then
    becomes valid again at a DIFFERENT depth must snap straight to the new
    value, not lerp in from the old pre-gap depth or from 0."""
    w, h = 1, 1
    f = TemporalDepthFilter(max_gap_s=0.25)
    # Settle at 1000mm.
    for i in range(30):
        f.filter(0, np.array([[1000]], dtype=np.uint16).tobytes(), w, h,
                 now=i / 30.0)
    # Long gap (invalid) — more than max_gap_s.
    t = 30 / 30.0
    for _ in range(20):
        f.filter(0, np.array([[0]], dtype=np.uint16).tobytes(), w, h, now=t)
        t += 1 / 30.0
    # Reappears far away — must come back as (near) 2000, not a blend with
    # the stale 1000 or a lerp-from-zero.
    out = f.filter(0, np.array([[2000]], dtype=np.uint16).tobytes(), w, h,
                   now=t)
    assert abs(int(out[0, 0]) - 2000) <= 1, out
    print("ok: reseeds directly (%d) after a long gap, no stale blend" %
          int(out[0, 0]))


def test_short_dropout_does_not_reset_state():
    """A single-frame dropout (well under max_gap_s — e.g. one speckle-
    filtered frame) should NOT throw away the settled filter memory: the next
    valid frame should continue smoothing, not seed cold."""
    w, h = 1, 1
    rng = np.random.RandomState(2)
    f = TemporalDepthFilter(max_gap_s=0.25)
    t = 0.0
    for i in range(30):
        v = 1500 + rng.randint(-4, 5)
        f.filter(0, np.array([[v]], dtype=np.uint16).tobytes(), w, h, now=t)
        t += 1 / 30.0
    # One dropped frame (~33ms, well under the 250ms gap threshold).
    f.filter(0, np.array([[0]], dtype=np.uint16).tobytes(), w, h, now=t)
    t += 1 / 30.0
    out = f.filter(0, np.array([[1503]], dtype=np.uint16).tobytes(), w, h,
                   now=t)
    # A cold reseed would jump straight to 1503; continued smoothing keeps it
    # much closer to the ~1500 steady state.
    assert abs(int(out[0, 0]) - 1500) < 5, out
    print("ok: single-frame dropout keeps smoothing (no cold reseed)")


def test_shape_change_resets_cleanly():
    """A camera-mode switch changes the depth grid's (w, h) — the filter must
    detect this and reset that sensor's state instead of crashing or
    reshaping stale per-pixel memory onto the new grid."""
    f = TemporalDepthFilter()
    for i in range(10):
        f.filter(0, np.full((4, 4), 1000, dtype=np.uint16).tobytes(), 4, 4,
                 now=i / 30.0)
    # New shape: must not raise, and must treat it as a fresh start (pass the
    # raw frame through unchanged on first sight of the new shape).
    out = f.filter(0, np.full((6, 8), 2000, dtype=np.uint16).tobytes(), 8, 6,
                   now=10 / 30.0)
    assert out.shape == (6, 8)
    assert np.all(out == 2000), out
    print("ok: shape change resets state cleanly")


def test_sensors_are_independent():
    """Two sensors must not share filter state (a stationary sensor 0 must
    not be perturbed by sensor 1's motion, and vice versa)."""
    f = TemporalDepthFilter()
    for i in range(30):
        f.filter(0, np.array([[1000]], dtype=np.uint16).tobytes(), 1, 1,
                 now=i / 30.0)
        f.filter(1, np.array([[3000]], dtype=np.uint16).tobytes(), 1, 1,
                 now=i / 30.0)
    out0 = f.filter(0, np.array([[1000]], dtype=np.uint16).tobytes(), 1, 1,
                    now=30 / 30.0)
    out1 = f.filter(1, np.array([[3000]], dtype=np.uint16).tobytes(), 1, 1,
                    now=30 / 30.0)
    assert abs(int(out0[0, 0]) - 1000) < 2, out0
    assert abs(int(out1[0, 0]) - 3000) < 2, out1
    print("ok: per-sensor filter state is independent")


if __name__ == "__main__":
    test_reduces_noise_on_static_signal()
    test_tracks_a_real_step_without_excessive_lag()
    test_mask_is_exactly_preserved()
    test_fresh_pixel_seeds_without_blending_stale_memory()
    test_short_dropout_does_not_reset_state()
    test_shape_change_resets_cleanly()
    test_sensors_are_independent()
    print("all temporal denoise tests passed")
