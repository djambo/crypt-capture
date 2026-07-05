# Kinect data quality — relay post-processing catalog

> Living reference, not a build plan: a catalog of relay-side improvements to
> depth/color/geometry quality, to come back to and pick from. Ideas here span
> "an afternoon" to "the TSDF fusion milestone already on the roadmap" — this
> doc exists so none of them get lost between sessions, not to force an order.
> Update the status marker in place as items move; keep the newest thinking
> here rather than a change log (unlike `docs/crypt_viewer_updates.md`).

## The architectural fact that makes all of this worth doing here

`central/preview_server.py`'s scene recorder tees the **exact bytes** already
broadcast to viewers (`_recorder.add_frame(out)` runs right after
`_broadcast`, see `docs/preview_protocol.md` "Scene recording"). So anything
in this doc that improves the relay's output improves **live viewing and
every future recording simultaneously**, for free — there is no separate
"clean it up for recording" step to build. That's the throughline; each entry
below assumes it.

Status markers: ✅ done · 🧪 experimental (branch, not merged) · 💡 idea only.

## Per-sensor cleanup (single camera, before or independent of fusion)

### ✅ Temporal denoise (done, merged to main — still opt-in)
`central/temporal_denoise.py` (see this repo's CLAUDE.md "Current status"
entry for full detail). Per-pixel One-Euro low-pass over
the raw depth grid, applied right after RVL decode and before unprojection:
kills the ToF's per-pixel jitter ("every point is vibrating", worst in VR)
while staying responsive to real motion. Opt-in via `--temporal-denoise`;
defaults (`min_cutoff=1.0`, `beta=0.01`) are a first estimate pending
eyes-on tuning against a real (not simulated) noisy Kinect.

### 💡 Spatial (within-frame) depth smoothing
An edge-preserving bilateral filter across NEIGHBOURING pixels in one frame —
complements the temporal filter above (that one denoises one pixel *over
time*; this denoises *across pixels* within a single frame), keyed off the
local depth gradient so it doesn't blur across real silhouette edges. The
`crypt` viewer's `MeshCloud` already does something in this spirit
client-side (its edge-preserving Laplacian `smooth`, default 0.5, over grid
neighbours — see that repo's CLAUDE.md) but only for the **mesh** render;
doing it at the relay would denoise the **point** render too, and benefit
every viewer instead of duplicating the work per client.

### 💡 Flying-pixel / edge-artifact removal
Classic ToF artifact: depth "mixes" between foreground and background right
at silhouette boundaries, producing a thin sheet of phantom points bridging
the subject to whatever's behind them. Detectable cheaply from local
depth-gradient magnitude on the grid — the same spirit as `MeshCloud`'s
`maxEdge` triangulation cut (crypt repo), but applied to CULL POINTS, not
just to decide which triangles to skip, so the point render benefits too
(today a flying-pixel sheet still shows up as points even in point mode,
mesh mode already cuts it via `maxEdge`).

### 💡 Per-point surface normals from the depth gradient
Not a cleanup by itself, but foundational: `dz/du, dz/dv` on the grid gives a
cheap per-point normal with no PCA-over-neighbours needed. Several entries
below (view-angle weighting, carving, any future lighting/shading) want this
as an input; worth building once and sharing.

## Multi-sensor / seam quality (the "make the subject look whole" cluster)

Ordered cheapest-to-most-correct; each is a real technique on its own, not a
prerequisite chain — pick the cost/benefit point that matches the current
need.

### 💡 Cross-sensor outlier removal
Each sensor's node-side speckle filter (`node/background.denoise_mask`,
`min_neighbors`/`set_denoise`) only sees its OWN points, so it can't catch a
point that floats inside/behind the subject because two sensors disagree
slightly after rig registration. A statistical/radius outlier pass on the
**merged world-space cloud** (coarse voxel hash for neighbour counting — no
need for a full KD-tree at these point counts) catches exactly that "ghost
shell" look. Cheapest real win for a multi-sensor rig; independent of
everything else in this section.

### 💡 Photometric (color) harmonization across sensors
Worth calling out on its own because a seam can look wrong with **perfect**
geometry: each Kinect's color camera auto-exposes/white-balances
independently, so a torso split across two cameras can show a visible color
step even when the depth is registered to the millimetre. A per-sensor
gain/white-balance match (calibrated once, or continuously matched using
whatever overlap region view-angle weighting below identifies) removes a
"seam" that is actually a color problem, not a geometry one — cheap, and
easy to misdiagnose as a calibration issue.

### 💡 View-angle-weighted overlap blending
Where two sensors both observe the same patch (a classic seam zone — the
side of a torso, say), keep both cameras' points but weight opacity/color by
how perpendicular the local surface normal is to that camera's view ray
(needs the per-point normals above). A grazing-angle observation is always
noisier; this removes the "double surface" shimmer at seams without
discarding either camera's data outright.

### 💡 Occupancy carving
The sharper version of the above: once two sensors are in the same
registered world frame, if sensor B's own depth measurement says empty space
where sensor A placed a point, sensor B's ray "carves away" A's point. A
lightweight, real-time-feasible precursor to full volumetric fusion — this
is probably the single biggest lever for "why does the subject look like N
overlapping shells" before committing to TSDF below.

### 💡 Continuous micro-recalibration (drift correction)
Already flagged as a stack-on in `docs/rig_calibration.md` ("Later polish:
per-pair ICP fine refinement on overlapping static environment; joint
(bundle) solve over all pairs instead of star-to-ref") — restated here
because it belongs in this catalog too: the wand pass gives a static
`rig_calib.json`, but thermal drift/vibration over a multi-hour session can
open the seam back up. A low-rate point-to-plane ICP between overlapping
sensor pairs, using the live subject cloud itself for correspondences,
nudging the existing rig transform — the rig quietly recalibrates itself
during a session instead of needing a re-wand.

### ✅ (planned) TSDF volumetric fusion — "approach B" on the roadmap
The real fix, and correctly the biggest lift: fuse all N unprojected clouds
into one signed-distance volume per frame (Open3D or a custom voxel hash),
marching-cubes out a single watertight mesh. Dissolves seams **by
construction** — not "N point sets glued together" but one continuous
surface. Trade-offs already noted in the main `CLAUDE.md`: real per-frame
compute cost (voxel resolution vs. fps) and variable mesh topology
frame-to-frame (not VAT-able without the further-out SMPL-X template-tracking
step, "approach C"). Don't reach for this before the cheaper entries above
have been tried — it's the correct end state, not the correct first step.

## Recording-specific (the archival path has no real-time constraint)

### 💡 Heavier offline "bake" pass for saved takes
A completed recording has no fps budget. The recorder could kick off an
async re-processing job on a finished take — a full TSDF fusion or a proper
Poisson reconstruction — producing a "high quality" archival version while
the live/preview stream stays light in real time. Matches the two-tier
live-vs-archival framing already in the main `CLAUDE.md` (M3's node-local
full-fidelity recording is separately planned); this would be the *relay's*
version of the same idea, downstream of whatever preview-resolution
`central/recording.py` already saved.

## If picking a next step (informal priority read, not a decision)

Cheapest visible wins: **cross-sensor outlier removal** + **photometric
harmonization** — both contained, both independent of the bigger fusion
architecture, both directly answer "does the subject look whole". Next
tier: **view-angle blending** and **occupancy carving** are the right next
real investment before committing to full TSDF. **TSDF fusion** stays the
correct long-run answer and is already scoped as its own roadmap milestone —
treat entries in this doc as candidates to de-risk or substitute for it
where a lighter technique gets "good enough" sooner.

## Keep this file current

Same convention as the rest of this repo's docs: when an idea here gets
built, flip its marker to ✅ and point at the branch/module/tests, the way
the temporal-denoise entry above does — don't let this list silently drift
out of sync with what's actually shipped.
