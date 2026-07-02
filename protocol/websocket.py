"""
Minimal WebSocket (RFC 6455) helpers — stdlib only, no dependencies.

Just enough to relay binary point-cloud preview frames from the central server
to browser clients (and to drive a headless test client). Server→client frames
are unmasked; client→server frames are masked, per spec. This is intentionally
small: it handles the handshake, binary/close/ping frames and (since the WAN
streaming work) **permessage-deflate** (RFC 7692) — negotiated per connection
with `no_context_takeover` both ways so every message is an independent raw
deflate stream (browsers offer the extension by default, so a remote viewer
gets compressed frames with zero client-side work). No fragmentation. The
central machine is x86, so this is fine; the Jetson never runs it.
"""

import base64
import hashlib
import os
import struct
import zlib

# RFC 7692: a message is raw-deflated, SYNC-flushed, and the trailing
# 00 00 ff ff of the flush is stripped on the wire (re-appended to inflate).
_DEFLATE_TAIL = b"\x00\x00\xff\xff"
_DEFLATE_EXT = ("permessage-deflate; server_no_context_takeover; "
                "client_no_context_takeover")


def _offers_deflate(headers):
    """True if a Sec-WebSocket-Extensions header offers permessage-deflate."""
    offers = headers.get("sec-websocket-extensions", "")
    return any(part.split(";")[0].strip() == "permessage-deflate"
               for part in offers.split(","))


def deflate_payload(payload, level=1):
    """Compress one message per RFC 7692 (no context takeover). Level 1 —
    this runs per frame per viewer; speed over ratio."""
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    data = c.compress(payload) + c.flush(zlib.Z_SYNC_FLUSH)
    if data.endswith(_DEFLATE_TAIL):
        data = data[:-4]
    return data


def inflate_payload(payload):
    """Decompress one RFC 7692 message (no context takeover)."""
    d = zlib.decompressobj(-15)
    return d.decompress(payload + _DEFLATE_TAIL) + d.flush()

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def accept_key(key):
    """Compute the Sec-WebSocket-Accept value for a client's key."""
    digest = hashlib.sha1((key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _recv_exactly(sock, n):
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            return b""
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _read_http_headers(sock):
    """Read an HTTP request/response head (up to the blank line)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > 65536:
            return None
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers


def server_handshake(sock):
    """Complete the server side of the WS upgrade. Returns a dict
    (`{"deflate": bool}` — whether permessage-deflate was negotiated) on
    success, None on failure. Truthy/falsy like the old bool."""
    parsed = _read_http_headers(sock)
    if not parsed:
        return None
    _request, headers = parsed
    key = headers.get("sec-websocket-key")
    if not key or "websocket" not in headers.get("upgrade", "").lower():
        sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        return None
    deflate = _offers_deflate(headers)
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n" % accept_key(key)
    )
    if deflate:
        resp += "Sec-WebSocket-Extensions: %s\r\n" % _DEFLATE_EXT
    resp += "\r\n"
    sock.sendall(resp.encode("ascii"))
    return {"deflate": deflate}


def client_handshake(sock, host, port, path="/", deflate=False):
    """Complete the client side of the WS upgrade. Returns a dict
    (`{"deflate": bool}` — whether the server accepted permessage-deflate)
    on success, None on failure. Truthy/falsy like the old bool."""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n" % (path, host, port, key)
    )
    if deflate:
        req += "Sec-WebSocket-Extensions: %s\r\n" % _DEFLATE_EXT
    req += "\r\n"
    sock.sendall(req.encode("ascii"))
    parsed = _read_http_headers(sock)
    if not parsed:
        return None
    status, headers = parsed
    if "101" not in status or \
            headers.get("sec-websocket-accept") != accept_key(key):
        return None
    return {"deflate": deflate and _offers_deflate(headers)}


def encode_frame(payload, opcode=OP_BINARY, mask=False, compress=False):
    """Encode one WS frame (FIN=1). Mask only for client→server frames.
    `compress=True` deflates the payload and sets RSV1 — only valid on a
    connection that negotiated permessage-deflate."""
    if compress:
        payload = deflate_payload(payload)
    n = len(payload)
    out = bytearray()
    out.append(0x80 | (0x40 if compress else 0x00) | (opcode & 0x0F))
    mbit = 0x80 if mask else 0x00
    if n < 126:
        out.append(mbit | n)
    elif n < 65536:
        out.append(mbit | 126)
        out += struct.pack(">H", n)
    else:
        out.append(mbit | 127)
        out += struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        out += key
        out += bytes(b ^ key[i & 3] for i, b in enumerate(payload))
    else:
        out += payload
    return bytes(out)


def read_frame(sock):
    """Read one WS frame. Returns (opcode, payload) or None on EOF/close."""
    head = _recv_exactly(sock, 2)
    if not head:
        return None
    b0, b1 = head[0], head[1]
    opcode = b0 & 0x0F
    rsv1 = b0 & 0x40                # permessage-deflate compressed message
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exactly(sock, 2)
        if not ext:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exactly(sock, 8)
        if not ext:
            return None
        length = struct.unpack(">Q", ext)[0]
    key = b""
    if masked:
        key = _recv_exactly(sock, 4)
        if not key:
            return None
    payload = _recv_exactly(sock, length) if length else b""
    if length and not payload:
        return None
    if masked and payload:
        payload = bytes(b ^ key[i & 3] for i, b in enumerate(payload))
    if rsv1 and payload:
        payload = inflate_payload(payload)
    return opcode, payload
