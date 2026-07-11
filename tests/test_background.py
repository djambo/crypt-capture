"""
Background-plate subtraction tests (headless; numpy required).

Run: python3 -m tests.test_background
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
except ImportError:
    print("background tests: skipped (no numpy)")
    sys.exit(0)

from node.background import BackgroundSubtractor, erode_mask


def test_capture_and_subtract():
    h, w = 8, 8
    wall = 2000
    sub = BackgroundSubtractor(margin_mm=50)

    # Capture 5 noisy "empty scene" frames (wall everywhere, ±10mm wobble).
    sub.start_capture(5)
    rng = np.random.RandomState(0)
    finished = False
    for _ in range(5):
        noisy = (wall + rng.randint(-10, 11, size=(h, w))).astype(np.uint16)
        finished = sub.feed(noisy)
    assert finished and sub.active and not sub.capturing
    assert abs(float(sub.plate.mean()) - wall) < 15, "plate should average ~wall"

    # A frame: wall everywhere, but a 2x2 "subject" patch much closer (1000mm).
    frame = np.full((h, w), wall, np.uint16)
    frame[3:5, 3:5] = 1000
    fg = sub.foreground(frame)
    # only the subject patch survives; the wall (≈plate) is removed
    assert fg[3:5, 3:5].all(), "subject must be kept"
    keep = np.zeros((h, w), bool); keep[3:5, 3:5] = True
    assert np.array_equal(fg, keep), "only the subject should remain"
    print("capture+subtract: OK (subject kept, wall removed)")


def test_margin_and_unknown():
    h, w = 4, 4
    sub = BackgroundSubtractor(margin_mm=50)
    sub.start_capture(1)
    plate = np.array([[2000, 2000, 0, 2000]] * 4, np.uint16)   # col 2 = unknown(0)
    sub.feed(plate)

    frame = np.array([[2000, 1900, 1500, 1951]] * 4, np.uint16)
    fg = sub.foreground(frame)
    # col0: equal to plate -> removed; col1: 100mm closer (>50) -> kept;
    # col2: plate unknown -> kept; col3: only 49mm closer (<50 margin) -> removed
    assert (not fg[0, 0]) and fg[0, 1] and fg[0, 2] and (not fg[0, 3]), fg[0]
    print("margin+unknown: OK")


def test_clear_disables():
    sub = BackgroundSubtractor()
    sub.start_capture(1)
    sub.feed(np.full((2, 2), 1500, np.uint16))
    assert sub.active
    sub.clear()
    assert not sub.active and sub.foreground(np.zeros((2, 2), np.uint16)) is None
    print("clear: OK")


def test_plate_persistence():
    """save()/load() round-trip: a node restart must resume subtraction from
    the persisted plate — but only when the grid shape still matches (a
    camera-mode change makes the saved plate wrong-shaped -> ignored)."""
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "test_bg_plate_rt.npz")
    try:
        sub = BackgroundSubtractor(margin_mm=50)
        sub.start_capture(1)
        plate = np.full((6, 8), 1800, np.uint16)
        sub.feed(plate)
        assert sub.active
        assert sub.save(path)

        fresh = BackgroundSubtractor(margin_mm=50)
        assert not fresh.active
        assert fresh.load(path, expect_shape=(6, 8))
        assert fresh.active
        fg = fresh.foreground(np.full((6, 8), 1500, np.uint16))
        assert fg.all()      # 300mm closer than the plate -> kept everywhere
        fg = fresh.foreground(np.full((6, 8), 1800, np.uint16))
        assert not fg.any()  # at plate depth -> removed everywhere

        wrong = BackgroundSubtractor()
        assert not wrong.load(path, expect_shape=(4, 4))   # shape gate
        assert not wrong.active
        assert not wrong.load(path + ".missing")           # absent file
        empty = BackgroundSubtractor()
        assert not empty.save(path + ".none")              # no plate -> no-op
    finally:
        for p in (path, path + ".none"):
            try:
                os.remove(p)
            except OSError:
                pass
    print("plate persistence: OK")


def test_erode_mask():
    """Silhouette-rim erosion: exactly the 1-px boundary band of a solid blob
    is removed per iteration (the mixed-pixel contour that carries the wall's
    colour); interior stays; px=0 is a no-op; thin structures vanish."""
    m = np.zeros((10, 10), bool)
    m[2:8, 2:8] = True                      # solid 6x6 blob

    e1 = erode_mask(m, 1)
    expect = np.zeros((10, 10), bool)
    expect[3:7, 3:7] = True                 # one ring gone
    assert np.array_equal(e1, expect), "1px erosion must strip one ring"

    e2 = erode_mask(m, 2)
    expect2 = np.zeros((10, 10), bool)
    expect2[4:6, 4:6] = True
    assert np.array_equal(e2, expect2), "2px erosion must strip two rings"

    assert erode_mask(m, 0) is m, "px=0 must be a no-op"

    # A hole inside the blob erodes outward too (its rim is mixed pixels).
    holed = m.copy()
    holed[5, 5] = False
    eh = erode_mask(holed, 1)
    assert not eh[4:7, 4:7].any(), "hole rim must erode"
    assert eh[3, 3], "far interior survives"

    # A 1-px-thin line disappears (nothing has 8 kept neighbours).
    line = np.zeros((6, 6), bool)
    line[3, 1:5] = True
    assert not erode_mask(line, 1).any()

    # Blob touching the image border: border pixels never survive.
    edge = np.ones((4, 4), bool)
    ee = erode_mask(edge, 1)
    assert ee[1:3, 1:3].all() and not ee[0].any() and not ee[:, 0].any()
    print("erode_mask: OK")


def test_status_message_round_trip():
    """The plate-done ack (CSTA): encode -> socket -> read_message, and the
    drop-stale gate (message_buffered) must recognise the fixed-size magic so
    a status sitting in a drained backlog isn't mistaken for a partial frame."""
    import select
    import socket

    from protocol.frame import (encode_status, read_message, message_buffered,
                                STATUS_BG_CAPTURED)

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    conn, _ = srv.accept()
    try:
        cli.sendall(encode_status(3, STATUS_BG_CAPTURED))
        select.select([conn], [], [], 2.0)
        assert message_buffered(conn)
        kind, payload = read_message(conn)
        assert kind == "status", kind
        assert payload == {"sensor_id": 3, "event": STATUS_BG_CAPTURED}, payload
    finally:
        cli.close()
        conn.close()
        srv.close()
    print("status (CSTA) round-trip: OK")


if __name__ == "__main__":
    test_capture_and_subtract()
    test_margin_and_unknown()
    test_clear_disables()
    test_plate_persistence()
    test_erode_mask()
    test_status_message_round_trip()
    print("\nALL BACKGROUND TESTS PASSED")
