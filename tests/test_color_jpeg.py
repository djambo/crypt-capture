"""
JPEG foreground-colour wire tests (FLAG_COLOR_JPEG) — headless; numpy
required, cv2 or Pillow required for the codec parts (skipped without), NO
pyk4a needed (kinect_node imported behind a stub, same as tests/test_ir.py).

The raw foreground RGB triples are ~75% of a subject frame's bytes — the wire
cost that starves a WiFi link. FLAG_COLOR_JPEG replaces them with a
bbox-cropped JPEG of the aligned colour image; the relay decodes it back onto
the full grid (jpeg_color_grid) so everything downstream is unchanged.

Covers: node encode -> relay decode round-trip (per-point colour matches the
raw path within JPEG tolerance), the size win, the raw fallback (quality 0 /
empty frame), decoder robustness (garbage/short/out-of-range payloads), and
the Frame flag surviving the wire.

Run: python3 -m tests.test_color_jpeg
"""

import os
import socket
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
except ImportError:
    print("color-jpeg tests: skipped (no numpy)")
    sys.exit(0)

if "pyk4a" not in sys.modules:
    stub = types.ModuleType("pyk4a")
    stub.PyK4A = type("PyK4A", (), {})
    stub.Config = type("Config", (), {})
    stub.DepthMode = types.SimpleNamespace(
        NFOV_UNBINNED=1, NFOV_2X2BINNED=2, WFOV_2X2BINNED=3, WFOV_UNBINNED=4)
    stub.ColorResolution = types.SimpleNamespace(
        RES_720P=1, RES_1080P=2, RES_1440P=3, RES_1536P=4, RES_2160P=5,
        RES_3072P=6)
    stub.FPS = types.SimpleNamespace(FPS_5=1, FPS_15=2, FPS_30=3)
    stub.ImageFormat = types.SimpleNamespace(COLOR_BGRA32=1)
    stub.WiredSyncMode = types.SimpleNamespace(
        STANDALONE=1, MASTER=2, SUBORDINATE=3)
    stub.CalibrationType = types.SimpleNamespace(ACCEL=1, DEPTH=2, COLOR=3)
    sys.modules["pyk4a"] = stub

from central.preview_server import jpeg_color_grid
from node.kinect_node import _encode_jpeg, _process_frame
from protocol.frame import Frame, read_frame


def _scene(w=64, h=48):
    """A subject blob of valid depth + a smooth BGRA colour gradient (smooth
    like a real camera image — a modulo sawtooth would make JPEG ring on the
    artificial 255->0 cliffs and fail the tolerance for the wrong reason)."""
    depth = np.zeros((h, w), np.uint16)
    depth[10:40, 20:50] = 1500                     # the "subject"
    yy, xx = np.mgrid[0:h, 0:w]
    csrc = np.zeros((h, w, 4), np.uint8)
    csrc[..., 0] = np.clip(xx * 3, 0, 255)         # B
    csrc[..., 1] = np.clip(yy * 4, 0, 255)         # G
    csrc[..., 2] = np.clip(xx + yy * 2, 0, 255)    # R
    csrc[..., 3] = 255
    return depth, csrc


def test_round_trip_matches_raw():
    if _encode_jpeg(np.zeros((4, 4, 4), np.uint8)) is None:
        print("round-trip: skipped (no cv2/Pillow)")
        return
    depth, csrc = _scene()
    raw = _process_frame(depth, csrc, None, 50, 0, 1,
                         color_jpeg_quality=0)
    jpg = _process_frame(depth, csrc, None, 50, 0, 1,
                         color_jpeg_quality=90)
    assert raw[9] is False and jpg[9] is True, "color_jpeg flags"
    assert raw[5] == jpg[5], "same point count either path"

    h, w = depth.shape
    grid = jpeg_color_grid(jpg[1], w, h)
    assert grid is not None, "relay failed to decode the JPEG payload"
    valid = depth != 0
    got = grid[valid].astype(np.int16)
    want = np.frombuffer(raw[1], np.uint8).reshape(-1, 3).astype(np.int16)
    assert got.shape == want.shape
    err = np.abs(got - want)
    assert err.mean() < 6 and err.max() < 48, \
        "JPEG colour drifted from raw (mean %.1f max %d)" % (err.mean(),
                                                             err.max())
    # The point of the exercise: meaningfully smaller than the raw triples.
    assert len(jpg[1]) < len(raw[1]) * 0.5, \
        "JPEG payload %d not < 50%% of raw %d" % (len(jpg[1]), len(raw[1]))
    print("round-trip: OK (%d -> %d bytes, mean err %.2f)"
          % (len(raw[1]), len(jpg[1]), err.mean()))


def test_fallbacks():
    depth, csrc = _scene()
    # quality 0 = raw path, flag off.
    out = _process_frame(depth, csrc, None, 50, 0, 1, color_jpeg_quality=0)
    assert out[9] is False and len(out[1]) == out[5] * 3
    # Empty frame: nothing to encode -> raw path (empty payload), flag off.
    out = _process_frame(np.zeros_like(depth), csrc, None, 50, 0, 1,
                         color_jpeg_quality=90)
    assert out[9] is False and out[5] == 0 and len(out[1]) == 0
    print("fallbacks: OK")


def test_decoder_robustness():
    assert jpeg_color_grid(b"", 64, 48) is None
    assert jpeg_color_grid(b"\x00" * 8, 64, 48) is None          # empty bbox
    import struct as _s
    bad_bbox = _s.pack("<HHHH", 60, 40, 20, 20) + b"\xff\xd8junk"
    assert jpeg_color_grid(bad_bbox, 64, 48) is None             # out of range
    ok_bbox = _s.pack("<HHHH", 0, 0, 8, 8) + b"not a jpeg"
    assert jpeg_color_grid(ok_bbox, 64, 48) is None              # corrupt data
    print("decoder robustness: OK")


def test_flag_survives_the_wire():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    conn, _ = srv.accept()
    try:
        f = Frame(sensor_id=1, frame_id=7, timestamp_ns=1, width=4, height=4,
                  depth=b"\x01\x02", color=b"\x03\x04", depth_rvl=True,
                  color_aligned=True, color_jpeg=True)
        cli.sendall(f.encode())
        g = read_frame(conn)
        assert g is not None and g.color_jpeg is True and g.color_aligned
        assert g.color == b"\x03\x04"
        f2 = Frame(sensor_id=1, frame_id=8, timestamp_ns=1, width=4, height=4,
                   depth=b"", color=b"", color_aligned=False)
        cli.sendall(f2.encode())
        g2 = read_frame(conn)
        assert g2 is not None and g2.color_jpeg is False
    finally:
        cli.close()
        conn.close()
        srv.close()
    print("wire flag: OK")


if __name__ == "__main__":
    test_round_trip_matches_raw()
    test_fallbacks()
    test_decoder_robustness()
    test_flag_survives_the_wire()
    print("\nALL COLOR-JPEG TESTS PASSED")
