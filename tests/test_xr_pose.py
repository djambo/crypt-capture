"""
XR pose passthrough tests (headless, no sockets).

The feature under test: a presenting viewer sends {cmd:"xr_pose", ...} and the
relay rebroadcasts it as {"type":"xr_pose", "sid":N, ...} to every OTHER
viewer, so their scenes can draw a live headset gizmo. The sender is excluded,
the sid is stable per connection, and dropped connections free their sid entry.

Run: python3 -m tests.test_xr_pose
"""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from central.preview_server import PreviewServer
from protocol import websocket


class FakeSender:
    def __init__(self):
        self.texts = []

    def put_text(self, ws_frame):
        self.texts.append(ws_frame)

    def close(self):
        pass


def _server_stub():
    """A PreviewServer with just the client-side state the passthrough
    touches — the real constructor spins up sockets/threads we don't need."""
    srv = PreviewServer.__new__(PreviewServer)
    srv._lock = threading.Lock()
    srv._clients = []
    srv._client_senders = {}
    srv._xr_sids = {}
    srv._xr_sid_next = 0
    return srv


def _decode_text(ws_frame):
    # Server->client frames are unmasked: header is 2 bytes for our sizes.
    assert ws_frame[0] & 0x0F == websocket.OP_TEXT
    length = ws_frame[1] & 0x7F
    assert length < 126, "test payloads stay under 126 bytes"
    return json.loads(ws_frame[2:2 + length].decode("utf-8"))


def test_fanout_excludes_sender_and_stamps_sid():
    srv = _server_stub()
    a, b, c = object(), object(), object()
    senders = {a: FakeSender(), b: FakeSender(), c: FakeSender()}
    srv._client_senders = dict(senders)

    srv._on_browser_command({"cmd": "xr_pose", "head": [1, 2, 3]}, conn=a)

    assert not senders[a].texts, "sender must not receive its own echo"
    for conn in (b, c):
        msgs = [_decode_text(f) for f in senders[conn].texts]
        assert len(msgs) == 1
        assert msgs[0]["type"] == "xr_pose"
        assert msgs[0]["head"] == [1, 2, 3]
        assert msgs[0]["sid"] == 0
        assert "cmd" not in msgs[0]
    print("ok: fanout excludes sender, stamps type+sid")


def test_sid_stable_per_connection_and_distinct():
    srv = _server_stub()
    a, b = object(), object()
    out = FakeSender()
    srv._client_senders = {a: FakeSender(), b: FakeSender(), object(): out}

    srv._on_browser_command({"cmd": "xr_pose"}, conn=a)
    srv._on_browser_command({"cmd": "xr_pose"}, conn=b)
    srv._on_browser_command({"cmd": "xr_pose"}, conn=a)

    sids = [_decode_text(f)["sid"] for f in out.texts]
    assert sids == [0, 1, 0], sids
    print("ok: sid stable per connection, distinct across connections")


def test_drop_frees_sid_entry():
    srv = _server_stub()

    class FakeConn:
        def close(self):
            pass

    conn = FakeConn()
    srv._client_senders = {conn: FakeSender()}
    srv._clients = [conn]
    srv._on_browser_command({"cmd": "xr_pose"}, conn=conn)
    assert conn in srv._xr_sids
    srv._drop(conn)
    assert conn not in srv._xr_sids
    print("ok: drop frees the sid entry")


if __name__ == "__main__":
    test_fanout_excludes_sender_and_stamps_sid()
    test_sid_stable_per_connection_and_distinct()
    test_drop_frees_sid_entry()
    print("all xr_pose passthrough tests passed")
