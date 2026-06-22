"""
crypt-capture wire protocol — a single synchronized depth+color frame.

One message per captured frame, streamed over TCP from each node to the
central recorder. `frame_id` is the hardware-synced frame index (identical
across all nodes for the same instant, courtesy of the Kinect daisy-chain
sync), which is how the recorder groups the N sensors back together.

Header (little-endian, 36 bytes):
    magic        4s   b"CVF1"
    sensor_id    B    0..N-1
    flags        B    bit0 = depth is RVL-compressed
    reserved     H
    frame_id     Q    hardware-synced frame index
    timestamp_ns Q    node capture timestamp (ns)
    width        H
    height       H
    depth_len    I    bytes of depth payload (RVL or raw u16)
    color_len    I    bytes of color payload (e.g. JPEG/H.26x keyframe)
Payload: depth_bytes ++ color_bytes
"""

import socket
import struct
from dataclasses import dataclass

MAGIC = b"CVF1"
_HEADER = struct.Struct("<4sBBHQQHHII")
HEADER_SIZE = _HEADER.size

FLAG_DEPTH_RVL = 0x01


@dataclass
class Frame:
    sensor_id: int
    frame_id: int
    timestamp_ns: int
    width: int
    height: int
    depth: bytes          # RVL-compressed (or raw u16 if flag unset)
    color: bytes          # opaque encoded color payload
    depth_rvl: bool = True

    def encode(self) -> bytes:
        flags = FLAG_DEPTH_RVL if self.depth_rvl else 0
        header = _HEADER.pack(
            MAGIC, self.sensor_id, flags, 0,
            self.frame_id, self.timestamp_ns,
            self.width, self.height, len(self.depth), len(self.color),
        )
        return header + self.depth + self.color


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from a blocking socket, or b'' on clean EOF."""
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            return b""
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket):
    """Read one Frame from a socket, or None on clean connection close."""
    header = _recv_exactly(sock, HEADER_SIZE)
    if not header:
        return None
    magic, sensor_id, flags, _res, frame_id, ts, w, h, dlen, clen = _HEADER.unpack(header)
    if magic != MAGIC:
        raise ValueError("bad magic %r — stream desynced" % (magic,))
    depth = _recv_exactly(sock, dlen) if dlen else b""
    color = _recv_exactly(sock, clen) if clen else b""
    if (dlen and not depth) or (clen and not color):
        return None
    return Frame(
        sensor_id=sensor_id, frame_id=frame_id, timestamp_ns=ts,
        width=w, height=h, depth=depth, color=color,
        depth_rvl=bool(flags & FLAG_DEPTH_RVL),
    )
