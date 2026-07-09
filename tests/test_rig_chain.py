"""
Graph-chained rig registration for a multi-camera RING.

`solve_rig` must register a camera that rarely shares the ball with the
REFERENCE directly, by chaining through a neighbour it does share it with
(s1 -> s2 -> s0). On a 3+-camera ring a ball spot is typically seen by only 2 of
N cameras, so the old direct-to-reference pairing left the third camera UNSOLVED
(it then reverted to raw and the clouds sprang apart). This builds exactly that
co-visibility pattern from known ground-truth poses and asserts every camera is
recovered — including the one with NO direct correspondences to the reference.
"""

import numpy as np

from central.calibration import solve_rig


def _rot_y(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def test_ring_chains_through_neighbour():
    # Ground-truth: each sensor i observes a world point P as R_i·P + t_i (world
    # -> sensor i). We invert to get sensor -> world = the rig we want back.
    pose = {
        0: (np.eye(3), np.zeros(3)),                       # reference
        1: (_rot_y(40), np.array([1.2, 0.0, 0.3])),
        2: (_rot_y(-35), np.array([-1.1, 0.0, 0.4])),
    }

    # World ball positions per capture id. Co-visibility is the ring pattern:
    #   caps 1..6  seen by {0, 2}      (ref & s2)
    #   caps 7..12 seen by {1, 2}      (s1 & s2)  -> s1 has NO direct s0 pairs
    # so s1 can only register by chaining s1 -> s2 -> s0.
    rng = np.random.RandomState(0)
    seen = {1: (0, 2), 2: (0, 2), 3: (0, 2), 4: (0, 2), 5: (0, 2), 6: (0, 2),
            7: (1, 2), 8: (1, 2), 9: (1, 2), 10: (1, 2), 11: (1, 2), 12: (1, 2)}
    world = {cid: rng.uniform([-0.5, -0.5, 1.0], [0.5, 0.5, 2.0])
             for cid in seen}

    tracks = {0: [], 1: [], 2: []}
    for cid, sensors in seen.items():
        P = world[cid]
        for s in sensors:
            R, t = pose[s]
            tracks[s].append((float(cid), R.dot(P) + t))   # world -> sensor s

    rig = solve_rig(tracks, min_pairs=5)

    assert set(rig) == {0, 1, 2}, \
        "ring not fully registered (chaining failed): solved=%s" % sorted(rig)

    # Verify each recovered sensor->world transform maps its observations back to
    # the shared world frame (the reference's frame): for every capture two
    # cameras saw, both must land on the same point.
    def to_world(s, x):
        return rig[s]["R"].dot(x) + rig[s]["t"]

    max_err = 0.0
    for cid, sensors in seen.items():
        pts = []
        for s in sensors:
            R, t = pose[s]
            pts.append(to_world(s, R.dot(world[cid]) + t))
        for k in range(1, len(pts)):
            max_err = max(max_err, float(np.linalg.norm(pts[k] - pts[0])))
    assert max_err < 1e-6, \
        "chained registration inconsistent: max cross-view error %.4f m" % max_err
    print("ring chaining OK (s1 registered via s2, max err %.2e m)" % max_err)


if __name__ == "__main__":
    test_ring_chains_through_neighbour()
    print("PASS")
