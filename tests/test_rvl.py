"""
RVL codec tests: the vectorized NumPy path must be BIT-IDENTICAL to the
pure-Python reference, and both must round-trip losslessly.

Run: python3 -m tests.test_rvl   (or: python3 tests/test_rvl.py)
NumPy is required to exercise the fast path; without it only the reference
round-trip is checked.
"""

import os
import random
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import rvl

try:
    import numpy as np
except ImportError:
    np = None


def _check_case(depth, name):
    """Reference round-trips, and (if numpy) numpy == reference, bit for bit."""
    n = len(depth)
    ref_bytes = rvl._compress_py(depth)
    ref_back = rvl._decompress_py(ref_bytes, n)
    assert list(ref_back) == list(depth), "reference not lossless: %s" % name

    if np is None:
        return
    np_bytes = rvl._compress_np(depth)
    assert np_bytes == ref_bytes, (
        "compress bytes differ from reference: %s (%d vs %d bytes)"
        % (name, len(np_bytes), len(ref_bytes)))
    np_back = rvl._decompress_np(np_bytes, n)
    assert isinstance(np_back, array) and np_back.typecode == "H"
    assert list(np_back) == list(depth), "numpy not lossless: %s" % name
    # cross: numpy decode of reference bytes, and reference decode of numpy bytes
    assert list(rvl._decompress_np(ref_bytes, n)) == list(depth), name
    assert list(rvl._decompress_py(np_bytes, n)) == list(depth), name


def test_edge_cases():
    cases = {
        "empty": [],
        "single_zero": [0],
        "single_value": [1234],
        "all_zero": [0] * 1000,
        "all_max": [0xFFFF] * 1000,
        "starts_nonzero": [5, 6, 0, 0, 7],
        "ends_nonzero": [0, 0, 5, 6, 7],
        "ends_zero": [5, 6, 7, 0, 0],
        "alternating": [0, 1] * 500,
        "big_jumps": [0, 65535, 1, 65534, 0, 2],
        "descending": list(range(2000, 1000, -1)),
    }
    for name, depth in cases.items():
        _check_case(depth, name)
    print("edge cases: OK (%d)" % len(cases))


def test_random():
    rng = random.Random(1234)
    for trial in range(40):
        n = rng.randint(1, 5000)
        density = rng.random()
        depth = [rng.randint(1, 65535) if rng.random() < density else 0
                 for _ in range(n)]
        _check_case(depth, "random#%d" % trial)
    print("random: OK")


def test_realistic_masked_frame():
    # A masked human-ish blob in a 640x576 frame: long zero runs + smooth depth.
    if np is None:
        print("realistic frame: skipped (no numpy)")
        return
    w, h = 640, 576
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, rx, ry = w * 0.5, h * 0.55, w * 0.18, h * 0.34
    r2 = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    depth = np.where(r2 < 1.0, (1200 + 300 * np.sqrt(np.clip(r2, 0, 1))), 0)
    depth = depth.astype(np.uint16).ravel()

    ref_bytes = rvl._compress_py(depth.tolist())
    np_bytes = rvl._compress_np(depth)
    assert np_bytes == ref_bytes, "masked frame bytes differ from reference"
    back = rvl._decompress_np(np_bytes, depth.size)
    assert np.array_equal(np.frombuffer(back, dtype=np.uint16), depth)
    ratio = depth.nbytes / max(1, len(np_bytes))
    valid = int((depth != 0).sum())
    print("realistic frame: OK  %d valid px, %.1fx compression"
          % (valid, ratio))


if __name__ == "__main__":
    test_edge_cases()
    test_random()
    test_realistic_masked_frame()
    print("\nALL RVL TESTS PASSED" + ("" if np is not None else " (reference only; numpy absent)"))
