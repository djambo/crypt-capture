"""
Background-plate subtraction for a static rig.

The camera is fixed, so we can snapshot the empty scene once and then keep only
pixels that are *closer than the background* — removing floor/walls at any
distance (unlike a blunt depth-range clip) and leaving just whoever walks in.

Capture averages several frames per pixel to beat the ToF depth wobble; a margin
(mm) absorbs the residual per-frame noise so the static background doesn't
flicker back in. NumPy only (runs on the node, which already needs NumPy).
"""

import numpy as np


class BackgroundSubtractor:
    def __init__(self, margin_mm=50):
        self.margin = margin_mm
        self.plate = None          # (H,W) float32 background depth; 0 = unknown
        self._sum = None
        self._cnt = None
        self._remaining = 0

    @property
    def capturing(self):
        return self._remaining > 0

    @property
    def active(self):
        return self.plate is not None

    def start_capture(self, frames):
        """Begin averaging `frames` frames into a new background plate. Disables
        subtraction until the capture completes."""
        self._sum = None
        self._cnt = None
        self._remaining = int(frames)
        self.plate = None

    def clear(self):
        self.plate = None
        self._sum = None
        self._cnt = None
        self._remaining = 0

    def feed(self, depth):
        """Accumulate one frame during capture. Returns True when the plate is
        finalized on this frame."""
        if self._sum is None:
            self._sum = np.zeros(depth.shape, np.float64)
            self._cnt = np.zeros(depth.shape, np.int64)
        valid = depth > 0
        self._sum[valid] += depth[valid]
        self._cnt[valid] += 1
        self._remaining -= 1
        if self._remaining <= 0:
            self.plate = np.where(self._cnt > 0,
                                  self._sum / np.maximum(self._cnt, 1),
                                  0.0).astype(np.float32)
            self._sum = None
            self._cnt = None
            return True
        return False

    def foreground(self, depth):
        """Boolean (H,W) mask: True = keep (closer than background, or background
        unknown). None if no plate yet."""
        return foreground_mask(self.plate, depth, self.margin)


def foreground_mask(plate, depth, margin):
    """Foreground mask against a *snapshotted* plate (see foreground()). Module-
    level so worker threads can pass a plate captured once per frame — a live
    `self.plate` could be clear()ed between accesses mid-computation."""
    if plate is None:
        return None
    return (plate == 0) | (depth.astype(np.float32) < plate - margin)


def denoise_mask(mask, min_neighbors=2):
    """Remove isolated speckles from a boolean foreground mask: drop any kept
    pixel with fewer than `min_neighbors` kept 8-neighbours. The subject is a
    dense blob (interior pixels have 8) so it's untouched; lone ToF-noise pixels
    (0–1 neighbours) disappear. `min_neighbors <= 0` is a no-op."""
    if min_neighbors <= 0:
        return mask
    m = mask.astype(np.uint8)
    c = np.zeros(mask.shape, np.uint16)
    c[1:, :] += m[:-1, :]; c[:-1, :] += m[1:, :]
    c[:, 1:] += m[:, :-1]; c[:, :-1] += m[:, 1:]
    c[1:, 1:] += m[:-1, :-1]; c[:-1, :-1] += m[1:, 1:]
    c[1:, :-1] += m[:-1, 1:]; c[:-1, 1:] += m[1:, :-1]
    return mask & (c >= min_neighbors)
