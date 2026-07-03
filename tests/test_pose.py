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
import socket
import threading

import numpy as np

from central.calibration import JointTracker, solve_skeleton
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


if __name__ == "__main__":
    test_cpos_roundtrip()
    test_joint_tracker()
    test_solve_skeleton()
    test_sim_projection_roundtrip()
    print("\nALL POSE TESTS PASSED")
