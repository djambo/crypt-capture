"""
crypt-capture wire protocol — a single synchronized depth+color frame.

One message per captured frame, streamed over TCP from each node to the
central recorder. `frame_id` is the hardware-synced frame index (identical
across all nodes for the same instant, courtesy of the Kinect daisy-chain
sync), which is how the recorder groups the N sensors back together.

Header (little-endian, 36 bytes):
    magic        4s   b"CVF1"
    sensor_id    B    0..N-1
    flags        B    bit0 = depth is RVL-compressed; bit1 = color is aligned RGB;
                      bit2 = color payload is bbox(u16 x0,y0,bw,bh) + JPEG
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
FLAG_COLOR_JPEG = 0x04      # color payload = u16 x0,y0,bw,bh (bbox of the
                            # valid pixels on the strided grid) + a JPEG of
                            # the aligned colour image cropped to that bbox.
                            # ~5-8x smaller than the raw triples — the raw
                            # foreground RGB was ~75% of a subject frame's
                            # bytes, the difference between a WiFi link
                            # carrying 3 cameras at 30 fps or at 8. The relay
                            # decodes + scatters via the same valid mask
                            # (jpeg_color_grid), so everything downstream is
                            # unchanged. Requires cv2 or Pillow on BOTH ends;
                            # either side missing it falls back loudly.


class Frame(object):
    """One synchronized depth+color frame. Plain class (no dataclass) so it
    imports on the Nano's Python 3.6."""

    __slots__ = ("sensor_id", "frame_id", "timestamp_ns", "width", "height",
                 "depth", "color", "depth_rvl", "color_aligned", "stride",
                 "color_jpeg")

    def __init__(self, sensor_id, frame_id, timestamp_ns, width, height,
                 depth, color, depth_rvl=True, color_aligned=False, stride=1,
                 color_jpeg=False):
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
        self.color_jpeg = color_jpeg      # color payload = bbox header + JPEG

    def encode(self):
        flags = FLAG_DEPTH_RVL if self.depth_rvl else 0
        if self.color_aligned:
            flags |= FLAG_COLOR_ALIGNED
        if self.color_jpeg:
            flags |= FLAG_COLOR_JPEG
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
        color_jpeg=bool(flags & FLAG_COLOR_JPEG),
        stride=stride or 1,          # 0 (old/unset) means full resolution
    )


# --- node intrinsics handshake -------------------------------------------
# Each node sends its OWN depth-camera intrinsics to central once on connect,
# so central needs no per-device calib files and scales to N cameras. The
# dimensions are the full-resolution depth grid (intrinsics are full-res; any
# preview --stride is reversed separately on central).

CALIB_MAGIC = b"CCAL"
# magic, sensor_id, w, h, fx, fy, cx, cy, then 8 Brown-Conrady distortion
# coefficients in OpenCV order: k1, k2, p1, p2, k3, k4, k5, k6.
_CALIB = struct.Struct("<4sIHHffff" + "ffffffff")
_REST = struct.Struct("<BBHQQHHII")    # frame header after the 4s magic

# --- node IMU / orientation handshake ------------------------------------
# Each node also sends a per-sensor GRAVITY (down) unit vector derived from the
# Kinect accelerometer, expressed in the depth-camera OPTICAL frame (x right,
# y down, z forward). It gives the cloud an initial orientation (which way is
# down / where the floor is) before any extrinsic calibration. Low-rate and
# static-ish (the rig doesn't move), so it's sent alongside the CCAL handshake,
# keyed by sensor_id. central re-expresses it in the cloud/view frame and
# forwards it to the viewer.
IMU_MAGIC = b"CIMU"
_IMU = struct.Struct("<4sIfff")        # magic, sensor_id, gx, gy, gz (unit vec)

# --- 2D pose keypoints (skeleton) -----------------------------------------
# Nodes that run a pose model on the color image (see docs/skeleton_pose.md)
# ship the detected keypoints as a tiny per-frame message. Each keypoint is
# (joint_id, u, v, z_m, conf): COCO-17 joint id, FULL-RESOLUTION pixel coords
# on the current grid (the color image is pixel-aligned with depth in both
# alignment modes, so (u,v) indexes the depth grid directly), the depth the
# node read at that pixel in METRES (0 = no depth there — consumer may skip),
# and the model's confidence 0..1. Central unprojects each keypoint through
# the sensor's ray table into a metric 3D joint. Low-rate (<=1 per frame,
# ~400 bytes), so it rides the same TCP stream as the frames.
POSE_MAGIC = b"CPOS"
_POSE_HEAD = struct.Struct("<4sIQB")     # magic, sensor_id, timestamp_ns, count
_POSE_KP = struct.Struct("<Bffff")       # joint_id, u, v, z_m, conf

# COCO-17 joint ids (what the standard 2D pose models emit), for reference:
# 0 nose, 1/2 eyes, 3/4 ears, 5/6 shoulders, 7/8 elbows, 9/10 wrists,
# 11/12 hips, 13/14 knees, 15/16 ankles.

# --- grid -> depth extrinsic ---------------------------------------------
# Which camera frame the streamed point grid lives in depends on alignment
# (depth-optical for color_to_depth, color-optical for depth_to_color). To keep
# every alignment registered to ONE canonical frame (the depth optical frame),
# the node sends the rigid transform that maps a grid-frame point into the depth
# frame: P_depth = R·P_grid + t. Identity for color_to_depth; the factory
# COLOR->DEPTH extrinsic for depth_to_color. R is row-major 3x3, t is metres.
EXTRINSIC_MAGIC = b"CEXT"
_EXTRINSIC = struct.Struct("<4sI" + "f" * 12)   # magic, sensor, R(9), t(3)

# --- colour-camera calibration (for textured mesh, docs/textured_mesh.md) -----
# For the textured-mesh render the relay projects each depth point into the
# COLOUR image to get its UV. That needs the colour camera's intrinsics +
# Brown-Conrady distortion (OpenCV order k1,k2,p1,p2,k3,k4,k5,k6) AND the
# DEPTH->COLOR rigid extrinsic (P_color = R·P_depth + t, R row-major 3x3, t
# metres) — none of which the relay has in color_to_depth (there it only gets
# the depth intrinsics). Sent once per (re)connect/reconfig alongside CCAL,
# keyed by sensor_id. w,h are the colour capture resolution the intrinsics are
# defined at (so the relay normalises UV = pixel / (w,h)).
COLOR_CALIB_MAGIC = b"CCLR"
_COLOR_CALIB = struct.Struct("<4sIHH" + "ffff" + "ffffffff" + "ffffffffffff")

# --- per-frame colour texture (JPEG) -----------------------------------------
# One full-resolution colour image per frame for the textured mesh. It carries
# the same capture frame_id as its Frame, and the relay buffers recent textures
# per sensor and pairs each geometry frame with its NEAREST-fid texture
# (preview_server._take_texture) — the JPEG is encoded on a separate latest-wins
# thread on the node, so it no longer arrives strictly before its Frame. format
# 0 = JPEG. Only sent when textured mode is enabled (set_texture) — off by
# default, so nodes/relays that don't use it never see it.
TEXTURE_MAGIC = b"CTEX"
_TEXTURE_HEAD = struct.Struct("<4sIQBHHI")  # magic, sensor, frame_id, fmt, w, h, len
TEXTURE_JPEG = 0

# --- node status events --------------------------------------------------
# Tiny node -> central notifications for state changes the operator must SEE
# (the control plane is fire-and-forget, so without these the viewer can only
# guess with optimistic timers — which lied whenever the node ran slower than
# assumed, e.g. background-plate averaging on a WiFi-throttled capture loop).
# Fixed-size; the relay rebroadcasts them to viewers as JSON TEXT
# ({"type": "node_status", sensor, event}).
STATUS_MAGIC = b"CSTA"
_STATUS = struct.Struct("<4sIB")       # magic, sensor_id, event code
STATUS_BG_CAPTURED = 1                 # background plate done -> subtraction live


def encode_calib(sensor_id, width, height, fx, fy, cx, cy, dist=(0,) * 8):
    d = tuple(dist) + (0.0,) * (8 - len(dist))
    return _CALIB.pack(CALIB_MAGIC, sensor_id, width, height,
                       fx, fy, cx, cy, *d[:8])


def encode_imu(sensor_id, gx, gy, gz):
    """Encode a per-sensor gravity (down) unit vector in the depth optical frame."""
    return _IMU.pack(IMU_MAGIC, sensor_id, gx, gy, gz)


def encode_extrinsic(sensor_id, R, t):
    """Encode the grid->depth rigid transform. R: 9 floats row-major (3x3),
    t: 3 floats (metres). P_depth = R·P_grid + t."""
    vals = list(R)[:9] + list(t)[:3]
    return _EXTRINSIC.pack(EXTRINSIC_MAGIC, sensor_id, *vals)


def encode_color_calib(sensor_id, width, height, fx, fy, cx, cy, dist, R, t):
    """Encode the colour-camera calibration for UV projection (CCLR). dist: 8
    Brown-Conrady coeffs (k1,k2,p1,p2,k3,k4,k5,k6); R: 9 floats row-major
    (DEPTH->COLOR rotation); t: 3 floats metres. w,h = colour capture res."""
    d = tuple(dist) + (0.0,) * (8 - len(dist))
    vals = list(R)[:9] + list(t)[:3]
    return _COLOR_CALIB.pack(COLOR_CALIB_MAGIC, sensor_id, width, height,
                             fx, fy, cx, cy, *(d[:8] + tuple(vals)))


def encode_texture(sensor_id, frame_id, data, width, height, fmt=TEXTURE_JPEG):
    """Encode one colour texture image (CTEX). data = encoded bytes (JPEG)."""
    return _TEXTURE_HEAD.pack(TEXTURE_MAGIC, sensor_id, frame_id, fmt,
                              width, height, len(data)) + data


def encode_status(sensor_id, event):
    """Encode a node status event (CSTA) — e.g. STATUS_BG_CAPTURED."""
    return _STATUS.pack(STATUS_MAGIC, sensor_id, int(event) & 0xFF)


def encode_pose(sensor_id, timestamp_ns, keypoints):
    """Encode one frame's 2D pose keypoints.

    keypoints: iterable of (joint_id, u, v, z_m, conf) — see the CPOS comment
    above for units/frames. Up to 255 keypoints."""
    kps = list(keypoints)[:255]
    out = _POSE_HEAD.pack(POSE_MAGIC, sensor_id, timestamp_ns, len(kps))
    for jid, u, v, z, conf in kps:
        out += _POSE_KP.pack(int(jid) & 0xFF, u, v, z, conf)
    return out


# Fixed-size message types: the magic alone determines the total wire length.
_FIXED_MESSAGE_SIZE = {
    CALIB_MAGIC: _CALIB.size,
    IMU_MAGIC: _IMU.size,
    EXTRINSIC_MAGIC: _EXTRINSIC.size,
    COLOR_CALIB_MAGIC: _COLOR_CALIB.size,
    STATUS_MAGIC: _STATUS.size,
}


def message_buffered(sock):
    """True iff a COMPLETE node->central message is already in `sock`'s local
    receive buffer — i.e. read_message() is guaranteed not to block. Peek-only
    (MSG_PEEK consumes nothing). The caller must know at least one byte is
    readable (e.g. via select) — on an idle blocking socket the peek itself
    would block.

    This is what the relay's drop-stale ingest gates on. Its original check
    was select() alone — "any pending BYTES" — so a single early byte of a
    PARTIALLY-arrived next frame made it discard the complete frame in hand
    and then block inside read_message until the rest trickled in. Under
    ordinary arrival jitter that threw away ~15-20%% of frames with the
    reader mostly idle (measured: 3 subtracted-subject sensors stuck at
    ~22-27 fps in with per-frame work of ~6 ms). Only a FULLY-buffered newer
    message justifies skipping the one already read."""
    try:
        head = sock.recv(_HEADER.size, socket.MSG_PEEK)
    except OSError:
        return True                 # dying socket: let read_message surface it
    if len(head) < 4:
        return False
    magic = head[:4]
    if magic == MAGIC:              # variable: header carries dlen + clen
        if len(head) < _HEADER.size:
            return False
        vals = _HEADER.unpack(head)
        need = _HEADER.size + vals[8] + vals[9]
    elif magic == TEXTURE_MAGIC:    # variable: head carries the JPEG length
        if len(head) < _TEXTURE_HEAD.size:
            return False
        need = _TEXTURE_HEAD.size + struct.unpack_from("<I", head, 21)[0]
    elif magic == POSE_MAGIC:       # variable: head carries the keypoint count
        if len(head) < _POSE_HEAD.size:
            return False
        need = _POSE_HEAD.size + head[16] * _POSE_KP.size
    else:
        need = _FIXED_MESSAGE_SIZE.get(magic)
        if need is None:            # unknown magic: let read_message raise
            return True
    if need <= len(head):
        return True
    try:
        return len(sock.recv(need, socket.MSG_PEEK)) >= need
    except OSError:
        return True


def read_message(sock):
    """Read one node->central message, dispatching on the leading magic.

    Returns ("frame", Frame), ("calib", dict), ("imu", dict), or None on close.
    """
    magic = _recv_exactly(sock, 4)
    if not magic:
        return None
    if magic == CALIB_MAGIC:
        rest = _recv_exactly(sock, _CALIB.size - 4)
        if not rest:
            return None
        vals = struct.unpack("<IHHffff" + "ffffffff", rest)
        sid, w, h, fx, fy, cx, cy = vals[:7]
        return ("calib", {"sensor_id": sid, "width": w, "height": h,
                          "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                          "dist": vals[7:15]})
    if magic == IMU_MAGIC:
        rest = _recv_exactly(sock, _IMU.size - 4)
        if not rest:
            return None
        sid, gx, gy, gz = struct.unpack("<Ifff", rest)
        return ("imu", {"sensor_id": sid, "gravity": (gx, gy, gz)})
    if magic == EXTRINSIC_MAGIC:
        rest = _recv_exactly(sock, _EXTRINSIC.size - 4)
        if not rest:
            return None
        vals = struct.unpack("<I" + "f" * 12, rest)
        return ("extrinsic", {"sensor_id": vals[0],
                              "R": vals[1:10], "t": vals[10:13]})
    if magic == COLOR_CALIB_MAGIC:
        rest = _recv_exactly(sock, _COLOR_CALIB.size - 4)
        if not rest:
            return None
        vals = struct.unpack("<IHH" + "ffff" + "ffffffff" + "ffffffffffff", rest)
        sid, w, h, fx, fy, cx, cy = vals[:7]
        return ("color_calib", {"sensor_id": sid, "width": w, "height": h,
                                "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                                "dist": vals[7:15],
                                "R": vals[15:24], "t": vals[24:27]})
    if magic == TEXTURE_MAGIC:
        rest = _recv_exactly(sock, _TEXTURE_HEAD.size - 4)
        if not rest:
            return None
        sid, fid, fmt, w, h, tlen = struct.unpack("<IQBHHI", rest)
        data = _recv_exactly(sock, tlen) if tlen else b""
        if tlen and not data:
            return None
        return ("texture", {"sensor_id": sid, "frame_id": fid, "format": fmt,
                            "width": w, "height": h, "data": data})
    if magic == STATUS_MAGIC:
        rest = _recv_exactly(sock, _STATUS.size - 4)
        if not rest:
            return None
        sid, event = struct.unpack("<IB", rest)
        return ("status", {"sensor_id": sid, "event": event})
    if magic == POSE_MAGIC:
        rest = _recv_exactly(sock, _POSE_HEAD.size - 4)
        if not rest:
            return None
        sid, ts, count = struct.unpack("<IQB", rest)
        body = _recv_exactly(sock, count * _POSE_KP.size) if count else b""
        if count and not body:
            return None
        kps = [_POSE_KP.unpack_from(body, i * _POSE_KP.size)
               for i in range(count)]
        return ("pose", {"sensor_id": sid, "timestamp_ns": ts,
                         "keypoints": kps})
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
        color_jpeg=bool(flags & FLAG_COLOR_JPEG),
        stride=stride or 1,
    ))
