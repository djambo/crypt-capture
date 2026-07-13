"""
Relay ingest freshness (drop-stale-on-ingest).

The relay's node-reader must show NOW, not a growing backlog. When it can't
keep up, frames pile in the socket buffer and reading them oldest-first plays
an ever-growing delay ("slow motion"/time dilation) even at a healthy recv
fps. `_serve_node` therefore drains the socket to the FRESHEST queued frame and
drops the stale ones (freshness beats completeness) — EXCEPT while recording,
which must tee every frame. (A calibration session KEEPS drop-stale on: the ball
segmentation is per-frame heavy, so consuming every frame let the backlog grow
without bound and fine align fell seconds behind; the stationary sampler is
window/velocity based, so fresh-but-sparse frames sample it fine.)

This test pre-queues several frames on a socketpair, runs the reader to EOF,
and asserts only the newest survived; then repeats with recording active and
asserts every frame is kept.
"""

import socket
import struct

import numpy as np

from central.preview_server import PreviewServer
from protocol import rvl
from protocol.frame import Frame, encode_calib


def _depth_frame(sensor_id, frame_id, w, h):
    """A tiny valid CVF1 frame (geometry only, stride 1)."""
    depth = np.full((h, w), 1500, dtype=np.uint16)
    depth[h // 3:2 * h // 3, w // 3:2 * w // 3] = 1200   # a little structure
    return Frame(sensor_id=sensor_id, frame_id=frame_id, timestamp_ns=frame_id,
                 width=w, height=h, depth=rvl.compress(depth), color=b"",
                 depth_rvl=True, color_aligned=False, stride=1).encode()


def _run_reader(server, frames_bytes):
    """Feed pre-built node messages into a fresh reader connection, run
    `_serve_node` until EOF, return frames_relayed for that run."""
    srv, cli = socket.socketpair()
    w, h = 48, 48
    cli.sendall(encode_calib(0, w, h, 40.0, 40.0, w / 2.0, h / 2.0))
    for fb in frames_bytes:
        cli.sendall(fb)
    cli.shutdown(socket.SHUT_WR)          # reader sees EOF after draining
    before = server.frames_relayed
    server._serve_node(srv, ("test", 0))  # returns on EOF
    cli.close()
    return server.frames_relayed - before


def test_drops_stale_keeps_newest():
    w, h = 48, 48
    n = 10
    frames = [_depth_frame(0, i, w, h) for i in range(n)]

    server = PreviewServer(rig_calib="")   # no rig file -> pure no-op path
    relayed = _run_reader(server, frames)

    # All 10 frames were queued before the reader started; it must collapse the
    # backlog to the freshest two (frame + the 1-frame read-ahead tolerance),
    # NOT process all 10 in order.
    assert relayed <= 2, "expected stale frames to be dropped, relayed=%d" % relayed
    assert relayed >= 1, "at least the freshest frame must be relayed"


def test_one_frame_backlog_is_tolerated():
    """A single newer frame in the buffer is a transient reader stall, not a
    growing backlog — it must be PROCESSED (read-ahead), not discarded.
    Dropping on a 1-frame lag turned scheduler jitter into a ~30% frame loss:
    three healthy hardware-synced 30 fps nodes read as ~20-25 fps in with
    "stale skipped" ~10/s while the relay reader sat ~80% idle (2026-07-13,
    first full-rig ethernet session)."""
    w, h = 48, 48
    frames = [_depth_frame(0, i, w, h) for i in range(2)]

    server = PreviewServer(rig_calib="")
    relayed = _run_reader(server, frames)

    assert relayed == 2, \
        "a 1-frame backlog must be relayed, not dropped (relayed=%d)" % relayed


def test_recording_keeps_every_frame():
    w, h = 48, 48
    n = 10
    frames = [_depth_frame(0, i, w, h) for i in range(n)]

    server = PreviewServer(rig_calib="")
    server._recorder.start(name="test-take")   # recording => drop disabled
    try:
        relayed = _run_reader(server, frames)
    finally:
        server._recorder.stop()

    # With recording active every frame must be teed/relayed — no dropping.
    assert relayed == n, "recording must keep every frame, relayed=%d" % relayed


def test_partial_message_does_not_trigger_skip():
    """message_buffered (the drop-stale gate) must say False for a PARTIALLY
    arrived message and True only once it is fully buffered. The original gate
    was select() alone ("any pending bytes"), so one early byte of the next
    frame made the reader discard the complete frame in hand and BLOCK inside
    read_message until the rest trickled in — throwing away ~15-20% of frames
    under ordinary arrival jitter while mostly idle (3 subtracted-subject
    sensors stuck at ~22-27 fps in with ~6 ms/frame of actual work)."""
    import time

    from protocol import frame as F

    tx, rx = socket.socketpair()
    try:
        enc = F.Frame(0, 7, 123, 4, 4, b"D" * 100, b"C" * 50).encode()
        cases = [                       # (encoded message, partial cut points)
            (enc, (20, 40)),            # frame: mid-header, mid-payload
            (F.encode_imu(1, 0.0, -1.0, 0.0), (5,)),          # fixed-size
            (F.encode_pose(2, 999, [(0, 1.0, 2.0, 3.0, 0.5)] * 5), (17,)),
            (F.encode_texture(0, 3, b"J" * 1000, 10, 10), (200,)),
        ]
        for msg, cuts in cases:
            sent = 0
            for cut in cuts:
                tx.sendall(msg[sent:cut])
                sent = cut
                time.sleep(0.05)
                assert F.message_buffered(rx) is False, \
                    "partial message (%d bytes) must not read as skippable" % cut
            tx.sendall(msg[sent:])
            time.sleep(0.05)
            assert F.message_buffered(rx) is True, "complete message"
            assert F.read_message(rx) is not None
        # Two complete frames back to back: still skippable after reading one.
        tx.sendall(enc + enc)
        time.sleep(0.05)
        assert F.message_buffered(rx) is True
        F.read_message(rx)
        assert F.message_buffered(rx) is True
        F.read_message(rx)
    finally:
        tx.close()
        rx.close()


if __name__ == "__main__":
    test_drops_stale_keeps_newest()
    test_one_frame_backlog_is_tolerated()
    test_recording_keeps_every_frame()
    test_partial_message_does_not_trigger_skip()
    print("ingest freshness OK")
