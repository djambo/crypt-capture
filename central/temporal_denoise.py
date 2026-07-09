"""
EXPERIMENTAL — per-pixel temporal One-Euro filter over the raw depth grid.

Kills the Kinect's per-pixel ToF depth jitter (the "every point is vibrating"
look in the viewer, worst in VR where the eye is much closer to the surface)
by low-pass filtering each pixel's depth ACROSS FRAMES before unprojection.
The same One-Euro filter already smooths skeleton joints (node/pose.py's
`OneEuro`) — vectorized here over the whole depth array instead of one
scalar, and applied to depth instead of pixel-space joints.

Why this is correct and not a smear: a depth pixel (u, v) is the same
physical camera ray every frame (fixed optics), so filtering PER PIXEL,
BEFORE any relay/node stride subsampling and before the point list is
flattened, is filtering the same signal over time — unlike filtering the
flat XYZ point list, where index i is a DIFFERENT physical point every frame
as the valid-pixel set shifts (that would smear unrelated points together).

Why One-Euro and not a plain EMA: it is adaptive — heavy smoothing while a
pixel's depth is nearly static (kills idle ToF shake) and light smoothing
the moment it's changing fast (a real-motion edge stays sharp, no added
lag/smear). A fixed-alpha EMA can't have both.

Critical invariant this file MUST preserve: the OUTPUT's zero/non-zero mask
is BYTE-IDENTICAL to the INPUT's. `central/preview_server.py`'s
`aligned_color_grid` pairs the node's foreground RGB payload with depth
pixels via `valid = depth != 0` — if filtering changed which pixels are
zero, colour would silently misalign (or drop) every frame. Filtered pixels
never round to exactly 0 in practice (Kinect's minimum range is far above
1mm), but the implementation still forces invalid-in -> invalid-out
EXPLICITLY (`np.where(valid, ...)` on the raw input mask) rather than
relying on that, and every test in tests/test_temporal_denoise.py checks
the mask directly.

Status: ON BY DEFAULT (per-pixel over time is a couple of vectorized passes,
negligible fps cost, and only helps). `preview_server.py` runs it
automatically; `--no-temporal-denoise` turns it off (`--temporal-denoise` is
now a no-op kept for compat). Relay-only (central/preview_server.py) — no node
or protocol change, so it can be toggled by restarting the relay on the laptop
while the Jetson nodes keep running untouched. Defaults are a first estimate
(no real noisy-Kinect data was available to tune against here) — expect to
retune --denoise-min-cutoff/--denoise-beta by eye once running on hardware.
"""

import math
import time

import numpy as np


class _SensorState:
    __slots__ = ("shape", "x_hat", "dx_hat", "valid_prev", "last_valid_t", "t_prev")

    def __init__(self, h, w, x0, now):
        self.shape = (h, w)
        self.x_hat = x0.copy()
        self.dx_hat = np.zeros((h, w), dtype=np.float32)
        self.valid_prev = x0 != 0
        # last_valid_t seeded to `now` everywhere (harmless for never-valid
        # pixels: last_valid_t is only ever consulted where the CURRENT frame
        # is valid, via the gap_expired computation below).
        self.last_valid_t = np.full((h, w), now, dtype=np.float64)
        self.t_prev = now


class TemporalDepthFilter:
    """Per-sensor, per-pixel One-Euro low-pass over raw depth (millimetres).

    min_cutoff: baseline smoothing strength when a pixel is static (Hz) —
      LOWER = more smoothing at rest. beta: how fast smoothing backs off as
      a pixel's depth starts changing (per mm/s) — HIGHER = trusts motion
      sooner (less lag, but less denoising on borderline-slow motion).
    d_cutoff: smoothing of the internal velocity estimate itself (Hz);
      the standard One-Euro default, rarely needs tuning.
    max_gap_s: a pixel invalid for longer than this is treated as a FRESH
      target on its next valid frame (direct seed, no blend with the stale
      pre-gap value) instead of continuing the old filter state — a subject
      that steps out of a pixel and back in later shouldn't have its new
      position dragged toward where it used to be.
    """

    def __init__(self, min_cutoff=1.0, beta=0.01, d_cutoff=1.0, max_gap_s=0.25):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.max_gap_s = float(max_gap_s)
        self._state = {}  # sensor_id -> _SensorState

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self, sensor_id=None):
        """Drop filter memory (a sensor's, or all). Call when a sensor's
        camera mode changes (handled automatically via shape mismatch below)
        or on any other event that makes stale state actively wrong."""
        if sensor_id is None:
            self._state.clear()
        else:
            self._state.pop(sensor_id, None)

    def filter(self, sensor_id, depth_u16, w, h, now=None):
        """depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length
        w*h — exactly what `rvl.decompress()` returns. Returns a numpy uint16
        array of the same shape (buffer-compatible with every existing
        `np.frombuffer(depth, ...)` call site downstream)."""
        if now is None:
            now = time.monotonic()
        raw = np.frombuffer(depth_u16, dtype=np.uint16).reshape(h, w)

        state = self._state.get(sensor_id)
        if state is None or state.shape != (h, w):
            # First sighting of this sensor, or its frame shape changed (camera
            # mode / stride switch): nothing to filter against yet — pass the
            # raw frame through and seed memory from it.
            state = _SensorState(h, w, raw.astype(np.float32), now)
            self._state[sensor_id] = state
            return raw.copy()

        dt = max(now - state.t_prev, 1e-4)
        state.t_prev = now

        # All per-pixel work is CROPPED to the valid-pixel bounding box. With
        # background subtraction the subject occupies a small sub-rectangle of
        # the grid, but the filter used to run ~15 full-grid float passes over
        # all w*h pixels every frame — measured ~16 ms/frame on a fast x86
        # REGARDLESS of subject size, the relay reader thread's single biggest
        # serial cost (it capped a 3-sensor rig at ~25 fps in). The crop is
        # exact, not an approximation: an invalid pixel's state is frozen by
        # construction (x_hat/dx_hat/last_valid_t untouched, output 0), so
        # pixels outside the box need no work at all — except valid_prev,
        # which must read False for them next frame (a cheap full-grid memset).
        # A full-room frame's box is the whole grid = the old cost exactly.
        valid_full = raw != 0
        rows = np.flatnonzero(valid_full.any(axis=1))
        if rows.size == 0:                     # empty frame: nothing to filter
            state.valid_prev.fill(False)
            return raw.copy()
        cols = np.flatnonzero(valid_full.any(axis=0))
        box = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))

        x = raw[box].astype(np.float32)
        valid = valid_full[box]
        x_hat_prev = state.x_hat[box]          # views — writes go through below
        dx_hat_prev = state.dx_hat[box]
        last_valid_t = state.last_valid_t[box]

        gap_expired = (now - last_valid_t) > self.max_gap_s
        continue_mask = valid & state.valid_prev[box] & ~gap_expired
        seed_mask = valid & ~continue_mask  # valid now, but not continuing

        # One-Euro update (vectorized over the box; only continue_mask
        # pixels' results are actually used below).
        dx = (x - x_hat_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * dx_hat_prev
        cutoff = self.min_cutoff + self.beta * np.abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1.0 - a) * x_hat_prev

        new_x_hat = np.where(continue_mask, x_hat, x_hat_prev)
        new_dx_hat = np.where(continue_mask, dx_hat, dx_hat_prev)
        # Fresh/reseeded pixels: snap straight to the raw value, zero velocity
        # — no blending with stale (or default-zero) memory.
        new_x_hat = np.where(seed_mask, x, new_x_hat)
        new_dx_hat = np.where(seed_mask, 0.0, new_dx_hat)
        # Currently-invalid pixels touch neither mask above, so np.where already
        # leaves their prior x_hat/dx_hat untouched (frozen memory) — a brief
        # 1-frame dropout (e.g. a speckle-filtered pixel) doesn't reset state,
        # but max_gap_s bounds how long that memory can matter.

        state.x_hat[box] = new_x_hat
        state.dx_hat[box] = new_dx_hat
        state.valid_prev.fill(False)           # invalid everywhere...
        state.valid_prev[box] = valid          # ...except inside the box
        state.last_valid_t[box] = np.where(valid, now, last_valid_t)

        # Force the output mask to EXACTLY match the input mask (see module
        # docstring) regardless of any floating-point edge case above.
        out = np.zeros((h, w), dtype=np.uint16)
        out[box] = np.clip(np.round(np.where(valid, new_x_hat, 0.0)),
                           0, 65535).astype(np.uint16)
        return out
