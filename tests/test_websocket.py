"""
WebSocket permessage-deflate tests (stdlib only, no server needed).

Covers the RFC 7692 per-message deflate/inflate round-trip, the RSV1 frame
encode → read_frame decode path, and extension-offer parsing.

Run: python3 -m tests.test_websocket
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import websocket


class _FakeSock(object):
    """Minimal recv()-only socket over a fixed byte buffer."""

    def __init__(self, data):
        self._data = data
        self._pos = 0

    def recv(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_deflate_roundtrip():
    for payload in (b"", b"x", b"hello world" * 100,
                    os.urandom(1000), bytes(range(256)) * 500,
                    struct.pack("<1000H", *range(1000))):
        assert websocket.inflate_payload(
            websocket.deflate_payload(payload)) == payload
    print("deflate/inflate roundtrip: OK")


def test_compressed_frame_roundtrip():
    payload = struct.pack("<5000H", *(i % 4096 for i in range(5000)))
    wire = websocket.encode_frame(payload, opcode=websocket.OP_BINARY,
                                  compress=True)
    assert wire[0] & 0x40, "RSV1 must be set on a compressed frame"
    assert len(wire) < len(payload), "structured data should shrink"
    opcode, out = websocket.read_frame(_FakeSock(wire))
    assert opcode == websocket.OP_BINARY
    assert out == payload
    # Masked (client→server) + compressed also round-trips.
    wire = websocket.encode_frame(payload, opcode=websocket.OP_BINARY,
                                  mask=True, compress=True)
    opcode, out = websocket.read_frame(_FakeSock(wire))
    assert out == payload
    # Uncompressed frames still pass through untouched.
    wire = websocket.encode_frame(payload, opcode=websocket.OP_BINARY)
    assert not (wire[0] & 0x40)
    opcode, out = websocket.read_frame(_FakeSock(wire))
    assert out == payload
    print("compressed frame roundtrip: OK (%d -> %d bytes)"
          % (len(payload), len(websocket.deflate_payload(payload))))


def test_offer_parsing():
    yes = {"sec-websocket-extensions":
           "permessage-deflate; client_max_window_bits"}
    assert websocket._offers_deflate(yes)
    multi = {"sec-websocket-extensions":
             "x-webkit-deflate-frame, permessage-deflate"}
    assert websocket._offers_deflate(multi)
    no = {"sec-websocket-extensions": "x-webkit-deflate-frame"}
    assert not websocket._offers_deflate(no)
    assert not websocket._offers_deflate({})
    print("extension offer parsing: OK")


if __name__ == "__main__":
    test_deflate_roundtrip()
    test_compressed_frame_roundtrip()
    test_offer_parsing()
    print("ALL WEBSOCKET TESTS PASSED")
