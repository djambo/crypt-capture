"""
Textured-mesh data-plane tests (docs/textured_mesh.md): the CCLR/CTEX node
messages, the relay's UV projection, and the CPV1/CPV2 UV + texture wire blocks.

Validates the producer side deterministically (no sockets): project known
geometry into a known colour camera and check the UVs, then round-trip the wire
blocks. The viewer decoder is exercised separately in the crypt repo.

Run: python3 -m tests.test_texture
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from protocol import frame as fr
import central.preview_server as ps


def _fake_reader(data):
    class B(object):
        def __init__(self, d):
            self.d = d
            self.i = 0

        def recv(self, n):
            c = self.d[self.i:self.i + n]
            self.i += len(c)
            return c
    return B(data)


def test_frame_messages_roundtrip():
    enc = fr.encode_color_calib(
        3, 2048, 1536, 900.0, 901.0, 1024.0, 768.0,
        (0.1, 0.2, 0.001, 0.002, 0.3, 0.01, 0.02, 0.03),
        (1, 0, 0, 0, 1, 0, 0, 0, 1), (0.032, -0.002, 0.004))
    kind, msg = fr.read_message(_fake_reader(enc))
    assert kind == "color_calib"
    assert msg["sensor_id"] == 3 and msg["width"] == 2048 and msg["height"] == 1536
    assert abs(msg["fx"] - 900.0) < 1e-3 and abs(msg["t"][0] - 0.032) < 1e-6
    assert len(msg["dist"]) == 8 and len(msg["R"]) == 9 and len(msg["t"]) == 3

    jpeg = b"\xff\xd8\xff\xe0FAKEJPEG\xff\xd9"
    kind, msg = fr.read_message(_fake_reader(
        fr.encode_texture(3, 999, jpeg, 2048, 1536)))
    assert kind == "texture"
    assert msg["frame_id"] == 999 and msg["format"] == 0 and msg["data"] == jpeg
    assert msg["crop"] == (0.0, 0.0, 1.0, 1.0)     # default = whole frame
    # CTEX carries the subject crop rect (node crops the JPEG to the subject).
    kind, msg = fr.read_message(_fake_reader(
        fr.encode_texture(3, 42, jpeg, 512, 640, crop=(0.2, 0.3, 0.7, 0.85))))
    assert kind == "texture" and msg["data"] == jpeg
    assert all(abs(a - b) < 1e-6 for a, b in
               zip(msg["crop"], (0.2, 0.3, 0.7, 0.85)))


def _pinhole_rays(w, h, fx, fy, cx, cy):
    us = (np.arange(w) - cx) / fx
    vs = (np.arange(h) - cy) / fy
    rx = np.tile(us, (h, 1)).astype(np.float32)
    ry = np.tile(vs[:, None], (1, w)).astype(np.float32)
    return rx, ry


def test_unproject_uv_recovers_pixels():
    # Colour camera == depth camera (same pinhole, identity extrinsic, no
    # distortion): the UV of the point unprojected from depth pixel (u,v) must
    # come back to that pixel, normalised by the colour image size.
    w, h = 8, 6
    fx = fy = 10.0
    cx, cy = 4.0, 3.0
    rx, ry = _pinhole_rays(w, h, fx, fy, cx, cy)
    depth = np.full((h, w), 1000, dtype=np.uint16)          # 1 m, all valid
    calib = {"fx": fx, "fy": fy, "cx": cx, "cy": cy,
             "dist": (0.0,) * 8, "R": (1, 0, 0, 0, 1, 0, 0, 0, 1),
             "t": (0, 0, 0), "width": w, "height": h}
    xyz, rgb, grid, uv = ps.unproject(depth.tobytes(), w, h, rx, ry, 1, 1,
                                      color_calib=calib)
    assert uv.shape == (w * h, 2)
    assert uv.min() >= 0.0 and uv.max() <= 1.0
    # Points are in row-major (v*w+u) order for an all-valid grid.
    for v in range(h):
        for u in range(w):
            k = v * w + u
            assert abs(uv[k, 0] - u / w) < 1e-5, (u, v, uv[k, 0])
            assert abs(uv[k, 1] - v / h) < 1e-5, (u, v, uv[k, 1])


def test_wire_uv_texture_blocks():
    # build_message with uv + texture sets both flags and appends the blocks
    # after grid; parse them back and check the UVs (uint16-normalised) and the
    # verbatim texture bytes. Do it for both wire formats.
    n = 5
    xyz = np.random.RandomState(0).rand(n, 3).astype(np.float32)
    uv = np.array([[0.0, 0.0], [1.0, 1.0], [0.25, 0.5],
                   [0.5, 0.25], [0.999, 0.001]], dtype=np.float32)
    jpeg = b"\xff\xd8" + bytes(range(200)) + b"\xff\xd9"
    crop = (0.15, 0.25, 0.65, 0.9)
    texture = {"format": 0, "width": 512, "height": 640, "data": jpeg,
               "crop": crop}
    grid = (n, 1, np.arange(n, dtype=np.uint32))
    for fmt in ("cpv1", "cpv2"):
        out = ps.build_message(1, 7, xyz, None, None, grid, fmt=fmt,
                               uv=uv, texture=texture)
        flags = struct.unpack_from("<I", out, 4)[0]
        assert flags & ps.FLAG_UV and flags & ps.FLAG_TEXTURE
        # The texture is the LAST block: locate it by its trailing length.
        # Header of the texture block = <BHHI> + crop <ffff> right before the JPEG.
        tpos = out.rfind(jpeg)
        assert tpos >= 0
        fmt_b, tw, th, tlen, u0, v0, u1, v1 = struct.unpack_from(
            "<BHHIffff", out, tpos - 25)
        assert fmt_b == 0 and tw == 512 and th == 640 and tlen == len(jpeg)
        assert all(abs(a - b) < 1e-6 for a, b in zip((u0, v0, u1, v1), crop))
        # The UV block is the 2*n uint16 immediately before the texture header.
        uv_bytes = out[tpos - 25 - n * 4:tpos - 25]
        q = np.frombuffer(uv_bytes, dtype="<u2").reshape(n, 2)
        back = q.astype(np.float32) / 65535.0
        assert np.allclose(back, uv, atol=1.0 / 65535 + 1e-6), (fmt, back, uv)


def test_uv_crop_remap():
    # The node crops the mesh JPEG to the subject bbox; the relay re-bases the
    # full-image UVs onto the cropped image (_remap_uv_to_crop), and the GPU-mesh
    # shader does the identical remap from the crop rect. Compose the relay remap
    # with its inverse (what the viewer/shader effectively undoes when sampling)
    # and check UVs inside the crop map linearly to [0,1], outside clamp.
    crop = (0.2, 0.4, 0.6, 0.8)          # du=0.4, dv=0.4
    uv = np.array([[0.2, 0.4],           # crop corner -> (0,0)
                   [0.6, 0.8],           # far corner  -> (1,1)
                   [0.4, 0.6],           # centre      -> (0.5,0.5)
                   [0.1, 0.9]],          # outside     -> clamped
                  dtype=np.float32)
    out = ps._remap_uv_to_crop(uv, crop)
    assert np.allclose(out[0], [0.0, 0.0], atol=1e-6)
    assert np.allclose(out[1], [1.0, 1.0], atol=1e-6)
    assert np.allclose(out[2], [0.5, 0.5], atol=1e-6)
    assert out[3, 0] == 0.0 and out[3, 1] == 1.0    # clamped both axes
    # Identity crop leaves UVs untouched (same object, no copy needed).
    assert ps._remap_uv_to_crop(uv, (0.0, 0.0, 1.0, 1.0)) is uv
    assert ps._remap_uv_to_crop(uv, None) is uv


def test_take_texture_staleness_window():
    # When the node STOPS sending CTEX (set_texture off, IR colour mode) the
    # last buffered JPEG must not be re-paired with live geometry forever —
    # that froze an RGB photo over IR-grey / moving frames. Pairing is bounded
    # to TEXTURE_BUFFER frames of fid skew; past it the frame goes textureless.
    import collections
    import types

    srv = types.SimpleNamespace(_pending_texture={})
    take = lambda sid, fid: ps.PreviewServer._take_texture(srv, sid, fid)

    assert take(0, 100) is None                       # nothing buffered
    buf = collections.deque(maxlen=ps.TEXTURE_BUFFER)
    srv._pending_texture[0] = buf
    buf.append((100, "tex100"))
    assert take(0, 100) == "tex100"                   # exact pair
    assert take(0, 100 + ps.TEXTURE_BUFFER) == "tex100"  # inside the window
    assert take(0, 101 + ps.TEXTURE_BUFFER) is None   # stream stopped -> drop
    assert not buf, "long-stale textures must be cleared"
    # A far-FUTURE texture (fid skew in the other direction, e.g. buffered
    # pre-restart textures vs a node whose counter reset) is KEPT — geometry
    # fids may catch up to it; only the stale-past case clears the buffer.
    buf.append((900, "texFuture"))
    assert take(0, 5) is None
    assert buf, "future-fid textures survive until geometry fids catch up"
    assert take(0, 890) == "texFuture"


def run():
    test_frame_messages_roundtrip()
    test_unproject_uv_recovers_pixels()
    test_wire_uv_texture_blocks()
    test_uv_crop_remap()
    test_take_texture_staleness_window()
    print("texture tests: OK")


if __name__ == "__main__":
    run()
