"""
EXPERIMENTAL — edge-preserving spatial (within-frame) depth smoothing.

The COMPANION to central/temporal_denoise.py. Both attack the Azure Kinect's
ToF depth noise (the "every point is vibrating / the surface is pebbled" look
in the viewer, worst in VR where the eye is close to the surface), but along
DIFFERENT axes:

  - temporal_denoise smooths ONE pixel OVER TIME (across frames). It cannot
    touch a single frame's spatial roughness — a still subject still has a
    grainy, bumpy surface within any one frame.
  - spatial_denoise (this file) smooths ACROSS NEIGHBOURING PIXELS WITHIN ONE
    frame. It flattens that per-frame grain immediately, even on the very
    first frame and even for a subject that never holds still.

Run either, both, or neither — they compose (temporal first cleans each
pixel's time series, then spatial cleans what's left across the surface) and
each is a one-flag relay-side toggle with no node or protocol change.

How it smooths the Kinect signal (and why it doesn't just blur the subject)
---------------------------------------------------------------------------
This is a BILATERAL filter over the depth grid, not a plain blur. A plain
Gaussian blur would average every pixel with its neighbours regardless of
what those neighbours are — which flattens the ToF grain but ALSO rounds off
real edges and, fatally here, drags the subject's silhouette into the
background behind it (foreground and background depths get averaged across
the silhouette into a smear of phantom mid-depth points).

The bilateral filter weights each neighbour by TWO things:
  1. spatial closeness  — a Gaussian on pixel distance (near neighbours count
     more than far ones), the ordinary blur term; and
  2. depth similarity   — a Gaussian on how close the neighbour's depth is to
     the centre pixel's depth (the "range" term, `sigma_depth`, in mm).

The range term is what makes it edge-preserving. ToF jitter is small (a few
mm to ~1-2 cm), so a pixel and its same-surface neighbours differ by only a
little → high depth-similarity weight → they average together → the grain is
smoothed out. But a real silhouette (subject at 1.2 m, wall at 2.5 m) is a
depth jump of hundreds of mm → the depth-similarity weight collapses to
essentially zero → across-edge neighbours are excluded from the average, so
the edge stays crisp and no phantom bridge points are created. In short: it
averages within a surface, never across a depth discontinuity. This is the
same principle as the viewer's `MeshCloud` `maxEdge` triangulation cut and
its edge-preserving Laplacian `smooth`, but done ONCE at the relay over the
raw depth grid so the POINT render benefits too (not only the mesh) and every
viewer / every future recording gets it for free, instead of each client
re-deriving it.

Doing it in DEPTH-GRID space (here, before unprojection) rather than on the
flat 3D point list is what makes "neighbour" meaningful and cheap: grid pixel
(u, v) and (u+1, v) are adjacent camera rays by construction, so neighbours
are a fixed array shift — no per-point KD-tree, no radius search. The flat
XYZ list has thrown that adjacency away.

Statelessness (contrast with temporal_denoise): this filter reads only the
current frame, so — unlike the temporal One-Euro — it keeps NO per-sensor
memory, needs no dt/reseed/gap logic, and a camera-mode / resolution switch
"just works" (the next frame is filtered on its own). `sensor_id` is accepted
only to keep the call site identical to TemporalDepthFilter.filter().

Critical invariant this file MUST preserve (identical to temporal_denoise):
the OUTPUT's zero/non-zero mask is BYTE-IDENTICAL to the INPUT's.
`central/preview_server.py`'s `aligned_color_grid` pairs the node's
foreground RGB payload with depth pixels via `valid = depth != 0`; if
filtering changed which pixels are zero, colour would silently misalign or
drop every frame. Two things guarantee it here: (a) INVALID (zero) neighbours
are excluded from every average — they are missing measurements, not depth 0,
so smoothing must never pull a real pixel toward them; and (b) the output is
forced to exactly 0 wherever the RAW input was 0, and every valid pixel keeps
at least its own (self-weighted) contribution so it can never collapse to 0.
Covered directly by tests/test_spatial_denoise.py.

Performance: cost is proportional to the number of GRID PIXELS SCANNED, not
the number of valid points, and the filter runs inline in the node handler
thread, so every millisecond it adds directly costs relay fps. Two things
keep that in check: (1) the range weight is computed in-place with reused
scratch buffers (no per-offset allocation on the full grid every frame), and
(2) the filter crops to the bounding box of valid pixels first — so a
BACKGROUND-SUBTRACTED subject (a small blob in a big grid, the mode this
denoise is really for) is filtered in ~3 ms at 1280x720 radius=1, essentially
free. The costly case is a FULL, un-subtracted environment frame (every pixel
valid → the whole grid is scanned): ~13 ms at 640x576 and ~45 ms at 1280x720
for radius=1 (radius=2 is ~2.3x that). If you're watching the full room and
fps drops, the fixes in order: turn on background subtraction (biggest win by
far — it's what makes the subject cheap AND is the intended use), keep
radius=1, drop to a lower depth FOV mode, or leave the filter off for the
setup/environment view and only enable it once subtraction is on.

Status: EXPERIMENTAL, opt-in via `preview_server.py --spatial-denoise`
(default OFF). Relay-only — no node or protocol change, so it toggles by
restarting the relay on the laptop while the Jetson nodes keep running
untouched. Defaults (`radius=1` → 3x3 window, `sigma_depth=30 mm`) are a
first estimate (no real noisy-Kinect data was available to tune against
here) — expect to retune by eye on hardware: raise `--spatial-radius` /
lower nothing for MORE smoothing, lower `--spatial-sigma-depth` to protect
finer edges (at the cost of less smoothing near them).
"""

import numpy as np


class SpatialDepthFilter:
    """Edge-preserving bilateral smoothing of one depth grid (millimetres).

    radius: half-window size in pixels. 1 = a 3x3 neighbourhood (cheapest,
      the default), 2 = 5x5 (stronger smoothing, ~2.8x the work). The window
      is (2*radius+1)^2 pixels.
    sigma_space: Gaussian falloff of the spatial (distance) weight, in pixels.
      Defaults to `radius` so the window edge sits at ~1 sigma. Larger =
      neighbours farther out still count (more smoothing).
    sigma_depth: Gaussian falloff of the depth-similarity weight, in
      millimetres — the edge-preservation knob. A neighbour whose depth
      differs from the centre by more than ~2-3x this is effectively
      excluded. LOWER protects finer/closer depth steps (but smooths less
      right next to them); HIGHER smooths harder but will start rounding
      shallow real steps. ToF jitter (~a few mm) sits well inside it; a
      subject/background silhouette (hundreds of mm) sits well outside.
    """

    def __init__(self, radius=1, sigma_space=None, sigma_depth=30.0):
        self.radius = int(radius)
        if self.radius < 1:
            raise ValueError("radius must be >= 1")
        self.sigma_space = float(sigma_space) if sigma_space else float(self.radius)
        self.sigma_depth = float(sigma_depth)
        # Precompute the constant per-offset spatial weights (they don't depend
        # on the frame). Offset (0,0) is the centre pixel's self-weight = 1.
        r = self.radius
        inv2ss2 = 1.0 / (2.0 * self.sigma_space * self.sigma_space)
        self._offsets = []
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                w = float(np.exp(-(dy * dy + dx * dx) * inv2ss2))
                self._offsets.append((dy, dx, w))
        self._inv2sd2 = 1.0 / (2.0 * self.sigma_depth * self.sigma_depth)

    def reset(self, sensor_id=None):
        """No-op — this filter is stateless (kept for call-site symmetry with
        TemporalDepthFilter.reset())."""

    def filter(self, sensor_id, depth_u16, w, h, now=None):
        """depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length
        w*h — exactly what `rvl.decompress()` returns (or what
        TemporalDepthFilter.filter() emits). `sensor_id`/`now` are accepted
        only for a uniform call site; both are unused (stateless). Returns a
        numpy uint16 array of shape (h, w), buffer-compatible with every
        downstream `np.frombuffer(depth, ...)` call site."""
        d_full = np.frombuffer(depth_u16, dtype=np.uint16).reshape(h, w)
        valid_full = d_full != 0
        out = np.zeros((h, w), dtype=np.uint16)
        if not valid_full.any():
            return out                          # nothing to filter (all holes)

        # Crop to the bounding box of valid pixels and filter only that. Every
        # pixel outside the box is invalid (a hole), so it would output 0
        # anyway AND it can never be a kept neighbour of a valid pixel (a valid
        # pixel on the box edge only borders holes outside) — so cropping is
        # EXACT, not an approximation. Huge win on a background-subtracted
        # subject (a small blob in a big grid); a no-op cost on a full frame.
        rows = np.any(valid_full, axis=1)
        cols = np.any(valid_full, axis=0)
        r0, r1 = int(np.argmax(rows)), h - 1 - int(np.argmax(rows[::-1]))
        c0, c1 = int(np.argmax(cols)), w - 1 - int(np.argmax(cols[::-1]))
        d = d_full[r0:r1 + 1, c0:c1 + 1].astype(np.float32)
        valid = valid_full[r0:r1 + 1, c0:c1 + 1]
        ch, cw = d.shape

        r = self.radius
        inv2sd2 = self._inv2sd2
        # Zero-pad by the radius so a neighbour offset is a plain slice; the
        # padded border is invalid (valid=False) so out-of-box neighbours
        # contribute nothing — no wraparound (which np.roll would introduce).
        dp = np.pad(d, r, mode="constant", constant_values=0.0)
        vp = np.pad(valid, r, mode="constant", constant_values=False)

        wsum = np.zeros((ch, cw), dtype=np.float32)  # sum of neighbour weights
        dsum = np.zeros((ch, cw), dtype=np.float32)  # sum of weight*neighbour depth
        wt = np.empty((ch, cw), dtype=np.float32)    # reused scratch per offset
        tmp = np.empty((ch, cw), dtype=np.float32)
        for dy, dx, sw in self._offsets:
            # nd[y,x] = d[y+dy, x+dx], nv likewise for validity (0 where OOB).
            nd = dp[r + dy:r + dy + ch, r + dx:r + dx + cw]
            nv = vp[r + dy:r + dy + ch, r + dx:r + dx + cw]
            # Depth-similarity (range) weight exp(-diff^2/2sigma^2): ~1 for
            # same-surface neighbours, ~0 across a silhouette. All in-place into
            # `wt` (no per-offset temporaries — this loop runs on the full grid
            # every frame, so allocation churn shows up directly as lost fps).
            np.subtract(d, nd, out=wt)
            np.multiply(wt, wt, out=wt)          # diff^2
            np.multiply(wt, -inv2sd2, out=wt)
            np.exp(wt, out=wt)                    # range weight in place
            np.multiply(wt, nv, out=wt)          # drop INVALID neighbours
            if sw != 1.0:
                np.multiply(wt, sw, out=wt)      # spatial (distance) weight
            wsum += wt
            np.multiply(wt, nd, out=tmp)
            dsum += tmp

        # A valid centre always includes its own offset (0,0): sw=1, diff=0 ->
        # rw=1, nv=True -> self-weight 1, so wsum > 0 there and it can never
        # collapse to 0. Invalid centres have wsum 0 and are forced to exactly
        # 0 below -> the input mask is preserved.
        keep = valid & (wsum > 0.0)
        crop = np.divide(dsum, wsum, out=np.zeros((ch, cw), dtype=np.float32),
                         where=keep)
        out[r0:r1 + 1, c0:c1 + 1] = np.clip(np.round(crop), 0, 65535).astype(np.uint16)
        return out
