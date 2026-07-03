"""Headless tests for the skeleton/pose pipeline (docs/skeleton_pose.md):

  - CPOS wire round-trip (encode_pose -> read_message),
  - JointTracker gating (confidence, missing depth),
  - solve_skeleton: posed sensors watching the same noisy joints -> recover
    the rig transform (full 3D, no IMU),
  - sim projection round-trip: sim_node's synthetic keypoints unprojected the
    relay's way land back on the ground-truth world joints.

Run: python3 -m tests.test_pose
"""

import math
import os
import socket
import tempfile
import threading
import time

import numpy as np

from central.calibration import JointTracker, solve_skeleton
from node.pose import (COCO_JOINTS, JointSmoother, OneEuro, PoseWorker,
                       decode_movenet, letterbox, sample_depth)
from node.sim_node import (parse_pose, project_keypoints,
                           skeleton_world_joints, world_to_view)
from protocol.frame import encode_pose, read_message

RNG = np.random.RandomState(23)


def rot_y(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_x(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def test_cpos_roundtrip():
    kps = [(0, 320.5, 120.25, 1.532, 0.91), (9, 10.0, 560.0, 2.1, 0.4),
           (16, 630.9, 570.1, 0.0, 0.88)]
    data = encode_pose(3, 123456789, kps)
    a, b = socket.socketpair()
    threading.Thread(target=lambda: (a.sendall(data), a.close()),
                     daemon=True).start()
    kind, msg = read_message(b)
    b.close()
    assert kind == "pose"
    assert msg["sensor_id"] == 3 and msg["timestamp_ns"] == 123456789
    assert len(msg["keypoints"]) == 3
    for got, want in zip(msg["keypoints"], kps):
        assert got[0] == want[0]
        for g, w in zip(got[1:], want[1:]):
            assert abs(g - w) < 1e-4
    print("CPOS round-trip: OK")


def test_joint_tracker():
    tr = JointTracker(min_conf=0.5)
    kept = tr.add(0, 0.0, [(5, (0.1, 0.2, -1.0), 0.9),   # kept
                           (6, (0.2, 0.2, -1.0), 0.3),   # low confidence
                           (7, (0.0, 0.0, 0.0), 0.9)])   # no depth
    assert kept == 1
    assert tr.counts() == {0: 1}
    assert list(tr.tracks[0]) == [5]
    print("JointTracker gating: OK")


def test_solve_skeleton():
    """Two posed sensors watch the same moving joints (with per-view noise ~
    real keypoint jitter). solve_skeleton must recover the pose to ~cm."""
    n = 120
    times = np.arange(n) / 15.0
    poses = {0: (np.eye(3), np.zeros(3)),
             1: (rot_y(55).dot(rot_x(-8)), np.array([1.3, 0.2, -0.7]))}
    trackers = JointTracker(min_conf=0.5)
    for k in range(n):
        world = skeleton_world_joints(times[k] * 3.0)   # speed up coverage
        for sid, (R_vw, t_vw) in poses.items():
            R_wv, t_wv = R_vw.T, -R_vw.T.dot(t_vw)
            joints = []
            for jid, pw in world.items():
                p = R_wv.dot(pw) + t_wv + RNG.normal(scale=0.015, size=3)
                joints.append((jid, p, 0.9))
            trackers.add(sid, times[k] + 0.001 * sid, joints)
    rig = solve_skeleton(trackers.tracks, ref=0)
    assert set(rig) == {0, 1}
    R_est, t_est = rig[1]["R"], rig[1]["t"]
    R_true, t_true = poses[1]
    rot_err = np.degrees(np.arccos(
        np.clip((np.trace(R_est.T.dot(R_true)) - 1) / 2, -1, 1)))
    t_err = np.linalg.norm(t_est - t_true)
    assert rot_err < 1.0, "rotation off %.2f deg" % rot_err
    assert t_err < 0.02, "translation off %.1f mm" % (t_err * 1000)
    print("solve_skeleton: OK (rot %.2f deg, t %.1f mm, rms %.1f mm, %d pairs)"
          % (rot_err, t_err * 1000, rig[1]["rms"] * 1000, rig[1]["pairs"]))


def test_sim_projection_roundtrip():
    """sim keypoints -> pinhole unprojection (the relay's math with zero
    distortion) -> pose transform must land on the ground-truth world joints."""
    w, h = 640, 576
    fx = (w / 2.0) / math.tan(math.radians(75.0) / 2.0)
    cx, cy = w / 2.0, h / 2.0
    pose = parse_pose("30,1.0,1.0,0.2,-5")
    world = skeleton_world_joints(5.0)
    kps = project_keypoints(world, pose, w, h)
    assert len(kps) >= 6, "most joints should be in frame"
    R = np.array(pose[0])
    t = np.array(pose[1])
    for jid, u, v, z, conf in kps:
        xo = (u - cx) / fx * z
        yo = (v - cy) / fx * z
        p_view = np.array([xo, -yo, -z])
        p_world = R.dot(p_view) + t
        err = np.linalg.norm(p_world - np.array(world[jid]))
        assert err < 1e-6, "joint %d off %.2e m" % (jid, err)
    print("sim projection round-trip: OK (%d joints)" % len(kps))


def test_letterbox_decode():
    """letterbox + decode_movenet must be exact inverses (up to NN resize)."""
    rgb = RNG.randint(0, 255, size=(576, 640, 3)).astype(np.uint8)
    square, scale, px, py = letterbox(rgb, 192)
    assert square.shape == (192, 192, 3)
    assert px == 0 and py > 0                      # wide image pads vertically
    # A keypoint the model would report at the letterboxed position of image
    # pixel (500, 300) must decode back to (500, 300).
    u_img, v_img = 500.0, 300.0
    x_norm = (u_img * scale + px) / 192.0
    y_norm = (v_img * scale + py) / 192.0
    raw = np.zeros((1, 1, COCO_JOINTS, 3), dtype=np.float32)
    raw[0, 0, 4] = (y_norm, x_norm, 0.8)
    kps = decode_movenet(raw, scale, px, py, 192)
    jid, u, v, conf = kps[4]
    assert jid == 4 and abs(conf - 0.8) < 1e-6
    assert abs(u - u_img) < 1e-3 and abs(v - v_img) < 1e-3
    print("letterbox/decode round-trip: OK")


def test_sample_depth():
    d = np.zeros((100, 100), dtype=np.uint16)
    d[50, 50] = 0                                  # hole at the keypoint...
    d[49, 50] = 1500
    d[51, 50] = 1520
    d[50, 49] = 1480
    assert abs(sample_depth(d, 50, 50) - 1.5) < 0.02   # ...median of neighbours
    assert sample_depth(d, 5, 5) == 0.0            # empty window
    assert sample_depth(d, -3, 50) == 0.0          # off-image
    print("sample_depth: OK")


def _build_dummy_movenet(path, size=192, dtype="int32"):
    """A minimal ONNX with MoveNet's exact interface — NHWC [1,S,S,3] input,
    [1,1,17,3] float output — whose keypoints are a constant (plus 0×input so
    the input participates in the graph). Exercises MoveNetEstimator's real
    onnxruntime path without shipping model weights."""
    import onnx
    from onnx import TensorProto, helper
    kp = np.zeros((1, 1, COCO_JOINTS, 3), dtype=np.float32)
    kp[0, 0, :, 0] = np.linspace(0.2, 0.8, COCO_JOINTS)   # y
    kp[0, 0, :, 1] = np.linspace(0.3, 0.7, COCO_JOINTS)   # x
    kp[0, 0, :, 2] = 0.9                                  # conf
    ttype = {"int32": TensorProto.INT32, "float32": TensorProto.FLOAT}[dtype]
    inp = helper.make_tensor_value_info("input", ttype, [1, size, size, 3])
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT,
                                        [1, 1, COCO_JOINTS, 3])
    nodes = [
        helper.make_node("Cast", ["input"], ["in_f"], to=TensorProto.FLOAT),
        helper.make_node("ReduceMean", ["in_f"], ["mean"], keepdims=0),
        helper.make_node("Mul", ["mean", "zero"], ["zeroed"]),
        helper.make_node("Add", ["kp", "zeroed"], ["output"]),
    ]
    init = [
        helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0]),
        helper.make_tensor("kp", TensorProto.FLOAT, list(kp.shape),
                           kp.ravel().tolist()),
    ]
    graph = helper.make_graph(nodes, "dummy_movenet", [inp], [out], init)
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, path)
    return kp[0, 0]


def test_movenet_estimator():
    """The real estimator path (onnxruntime session, dtype/layout detection,
    letterbox, decode) against a dummy model with MoveNet's interface."""
    try:
        import onnx  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        print("MoveNetEstimator: skipped (onnx/onnxruntime not installed)")
        return
    from node.pose import MoveNetEstimator
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dummy.onnx")
        truth = _build_dummy_movenet(path, size=192, dtype="int32")
        est = MoveNetEstimator(path, threads=1)
        assert est.size == 192 and not est.nchw and est.dtype == np.int32
        rgb = RNG.randint(0, 255, size=(576, 640, 3)).astype(np.uint8)
        kps = est.infer(rgb)
        assert len(kps) == COCO_JOINTS
        # Decode the constant truth by hand and compare.
        _, scale, px, py = letterbox(rgb, 192)
        for jid, u, v, conf in kps:
            y, x, c = truth[jid]
            assert abs(conf - c) < 1e-5
            assert abs(u - (x * 192 - px) / scale) < 1e-3
            assert abs(v - (y * 192 - py) / scale) < 1e-3
    print("MoveNetEstimator (dummy ONNX): OK")


def test_one_euro():
    """Jitter shrinks a lot at rest; fast motion tracks with little lag."""
    rng = np.random.RandomState(3)
    # Static signal + noise: filtered variance must drop >5x.
    f = OneEuro()
    xs, ys = [], []
    for k in range(120):
        t = k / 30.0
        x = 100.0 + rng.normal(scale=2.0)
        xs.append(x)
        ys.append(f.filter(x, t))
    raw_std = np.std(np.array(xs[30:]) - 100.0)
    smt_std = np.std(np.array(ys[30:]) - 100.0)
    assert smt_std < raw_std / 2.5, (raw_std, smt_std)
    # A fast ramp (500 px/s) must not lag more than ~a frame's travel.
    f2 = OneEuro()
    for k in range(60):
        t = k / 30.0
        out = f2.filter(500.0 * t, t)
    lag_px = 500.0 * (59 / 30.0) - out
    assert lag_px < 25.0, "ramp lag %.1f px" % lag_px
    # JointSmoother resets a stale joint (no slide across the frame).
    js = JointSmoother()
    js.filter(5, 100.0, 100.0, 1.5, 0.0)
    u, v, z = js.filter(5, 400.0, 300.0, 2.0, 1.0)   # > RESET_S later
    assert u == 400.0 and v == 300.0 and z == 2.0
    print("OneEuro/JointSmoother: OK (std %.2f -> %.2f px, ramp lag %.1f px)"
          % (raw_std, smt_std, lag_px))


class _FakeEstimator(object):
    """Person-shaped keypoints with confident torso joints (5/6/11/12)."""

    def __init__(self, torso_conf=0.8):
        self.calls = 0
        self.torso_conf = torso_conf

    def infer(self, rgb):
        self.calls += 1
        c = self.torso_conf
        return [(0, 100.0, 80.0, 0.9), (9, 300.0, 200.0, 0.7),
                (5, 120.0, 120.0, c), (6, 160.0, 120.0, c),
                (11, 125.0, 200.0, c), (12, 155.0, 200.0, c),
                (16, 50.0, 40.0, 0.05)]        # last one below min_conf


def test_pose_worker():
    """Latest-frame semantics + depth attach + emit payloads."""
    emitted = []
    est = _FakeEstimator()
    depth = np.full((576, 640), 1500, dtype=np.uint16)
    color = RNG.randint(0, 255, size=(576, 640, 4)).astype(np.uint8)  # BGRA
    w = PoseWorker(est, lambda kps: emitted.append(kps), min_conf=0.2,
                   label="test pose")
    for _ in range(5):
        w.submit(color, depth)
    deadline = time.time() + 3.0
    while not emitted and time.time() < deadline:
        time.sleep(0.01)
    w.stop()
    assert emitted, "worker emitted nothing"
    assert est.calls <= 5                       # never more than submitted
    kps = emitted[0]
    assert [k[0] for k in kps] == [0, 9, 5, 6, 11, 12]  # low-conf dropped
    jid, u, v, z, conf = kps[0]
    assert abs(z - 1.5) < 1e-6                  # depth attached (mm -> m)
    # Payload encodes/decodes through the real wire format.
    data = encode_pose(1, 42, kps)
    a, b = socket.socketpair()
    threading.Thread(target=lambda: (a.sendall(data), a.close()),
                     daemon=True).start()
    kind, msg = read_message(b)
    b.close()
    assert kind == "pose" and len(msg["keypoints"]) == 6
    print("PoseWorker: OK (%d inferences for 5 submits)" % est.calls)


def test_pose_worker_person_gate():
    """Weak torso (furniture ghost) -> the frame emits NOTHING."""
    emitted = []
    est = _FakeEstimator(torso_conf=0.15)       # below the 0.35 gate
    depth = np.full((576, 640), 1500, dtype=np.uint16)
    color = np.zeros((576, 640, 4), dtype=np.uint8)
    w = PoseWorker(est, lambda kps: emitted.append(kps), min_conf=0.2,
                   gate_conf=0.35, label="test gate")
    w.submit(color, depth)
    deadline = time.time() + 2.0
    while est.calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    w.stop()
    assert est.calls >= 1 and not emitted, (est.calls, emitted)
    print("PoseWorker person gate: OK (ghost frame suppressed)")


def test_pose_worker_joint_subset():
    """--pose-joints minimal: only the requested joints are emitted."""
    from node.pose import MINIMAL_JOINTS
    emitted = []
    est = _FakeEstimator(torso_conf=0.8)
    depth = np.full((576, 640), 1500, dtype=np.uint16)
    color = np.zeros((576, 640, 4), dtype=np.uint8)
    w = PoseWorker(est, lambda kps: emitted.append(kps), min_conf=0.2,
                   joints=MINIMAL_JOINTS, label="test subset")
    w.submit(color, depth)
    deadline = time.time() + 2.0
    while not emitted and time.time() < deadline:
        time.sleep(0.01)
    w.stop()
    assert emitted
    ids = set(k[0] for k in emitted[0])
    assert ids <= set(MINIMAL_JOINTS), ids
    assert 0 in ids and 9 in ids            # head + wrist made it through
    print("PoseWorker joint subset: OK (%s)" % sorted(ids))


if __name__ == "__main__":
    test_cpos_roundtrip()
    test_joint_tracker()
    test_solve_skeleton()
    test_sim_projection_roundtrip()
    test_letterbox_decode()
    test_sample_depth()
    test_movenet_estimator()
    test_one_euro()
    test_pose_worker()
    test_pose_worker_person_gate()
    test_pose_worker_joint_subset()
    print("\nALL POSE TESTS PASSED")
