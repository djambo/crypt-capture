"""
2D human pose on the node's color image -> CPOS keypoints (docs/skeleton_pose.md).

Runs a single-person MoveNet ONNX model (Lightning 192 or Thunder 256,
Apache-2.0) via onnxruntime against the SAME color image that is already
pixel-aligned with the streamed depth grid, reads the depth under each
keypoint, and hands `(joint_id, u, v, z_m, conf)` tuples to the node's sender
(protocol/frame.encode_pose). Central lifts them to metric 3D joints.

Performance contract (the whole point of this file's shape):
  - the capture loop only calls PoseWorker.submit(), which stashes array refs
    and returns immediately — the cloud stream can NEVER wait on pose;
  - the worker infers on the LATEST frame only (freshness beats completeness,
    same rule as the frame pipeline) at whatever rate the model manages;
  - onnxruntime releases the GIL during Run and is capped to a couple of
    intra-op threads, so it coexists with the RVL/color worker processes.

Kept import-safe without onnxruntime (the dependency is only touched when a
model is actually configured) and Python-3.6-safe like all node code.
"""

import math
import os
import threading
import time

import numpy as np

COCO_JOINTS = 17
# Torso joints (shoulders + hips) used by the person gate: MoveNet ALWAYS
# emits 17 keypoints — pointed at furniture it produces low-confidence
# garbage, and a couple of joints can fluke past a per-joint threshold. A
# real person reliably lights up the torso, so the gate is the MEAN torso
# confidence: below it, the whole frame emits nothing (the viewer's stale
# timeout then hides the skeleton).
CORE_JOINTS = (5, 6, 11, 12)
# The "minimal" preset: head + shoulders + wrists + hips — reads as a person,
# tracks the hands (the FX input), and keeps the skeleton uncluttered. The
# model always computes all 17 (subsetting is emit-side, not a speedup).
MINIMAL_JOINTS = (0, 5, 6, 9, 10, 11, 12)


def letterbox(rgb, size):
    """Resize an (H, W, 3) image into a (size, size, 3) letterboxed square
    (nearest-neighbour, pure NumPy — no cv2 dependency on the node).
    Returns (square, scale, pad_x, pad_y); original pixel coords are
    recovered as u = (u_sq - pad_x) / scale, v = (v_sq - pad_y) / scale."""
    h, w = rgb.shape[:2]
    scale = float(size) / float(max(h, w))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    xi = np.minimum((np.arange(nw) / scale).astype(np.int64), w - 1)
    yi = np.minimum((np.arange(nh) / scale).astype(np.int64), h - 1)
    resized = rgb[yi][:, xi]
    pad_x = (size - nw) // 2
    pad_y = (size - nh) // 2
    out = np.zeros((size, size, 3), dtype=rgb.dtype)
    out[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    return out, scale, pad_x, pad_y


def decode_movenet(raw, scale, pad_x, pad_y, size):
    """MoveNet output (17 x (y, x, score), normalized to the letterboxed
    square) -> [(joint_id, u, v, conf)] in ORIGINAL image pixel coords."""
    kp = np.asarray(raw, dtype=np.float64).reshape(COCO_JOINTS, 3)
    out = []
    for jid in range(COCO_JOINTS):
        y, x, conf = kp[jid]
        u = (x * size - pad_x) / scale
        v = (y * size - pad_y) / scale
        out.append((jid, float(u), float(v), float(conf)))
    return out


class OneEuro(object):
    """One-Euro filter (Casiez et al.) for one scalar channel — the standard
    keypoint de-jitterer: heavy smoothing at low speed (kills idle shake),
    light smoothing at high speed (movement stays responsive, minimal lag).
    beta is in 1/(units per second): larger = trusts fast motion sooner."""

    def __init__(self, min_cutoff=0.5, beta=0.01, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._t = None
        self._x = 0.0
        self._dx = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, x, t):
        if self._t is None:
            self._t = t
            self._x = x
            self._dx = 0.0
            return x
        dt = max(1e-6, t - self._t)
        self._t = t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        return self._x


class JointSmoother(object):
    """Per-joint One-Euro smoothing for (u, v) pixels + z metres. A joint
    unseen for `reset_s` drops its filter state, so a re-appearing joint
    snaps to its new position instead of sliding across the frame."""

    RESET_S = 0.5

    def __init__(self):
        self._f = {}                 # joint_id -> (t_last, fu, fv, fz)

    def filter(self, jid, u, v, z, t):
        st = self._f.get(jid)
        if st is None or t - st[0] > self.RESET_S:
            # z is deliberately snappier (min_cutoff 1.0, beta 2.0): metres
            # move at ~1 m/s when walking, and a soft z filter trails the
            # body in DEPTH — the most visible "skeleton chases the cloud"
            # artifact on hardware.
            st = [t, OneEuro(0.5, 0.01, 1.0), OneEuro(0.5, 0.01, 1.0),
                  OneEuro(1.0, 2.0, 1.0)]
            self._f[jid] = st
        st[0] = t
        u = st[1].filter(u, t)
        v = st[2].filter(v, t)
        if z > 0:
            z = st[3].filter(z, t)
        return u, v, z

    def reset(self):
        self._f.clear()


def sample_depth(depth_u16, u, v, half=2):
    """Depth (metres) under a keypoint: median of the non-zero values in a
    (2*half+1)^2 window — robust to the keypoint landing on a masked pixel or
    a depth hole. 0.0 = no depth there (consumers skip the joint)."""
    h, w = depth_u16.shape[:2]
    ui = int(round(u))
    vi = int(round(v))
    if not (0 <= ui < w and 0 <= vi < h):
        return 0.0
    win = depth_u16[max(0, vi - half):vi + half + 1,
                    max(0, ui - half):ui + half + 1]
    vals = win[win > 0]
    if vals.size == 0:
        return 0.0
    return float(np.median(vals)) / 1000.0


class MoveNetEstimator(object):
    """Single-person MoveNet via onnxruntime. Tolerant of the common export
    variants: NHWC [1,S,S,3] or NCHW [1,3,S,S] input, int32/uint8/float
    dtype (all fed 0..255 — the graph does its own normalization), any output
    list containing one 17x3 tensor."""

    def __init__(self, model_path, threads=2, providers=None, trt=False):
        import onnxruntime as ort              # deferred: only with a model
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(threads)  # bound CPU next to RVL workers
        if providers is None:
            avail = ort.get_available_providers()
            if trt and "TensorrtExecutionProvider" in avail:
                # TensorRT EP with a persistent ENGINE CACHE: the first start
                # compiles the model (~1-3 min); every later start loads the
                # cached engine instantly. Use when the CUDA EP underperforms
                # (e.g. graph partitions falling back to CPU per frame).
                cache = os.path.join(
                    os.path.dirname(os.path.abspath(model_path)), "trt_cache")
                try:
                    os.makedirs(cache)
                except OSError:
                    pass
                if not os.listdir(cache):
                    print("pose: building the TensorRT engine (first run "
                          "only, ~1-3 min — cached in %s afterwards)…"
                          % cache)
                providers = [("TensorrtExecutionProvider",
                              {"trt_engine_cache_enable": True,
                               "trt_engine_cache_path": cache,
                               "trt_timing_cache_enable": True}),
                             "CUDAExecutionProvider", "CPUExecutionProvider"]
                providers = [pv for pv in providers
                             if (pv[0] if isinstance(pv, tuple) else pv)
                             in avail]
            else:
                # Prefer CUDA over the TensorRT EP by default: without the
                # cache TRT recompiles at EVERY session start (minutes),
                # while the CUDA EP starts instantly and is within a few ms
                # for a model this small. CPU is the fallback.
                providers = [p for p in ("CUDAExecutionProvider",
                                         "CPUExecutionProvider")
                             if p in avail] or avail
        self.sess = ort.InferenceSession(
            model_path, sess_options=so, providers=providers)
        # What is inference actually running on? CPUExecutionProvider-only =
        # ~10 fps on a Jetson; CUDA/TensorRT = 60+. Logged at node startup so
        # a CPU-only install is impossible to miss.
        self.providers = self.sess.get_providers()
        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        shape = list(inp.shape)
        if len(shape) == 4 and shape[1] == 3:
            self.nchw = True
            dim = shape[2]
        else:
            self.nchw = False
            dim = shape[1] if len(shape) == 4 else None
        self.size = int(dim) if isinstance(dim, int) and dim > 0 else 192
        t = inp.type
        if "int32" in t:
            self.dtype = np.int32
        elif "uint8" in t:
            self.dtype = np.uint8
        else:
            self.dtype = np.float32

    def infer(self, rgb):
        """(H, W, 3) uint8 RGB -> [(joint_id, u, v, conf)] in image pixels."""
        square, scale, px, py = letterbox(rgb, self.size)
        x = square.astype(self.dtype)
        if self.nchw:
            x = np.transpose(x, (2, 0, 1))
        outputs = self.sess.run(None, {self.input_name: x[None]})
        raw = None
        for o in outputs:
            if np.asarray(o).size == COCO_JOINTS * 3:
                raw = o
                break
        if raw is None:
            raise ValueError("model produced no 17x3 keypoint tensor")
        return decode_movenet(raw, scale, px, py, self.size)


class PoseWorker(object):
    """Runs the estimator OFF the capture path.

    submit(color, depth) stashes references to the newest frame and returns
    immediately; the worker thread infers on the latest stash, attaches depth
    to each confident keypoint, and calls emit([(jid, u, v, z_m, conf)]).
    `color` may be BGRA (channels flipped here) or RGB.
    """

    REPORT_EVERY = 150

    def __init__(self, estimator, emit, min_conf=0.2, gate_conf=0.35,
                 smooth=True, joints=None, label="pose"):
        self.estimator = estimator
        self.emit = emit
        self.min_conf = float(min_conf)
        self.gate_conf = float(gate_conf)   # mean CORE_JOINTS confidence gate
        self.joints = frozenset(joints) if joints is not None else None
        self.smoother = JointSmoother() if smooth else None
        self.label = label
        self.inferences = 0
        self.gated = 0                      # frames rejected by the gate
        self.last_ms = 0.0
        self._cond = threading.Condition()
        self._latest = None
        self._stop = False
        self._win_t0 = time.time()
        self._thread = threading.Thread(target=self._loop)
        self._thread.daemon = True
        self._thread.start()

    def submit(self, color, depth):
        with self._cond:
            self._latest = (color, depth)
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()
        self._thread.join(timeout=3.0)

    def _loop(self):
        while True:
            with self._cond:
                while self._latest is None and not self._stop:
                    self._cond.wait(0.5)
                if self._stop:
                    return
                color, depth = self._latest
                self._latest = None
            t0 = time.time()
            try:
                if color.shape[2] >= 4:                # BGRA from pyk4a
                    rgb = np.ascontiguousarray(color[..., 2::-1])
                else:
                    rgb = color
                kps = self.estimator.infer(rgb)
            except Exception as exc:
                print("%s: inference failed (%s) — pose worker stopped"
                      % (self.label, exc))
                return
            now = time.time()
            self.last_ms = (now - t0) * 1000.0
            self.inferences += 1
            # Person gate: no credible torso -> no person -> emit nothing
            # (and reset smoothing so a returning person doesn't inherit
            # filter state aimed at wherever the ghost was).
            core = [c for j, _u, _v, c in kps if j in CORE_JOINTS]
            if not core or sum(core) / len(core) < self.gate_conf:
                self.gated += 1
                if self.smoother is not None:
                    self.smoother.reset()
                out = []
            else:
                out = []
                for jid, u, v, conf in kps:
                    if conf < self.min_conf:
                        continue
                    if self.joints is not None and jid not in self.joints:
                        continue
                    z = sample_depth(depth, u, v)
                    if self.smoother is not None:
                        u, v, z = self.smoother.filter(jid, u, v, z, now)
                    out.append((jid, u, v, z, conf))
            if out:
                try:
                    self.emit(out)
                except Exception as exc:
                    print("%s: emit failed (%s) — pose worker stopped"
                          % (self.label, exc))
                    return
            if self.inferences % self.REPORT_EVERY == 0:
                fps = self.REPORT_EVERY / max(1e-6, now - self._win_t0)
                self._win_t0 = now
                print("%s: %.1f fps (%.0f ms/infer, %d joints, %d%% gated)"
                      % (self.label, fps, self.last_ms, len(out),
                         100 * self.gated // self.REPORT_EVERY))
                self.gated = 0
