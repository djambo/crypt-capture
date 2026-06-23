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

from node.background import BackgroundSubtractor


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


if __name__ == "__main__":
    test_capture_and_subtract()
    test_margin_and_unknown()
    test_clear_disables()
    print("\nALL BACKGROUND TESTS PASSED")
