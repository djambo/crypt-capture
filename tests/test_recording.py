"""
Scene-recording tests (headless): the CPR1 take writer/reader round-trip, the
non-blocking tee contract, the relay's record commands, and the plain-HTTP
recording delivery on the WebSocket port.

Run: python3 -m tests.test_recording
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from central import recording
from central.preview_server import PreviewServer, build_message


def _payload(sensor=0, frame=0, count=64, rgb=True, grid=False):
    xyz = np.linspace(0, 1, count * 3, dtype=np.float32).reshape(count, 3)
    colors = (np.arange(count * 3) % 255).astype(np.uint8).reshape(count, 3) \
        if rgb else None
    g = (16, max(count // 16, 1) + 1,
         np.arange(count, dtype=np.uint32)) if grid else None
    return build_message(sensor, frame, xyz, colors,
                         gravity=(0.0, -1.0, 0.0), grid=g)


def test_round_trip():
    """Frames come back verbatim, timestamped, with correct sidecar meta."""
    d = tempfile.mkdtemp()
    try:
        rec = recording.TakeRecorder(d)
        meta = rec.start(name="unit test")
        assert rec.recording and meta["name"] == "unit test"
        sent = [_payload(0, 0, 64), _payload(1, 0, 200, rgb=False),
                _payload(0, 1, 32, grid=True)]
        for p in sent:
            rec.add_frame(p)
            time.sleep(0.005)
        # Idempotent second start while running: returns the live take.
        again = rec.start(name="other")
        assert again["id"] == meta["id"], again
        final = rec.stop()
        assert not rec.recording and rec.stop() is None
        assert final["frames"] == 3 and final["sensors"] == [0, 1]
        assert final["max_count"] == 200 and final["dropped"] == 0
        assert final["duration"] > 0

        frames = list(recording.read_take(
            os.path.join(d, final["id"] + ".cpr")))
        assert [p for _, p in frames] == sent
        ts = [t for t, _ in frames]
        assert ts == sorted(ts) and ts[0] >= 0

        items = recording.list_recordings(d)
        assert len(items) == 1 and items[0] == final, items
        print("round trip: OK (%d frames, %d B)"
              % (final["frames"], final["bytes"]))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tee_never_blocks_and_drops_over_cap():
    """add_frame must return immediately; past the buffer cap frames are
    dropped-and-counted rather than stalling the (live) caller."""
    d = tempfile.mkdtemp()
    try:
        rec = recording.TakeRecorder(d, max_buffer_bytes=4096)
        rec.start()
        big = _payload(0, 0, 2000)          # ~30 KB >> 4 KB cap
        t0 = time.time()
        for i in range(200):
            rec.add_frame(big)
        elapsed = time.time() - t0
        assert elapsed < 0.5, "add_frame blocked (%.3fs)" % elapsed
        final = rec.stop()
        assert final["dropped"] > 0, final
        assert final["frames"] + final["dropped"] == 200
        print("tee non-blocking + drop-over-cap: OK (%d dropped)"
              % final["dropped"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reader_rejects_garbage_and_survives_truncation():
    d = tempfile.mkdtemp()
    try:
        bad = os.path.join(d, "bad.cpr")
        with open(bad, "wb") as f:
            f.write(b"NOPE0000")
        try:
            list(recording.read_take(bad))
            assert False, "bad magic accepted"
        except ValueError:
            pass
        # A crash mid-write leaves a truncated tail: reader yields the intact
        # prefix and stops (no exception).
        rec = recording.TakeRecorder(d)
        meta = rec.start()
        p = _payload(0, 0, 64)
        rec.add_frame(p)
        rec.add_frame(p)
        final = rec.stop()
        path = os.path.join(d, final["id"] + ".cpr")
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) - 10)
        frames = list(recording.read_take(path))
        assert len(frames) == 1 and frames[0][1] == p
        print("reader gates + truncation: OK")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_id_safety():
    assert recording.take_path("recs", "../../etc/passwd") is None
    assert recording.take_path("recs", "a/b") is None
    assert recording.take_path("recs", "take-1.cpr") == \
        os.path.join("recs", "take-1.cpr")
    assert recording.take_path("recs", "take-1") == \
        os.path.join("recs", "take-1.cpr")
    assert not recording.delete_recording("recs", "../x")
    print("id safety: OK")


class _Sink:
    """Captures the server's broadcast texts without real sockets."""

    def __init__(self, server):
        self.messages = []
        server._broadcast_text = lambda obj: self.messages.append(obj)

    def by_type(self, t):
        return [m for m in self.messages if m.get("type") == t]


def test_server_commands():
    """record_start/stop/delete via the browser-command entry point, with
    frames teed from the node path."""
    d = tempfile.mkdtemp()
    try:
        server = PreviewServer(recordings_dir=d)
        sink = _Sink(server)
        server._on_browser_command({"cmd": "record_start", "name": "cmd take"})
        assert server._recorder.recording
        statuses = sink.by_type("record_status")
        assert statuses and statuses[-1]["state"] == "recording"
        for i in range(3):
            server._recorder.add_frame(_payload(0, i))
        server._on_browser_command({"cmd": "record_stop"})
        assert not server._recorder.recording
        saved = [m for m in sink.by_type("record_status")
                 if m.get("state") == "saved"]
        assert saved and saved[-1]["recording"]["frames"] == 3
        lists = sink.by_type("recordings")
        assert lists and len(lists[-1]["items"]) == 1
        rec_id = saved[-1]["recording"]["id"]
        # Stop with nothing running -> idle (resets a stale viewer panel).
        server._on_browser_command({"cmd": "record_stop"})
        assert sink.by_type("record_status")[-1]["state"] == "idle"
        server._on_browser_command({"cmd": "delete_recording", "id": rec_id})
        assert sink.by_type("recordings")[-1]["items"] == []
        print("server commands: OK")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _http_get(server, request_line):
    """Run one request through _serve_http over a socketpair; returns
    (status_line, headers, body)."""
    a, b = socket.socketpair()

    def serve_and_close():
        # In production _serve_client closes the socket after _serve_http;
        # do the same here so the reader below sees EOF.
        try:
            server._serve_http(b, request_line)
        finally:
            b.close()

    t = threading.Thread(target=serve_and_close)
    t.start()
    chunks = []
    while True:
        data = a.recv(65536)
        if not data:
            break
        chunks.append(data)
    t.join()
    a.close()
    b.close()
    raw = b"".join(chunks)
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return lines[0], headers, body


def test_http_endpoint():
    """GET /recordings lists takes; GET /recordings/<id> serves the file
    byte-exact with CORS; traversal and unknown paths 404."""
    d = tempfile.mkdtemp()
    try:
        server = PreviewServer(recordings_dir=d)
        _Sink(server)
        server._on_browser_command({"cmd": "record_start", "name": "http"})
        payload = _payload(0, 0, 128)
        server._recorder.add_frame(payload)
        server._on_browser_command({"cmd": "record_stop"})
        rec_id = recording.list_recordings(d)[0]["id"]

        status, headers, body = _http_get(server, "GET /recordings HTTP/1.1")
        assert "200" in status and headers["content-type"] == "application/json"
        assert headers["access-control-allow-origin"] == "*"
        items = json.loads(body.decode("utf-8"))
        assert len(items) == 1 and items[0]["id"] == rec_id

        status, headers, body = _http_get(
            server, "GET /recordings/%s HTTP/1.1" % rec_id)
        assert "200" in status
        assert int(headers["content-length"]) == len(body)
        expected = open(os.path.join(d, rec_id + ".cpr"), "rb").read()
        assert body == expected, "served file differs from disk"

        status, _, _ = _http_get(server, "GET /recordings/nope HTTP/1.1")
        assert "404" in status
        status, _, _ = _http_get(
            server, "GET /recordings/../rig_calib.json HTTP/1.1")
        assert "404" in status
        status, _, _ = _http_get(server, "GET / HTTP/1.1")
        assert "404" in status
        status, _, _ = _http_get(server, "POST /recordings HTTP/1.1")
        assert "405" in status
        status, _, _ = _http_get(server, "OPTIONS /recordings HTTP/1.1")
        assert "204" in status
        print("http endpoint: OK")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_round_trip()
    test_tee_never_blocks_and_drops_over_cap()
    test_reader_rejects_garbage_and_survives_truncation()
    test_id_safety()
    test_server_commands()
    test_http_endpoint()
    print("all recording tests passed")
