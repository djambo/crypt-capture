"""
SceneBundler (relay scene-coherent broadcast) behaviour.

The bundler holds each sensor's freshest finished frame and releases ALL
sensors' frames together, so the multi-camera body refreshes as one unit in
the viewer. Barrier-with-timeout semantics:

  - a bundle flushes the moment every ACTIVE sensor has contributed
    (no added latency beyond the cameras' own phase spread);
  - a missing/stalled sensor only delays a bundle up to `timeout`;
  - a newer frame from the same sensor overwrites its pending slot
    (freshness beats completeness);
  - a single-sensor rig flushes immediately per frame (old behaviour);
  - a sensor silent for ACTIVE_TTL drops out of the barrier.
"""

import threading
import time

from central.preview_server import SceneBundler


class _Collector(object):
    def __init__(self):
        self.bundles = []
        self.event = threading.Event()

    def __call__(self, items):
        self.bundles.append(items)
        self.event.set()

    def wait(self, n, timeout=2.0):
        deadline = time.monotonic() + timeout
        while len(self.bundles) < n and time.monotonic() < deadline:
            self.event.wait(0.05)
            self.event.clear()
        return len(self.bundles) >= n


def test_complete_bundle_flushes_before_timeout():
    got = _Collector()
    b = SceneBundler(got, timeout=0.5)      # long timeout: must NOT be the trigger
    try:
        t0 = time.monotonic()
        b.add(0, b"f0", 100)
        b.add(1, b"f1", 200)
        b.add(2, b"f2", 300)
        assert got.wait(1), "complete bundle never flushed"
        took = time.monotonic() - t0
        assert took < 0.4, "flush waited for the timeout (%.3fs) despite " \
                           "being complete" % took
        (bundle,) = got.bundles
        assert [sid for sid, _ in bundle] == [0, 1, 2]
        assert [p for _, p in bundle] == [b"f0", b"f1", b"f2"]
    finally:
        b.close()


def test_missing_sensor_flushes_at_timeout():
    got = _Collector()
    b = SceneBundler(got, timeout=0.15)
    try:
        # Register three active sensors with a first complete bundle.
        for sid in (0, 1, 2):
            b.add(sid, b"x", 0)
        assert got.wait(1)
        # Next round: sensor 2 never delivers.
        t0 = time.monotonic()
        b.add(0, b"a", 0)
        b.add(1, b"b", 0)
        assert got.wait(2), "incomplete bundle never timed out"
        took = time.monotonic() - t0
        assert took >= 0.1, "flushed before the timeout despite missing sensor"
        assert [sid for sid, _ in got.bundles[1]] == [0, 1]
    finally:
        b.close()


def test_newer_frame_overwrites_pending():
    got = _Collector()
    b = SceneBundler(got, timeout=0.15)
    try:
        for sid in (0, 1):
            b.add(sid, b"x", 0)
        assert got.wait(1)
        # Sensor 0 delivers twice while sensor 1 stays silent: the second
        # frame supersedes the first, and the timeout flush carries only it.
        b.add(0, b"old", 0)
        b.add(0, b"new", 0)
        assert got.wait(2)
        assert got.bundles[1] == [(0, b"new")]
    finally:
        b.close()


def test_single_sensor_flushes_immediately():
    got = _Collector()
    b = SceneBundler(got, timeout=0.5)
    try:
        for i in range(3):
            t0 = time.monotonic()
            b.add(0, ("f%d" % i).encode(), i)
            assert got.wait(i + 1), "single-sensor frame stuck in bundler"
            assert time.monotonic() - t0 < 0.4
        assert [bun[0][1] for bun in got.bundles] == [b"f0", b"f1", b"f2"]
    finally:
        b.close()


def test_dead_sensor_leaves_barrier():
    got = _Collector()
    b = SceneBundler(got, timeout=0.15)
    b.ACTIVE_TTL = 0.3                     # fast decay for the test
    try:
        for sid in (0, 1):
            b.add(sid, b"x", 0)
        assert got.wait(1)
        time.sleep(0.4)                    # sensor 1 goes silent past the TTL
        t0 = time.monotonic()
        b.add(0, b"solo", 0)
        assert got.wait(2)
        took = time.monotonic() - t0
        assert took < 0.1, "dead sensor still held the barrier (%.3fs)" % took
        assert got.bundles[1] == [(0, b"solo")]
    finally:
        b.close()


if __name__ == "__main__":
    test_complete_bundle_flushes_before_timeout()
    test_missing_sensor_flushes_at_timeout()
    test_newer_frame_overwrites_pending()
    test_single_sensor_flushes_immediately()
    test_dead_sensor_leaves_barrier()
    print("scene sync OK")
