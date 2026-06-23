"""
crypt-capture wire protocol — a single synchronized depth+color frame.

One message per captured frame, streamed over TCP from each node to the
central recorder. `frame_id` is the hardware-synced frame index (identical
across all nodes for the same instant, courtesy of the Kinect daisy-chain
sync), which is how the recorder groups the N sensors back together.

Header (little-endian, 36 bytes):
    magic        4s   b"CVF1"
    sensor_id    B    0..N-1
    flags        B    bit0 = depth is RVL-compressed; bit1 = color is aligned RGB
    stride       H    preview downsample factor applied on the node (1 = full res);
                      depth/color are width*height at this stride, and pixel (u,v)
                      maps to original (u*stride, v*stride) for unprojection
    frame_id     Q    hardware-synced frame index
    timestamp_ns Q    node capture timestamp (ns)
    width        H    (possibly strided) depth width
    height       H    (possibly strided) depth height
    depth_len    I    bytes of depth payload (RVL or raw u16)
    color_len    I    bytes of color payload
Payload: depth_bytes ++ color_bytes
"""

import socket
import struct

MAGIC = b"CVF1"
_HEADER = struct.Struct("<4sBBHQQHHII")
HEADER_SIZE = _HEADER.size

FLAG_DEPTH_RVL = 0x01
FLAG_COLOR_ALIGNED = 0x02   # color payload = raw uint8 RGB of valid pixels,
                            # row-major, one triple per non-zero depth pixel


class Frame(object):
    """One synchronized depth+color frame. Plain class (no dataclass) so it
    imports on the Nano's Python 3.6."""

    __slots__ = ("sensor_id", "frame_id", "timestamp_ns", "width", "height",
                 "depth", "color", "depth_rvl", "color_aligned", "stride")

    def __init__(self, sensor_id, frame_id, timestamp_ns, width, height,
                 depth, color, depth_rvl=True, color_aligned=False, stride=1):
        self.sensor_id = sensor_id        # 0..N-1
        self.frame_id = frame_id          # hardware-synced frame index
        self.timestamp_ns = timestamp_ns  # node capture timestamp (ns)
        self.width = width                # (strided) depth width
        self.height = height              # (strided) depth height
        self.depth = depth                # RVL-compressed (or raw u16)
        self.color = color                # opaque encoded color payload
        self.depth_rvl = depth_rvl
        self.color_aligned = color_aligned  # color is depth-aligned RGB (see flag)
        self.stride = stride              # node-side preview downsample (1 = full)

    def encode(self):
        flags = FLAG_DEPTH_RVL if self.depth_rvl else 0
        if self.color_aligned:
            flags |= FLAG_COLOR_ALIGNED
        header = _HEADER.pack(
            MAGIC, self.sensor_id, flags, self.stride,
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
    magic, sensor_id, flags, stride, frame_id, ts, w, h, dlen, clen = _HEADER.unpack(header)
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
        color_aligned=bool(flags & FLAG_COLOR_ALIGNED),
        stride=stride or 1,          # 0 (old/unset) means full resolution
    )


# --- node intrinsics handshake -------------------------------------------
# Each node sends its OWN depth-camera intrinsics to central once on connect,
# so central needs no per-device calib files and scales to N cameras. The
# dimensions are the full-resolution depth grid (intrinsics are full-res; any
# preview --stride is reversed separately on central).

CALIB_MAGIC = b"CCAL"
_CALIB = struct.Struct("<4sIHHffff")   # magic, sensor_id, w, h, fx, fy, cx, cy
_REST = struct.Struct("<BBHQQHHII")    # frame header after the 4s magic


def encode_calib(sensor_id, width, height, fx, fy, cx, cy):
    return _CALIB.pack(CALIB_MAGIC, sensor_id, width, height, fx, fy, cx, cy)


def read_message(sock):
    """Read one node->central message, dispatching on the leading magic.

    Returns ("frame", Frame), ("calib", dict), or None on clean close.
    """
    magic = _recv_exactly(sock, 4)
    if not magic:
        return None
    if magic == CALIB_MAGIC:
        rest = _recv_exactly(sock, _CALIB.size - 4)
        if not rest:
            return None
        sid, w, h, fx, fy, cx, cy = struct.unpack("<IHHffff", rest)
        return ("calib", {"sensor_id": sid, "width": w, "height": h,
                          "fx": fx, "fy": fy, "cx": cx, "cy": cy})
    if magic != MAGIC:
        raise ValueError("bad magic %r — stream desynced" % (magic,))
    head = _recv_exactly(sock, _REST.size)
    if not head:
        return None
    sensor_id, flags, stride, frame_id, ts, w, h, dlen, clen = _REST.unpack(head)
    depth = _recv_exactly(sock, dlen) if dlen else b""
    color = _recv_exactly(sock, clen) if clen else b""
    if (dlen and not depth) or (clen and not color):
        return None
    return ("frame", Frame(
        sensor_id=sensor_id, frame_id=frame_id, timestamp_ns=ts,
        width=w, height=h, depth=depth, color=color,
        depth_rvl=bool(flags & FLAG_DEPTH_RVL),
        color_aligned=bool(flags & FLAG_COLOR_ALIGNED),
        stride=stride or 1,
    ))
