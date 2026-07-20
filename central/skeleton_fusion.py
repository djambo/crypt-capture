"""
Cross-sensor skeleton fusion (docs/skeleton_pose.md).

Each node's pose pipeline yields an INDEPENDENT noisy skeleton — per-sensor
2D keypoints lifted to metric 3D at the relay and rig-transformed into the
shared world frame (`preview_server._on_pose`). Before this module they were
only broadcast side by side, so 3 cameras drew 3 jittery skeletons instead of
one better one. The fuser merges the freshest per-sensor world-frame joints
into ONE skeleton, which kills the failure modes no single camera can detect:

  - flying joints: a keypoint whose depth pixel landed on the BACKGROUND
    unprojects metres off-body with healthy confidence. With >=3 estimates the
    outlier is metres from the per-joint median and is dropped outright; with
    2 the higher-confidence estimate wins when they disagree.
  - view-dependent garbage: the camera looking at the subject's back reports
    plausible-confidence nonsense for wrists/face. Confidence-weighted
    averaging lets the camera with the frontal view dominate each joint.
  - occlusion dropouts: a joint hidden from one camera usually exists in
    another, so the fused skeleton has near-zero dropouts.

CRITICAL correctness gate — registration: per-sensor joints only share a frame
once the rig calibration is applied. On an UNCALIBRATED multi-camera rig each
sensor's "world" is its own view frame, and merging across those frames would
produce a garbage skeleton snapping between camera frames. So a sensor is
tagged `registered` by the caller (rig transform loaded for it), and:

  - exactly one fresh sensor        -> passthrough (single-camera rigs always
                                       get a fused stream, calibrated or not);
  - several fresh, some registered  -> fuse the REGISTERED ones only;
  - several fresh, none registered  -> no fused output (the viewer falls back
                                       to the per-sensor skeletons).

Pure Python + math (a skeleton is ~17 tiny tuples — numpy would be overhead),
stateless beyond the per-sensor latest-sample slots, relay-only.
"""

import math
import threading

# A sensor's pose sample participates in fusion while younger than this.
# Pose inference runs at ~7-20 Hz per sensor, so 0.4 s keeps every live
# sensor in the pool while a person who left one camera's gate ages out fast.
DEFAULT_WINDOW_S = 0.4
# Estimates farther than this from the per-joint consensus are outliers
# (flying joints land metres away; rough-tier registration error is ~5-10 cm,
# so 0.25 m tolerates an imperfect rig without admitting depth-lift fliers).
DEFAULT_OUTLIER_M = 0.25
# Estimates below this confidence never participate.
DEFAULT_MIN_CONF = 0.05


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                     + (a[2] - b[2]) ** 2)


def _median(vals):
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _weighted_mean(estimates):
    """estimates: [((x,y,z), conf)] -> confidence-weighted mean position."""
    wsum = sum(max(1e-6, c) for _, c in estimates)
    x = sum(p[0] * max(1e-6, c) for p, c in estimates) / wsum
    y = sum(p[1] * max(1e-6, c) for p, c in estimates) / wsum
    z = sum(p[2] * max(1e-6, c) for p, c in estimates) / wsum
    return (x, y, z)


def fuse_joint(estimates, outlier_m=DEFAULT_OUTLIER_M):
    """Merge one joint's per-sensor estimates [((x,y,z), conf)] into a single
    ((x,y,z), conf). Robust consensus:

      1 estimate  -> passthrough.
      2 estimates -> agree within outlier_m: confidence-weighted mean;
                     disagree: the higher-confidence one wins (no way to know
                     which is the flier — trust the model's own signal).
      >=3         -> component-wise median is the consensus centre; estimates
                     farther than outlier_m from it are dropped (the flying-
                     joint kill), survivors confidence-weighted-averaged.

    Fused confidence = the best surviving confidence (agreement across
    cameras never *reduces* trust in the best view's estimate).
    """
    if not estimates:
        return None
    if len(estimates) == 1:
        return estimates[0]
    if len(estimates) == 2:
        (p0, c0), (p1, c1) = estimates
        if _dist(p0, p1) > outlier_m:
            return (p0, c0) if c0 >= c1 else (p1, c1)
        return (_weighted_mean(estimates), max(c0, c1))
    centre = (_median([p[0] for p, _ in estimates]),
              _median([p[1] for p, _ in estimates]),
              _median([p[2] for p, _ in estimates]))
    kept = [(p, c) for p, c in estimates if _dist(p, centre) <= outlier_m]
    if not kept:                       # pathological total disagreement
        return max(estimates, key=lambda e: e[1])
    return (_weighted_mean(kept), max(c for _, c in kept))


class SkeletonFuser(object):
    """Keeps each sensor's freshest world-frame skeleton sample and fuses
    the fresh set on demand. add() is called from the relay's _on_pose (one
    thread per node connection — a lock guards the tiny shared dict)."""

    def __init__(self, window_s=DEFAULT_WINDOW_S,
                 outlier_m=DEFAULT_OUTLIER_M, min_conf=DEFAULT_MIN_CONF):
        self.window_s = float(window_s)
        self.outlier_m = float(outlier_m)
        self.min_conf = float(min_conf)
        self._latest = {}   # sid -> (t, registered, {jid: ((x,y,z), conf)})
        self.last_sensors = 0          # sensors contributing to the last fuse
        self._lock = threading.Lock()

    def add(self, sid, t, joints, registered):
        """Record sensor `sid`'s sample ([(jid, (x,y,z), conf)], world frame,
        `registered` = rig transform applied) and return the fused skeleton
        {jid: ((x,y,z), conf)}, or None when fusion is not possible (multiple
        fresh sensors, none registered)."""
        rec = (float(t), bool(registered),
               dict((int(j), (p, float(c))) for j, p, c in joints))
        with self._lock:
            self._latest[sid] = rec
            return self._fuse(t)

    def drop_sensor(self, sid):
        with self._lock:
            self._latest.pop(sid, None)

    def _fuse(self, now):
        fresh = dict((sid, rec) for sid, rec in self._latest.items()
                     if now - rec[0] <= self.window_s)
        if not fresh:
            self.last_sensors = 0
            return None
        if len(fresh) > 1:
            registered = dict((sid, rec) for sid, rec in fresh.items()
                              if rec[1])
            if registered:
                fresh = registered
            else:
                # Several cameras, no shared frame: fusing would mix
                # incompatible coordinate frames. Emit nothing.
                self.last_sensors = 0
                return None
        out = {}
        jids = set()
        for _, _, joints in fresh.values():
            jids.update(joints.keys())
        for jid in jids:
            estimates = [joints[jid] for _, _, joints in fresh.values()
                         if jid in joints and joints[jid][1] >= self.min_conf]
            fused = fuse_joint(estimates, self.outlier_m)
            if fused is not None:
                out[jid] = fused
        self.last_sensors = len(fresh)
        return out or None
