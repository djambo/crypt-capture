"""
Per-viewer ClientSender tests (headless, no sockets needed).

The fix under test: one slow viewer must never stall the node stream or the
other viewers. Each viewer gets its own sender thread fed via a latest-frame
mailbox — binary cloud frames overwrite a per-sensor slot (stale ones are
skipped), TEXT messages are ordered and lossless, and a producer hand-off
never blocks even while the viewer's socket is wedged.

Run: python3 -m tests.test_sender
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from central.preview_server import ClientSender


class FakeConn:
    """Stands in for a client socket: records sends, can wedge like a full
    TCP buffer (sendall blocks until released), can die mid-send."""

    def __init__(self):
        self.sent = []
        self.unblock = threading.Event()
        self.unblock.set()
        self.fail = False
        self._first_send_seen = threading.Event()

    def sendall(self, data):
        self._first_send_seen.set()
        self.unblock.wait()
        if self.fail:
            raise OSError("connection reset")
        self.sent.append(bytes(data))


def _wait_until(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_latest_frame_wins_per_sensor():
    """While the socket is wedged, newer cloud frames replace unsent ones —
    per sensor — and only the freshest goes out when the socket drains."""
    conn = FakeConn()
    sender = ClientSender(conn, on_error=lambda c: None)
    try:
        # Let the thread wedge inside sendall on a first frame so the
        # mailbox fills behind it deterministically.
        conn.unblock.clear()
        sender.put_frame(0, b"s0-f0")
        assert conn._first_send_seen.wait(2.0)
        for i in range(1, 6):
            sender.put_frame(0, b"s0-f%d" % i)      # overwrite each other
        sender.put_frame(1, b"s1-f0")
        sender.put_frame(1, b"s1-f1")               # overwrites s1-f0
        conn.unblock.set()
        assert _wait_until(lambda: len(conn.sent) >= 3)
        # The wedged first frame, then exactly ONE (the newest) per sensor.
        assert conn.sent[0] == b"s0-f0", conn.sent
        assert set(conn.sent[1:]) == {b"s0-f5", b"s1-f1"}, conn.sent
        assert sender.dropped == 5, sender.dropped   # s0-f1..f4 + s1-f0 skipped
        print("latest frame wins per sensor: OK")
    finally:
        sender.close()


def test_text_is_ordered_and_lossless():
    conn = FakeConn()
    sender = ClientSender(conn, on_error=lambda c: None)
    try:
        conn.unblock.clear()
        sender.put_text(b"t0")
        assert conn._first_send_seen.wait(2.0)
        for i in range(1, 20):
            sender.put_text(b"t%d" % i)
        conn.unblock.set()
        assert _wait_until(lambda: len(conn.sent) == 20)
        assert conn.sent == [b"t%d" % i for i in range(20)], conn.sent
        print("text ordered + lossless: OK")
    finally:
        sender.close()


def test_producer_never_blocks_on_wedged_client():
    """The hand-off must return immediately even while sendall is stuck —
    this is the actual bug: a blocking send in the node thread dragged
    every viewer down to the slowest link."""
    conn = FakeConn()
    sender = ClientSender(conn, on_error=lambda c: None)
    try:
        conn.unblock.clear()
        sender.put_frame(0, b"wedge")
        assert conn._first_send_seen.wait(2.0)
        t0 = time.time()
        for i in range(1000):
            sender.put_frame(0, b"f%d" % i)
            sender.put_text(b"t%d" % i)
        elapsed = time.time() - t0
        assert elapsed < 0.5, "hand-off blocked (%.3fs for 2000 puts)" % elapsed
        print("producer non-blocking while client wedged: OK (%.1f ms)"
              % (elapsed * 1e3))
    finally:
        conn.unblock.set()
        sender.close()


def test_dead_socket_reports_and_stops():
    conn = FakeConn()
    dropped = []
    sender = ClientSender(conn, on_error=dropped.append)
    conn.fail = True
    sender.put_frame(0, b"boom")
    assert _wait_until(lambda: dropped == [conn])
    # After the error the sender is inert: puts are absorbed, nothing sent.
    sender.close()
    sender.put_frame(0, b"after")
    sender.put_text(b"after")
    time.sleep(0.05)
    assert conn.sent == [], conn.sent
    print("dead socket -> on_error, sender stops: OK")


if __name__ == "__main__":
    test_latest_frame_wins_per_sensor()
    test_text_is_ordered_and_lossless()
    test_producer_never_blocks_on_wedged_client()
    test_dead_socket_reports_and_stops()
    print("all sender tests passed")
