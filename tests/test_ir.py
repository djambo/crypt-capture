"""
IR colour mode tests (set_ir) — headless; numpy required, NO pyk4a needed
(kinect_node is imported behind a stub module so its pure-NumPy worker stage
is unit-testable off-device).

Covers: the sqrt tone map (_ir_to_gray endpoints/monotonicity/clip), the
_process_frame IR branch (grey payload paired 1:1 with the valid depth mask,
exactly where the colour payload would go), and sim_node.color_to_gray (the
headless stand-in — grey means R==G==B on the wire).

Run: python3 -m tests.test_ir
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
except ImportError:
    print("ir tests: skipped (no numpy)")
    sys.exit(0)

# kinect_node imports pyk4a at module level; stub just enough of it that the
# import succeeds (only the capture path touches the SDK — the worker stage
# under test is pure NumPy).
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
    stub.CalibrationType = types.SimpleNamespace(
        ACCEL=1, DEPTH=2, COLOR=3)
    sys.modules["pyk4a"] = stub

from node.kinect_node import IR_CLIP, _ir_to_gray, _process_frame
from node.sim_node import color_to_gray
from protocol import rvl


def test_tone_map():
    v = np.array([0, 1, int(IR_CLIP) // 4, int(IR_CLIP), 5000, 65535],
                 dtype=np.uint16)
    g = _ir_to_gray(v)
    assert g.dtype == np.uint8 and g.shape == v.shape
    assert g[0] == 0, "zero IR must be black"
    assert g[3] == 255, "IR at the clip must be full white"
    assert g[4] == 255 and g[5] == 255, "above the clip saturates"
    # sqrt curve: quarter scale lands at half brightness (mid-range lift).
    assert abs(int(g[2]) - 127) <= 1, "sqrt tone map: 1/4 clip -> ~1/2 white"
    # monotonic over the whole LUT range
    ramp = _ir_to_gray(np.arange(65536, dtype=np.uint16))
    assert np.all(np.diff(ramp.astype(np.int16)) >= 0), "must be monotonic"
    # 2D shape passes through
    img = _ir_to_gray(np.full((4, 6), 250, np.uint16))
    assert img.shape == (4, 6)
    print("tone map: OK")


def test_process_frame_ir_branch():
    h, w = 12, 16
    depth = np.zeros((h, w), np.uint16)
    depth[3:9, 4:12] = 1500                       # a valid "subject" patch
    ir = np.zeros((h, w), np.uint16)
    ir[3:9, 4:12] = np.arange(48, dtype=np.uint16).reshape(6, 8) * 20

    comp, color, aligned, gw, gh, pts, _, _ = _process_frame(
        depth, None, None, 60, 0, 1, ir_src=ir)
    assert aligned and (gw, gh) == (w, h)
    assert pts == 48 and len(color) == pts * 3
    tri = np.frombuffer(color, np.uint8).reshape(-1, 3)
    assert np.all(tri[:, 0] == tri[:, 1]) and np.all(tri[:, 1] == tri[:, 2]), \
        "IR payload must be grey (R==G==B)"
    # paired 1:1 with the valid depth pixels, in row-major mask order — the
    # exact contract the relay's colour pairing relies on
    expect = _ir_to_gray(ir)[depth != 0]
    assert np.array_equal(tri[:, 0], expect), "grey must be the masked IR"
    # depth path untouched by the IR branch
    back = np.asarray(rvl.decompress(comp, gw * gh),
                      dtype=np.uint16).reshape(h, w)
    assert np.array_equal(back, depth)

    # ir_src=None falls back to the camera-colour path (BGRA -> RGB)
    csrc = np.zeros((h, w, 4), np.uint8)
    csrc[..., 0] = 10                              # B
    csrc[..., 2] = 30                              # R
    _, color2, aligned2, _, _, _, _, _ = _process_frame(
        depth, csrc, None, 60, 0, 1)
    tri2 = np.frombuffer(color2, np.uint8).reshape(-1, 3)
    assert aligned2 and np.all(tri2[0] == (30, 0, 10)), "colour path unchanged"

    # stride applies to the IR image exactly like depth/colour
    comp3, color3, _, gw3, gh3, pts3, _, _ = _process_frame(
        depth, None, None, 60, 0, 2, ir_src=ir)
    assert (gw3, gh3) == (w // 2, h // 2) and len(color3) == pts3 * 3
    expect3 = _ir_to_gray(ir[::2, ::2])[depth[::2, ::2] != 0]
    tri3 = np.frombuffer(color3, np.uint8).reshape(-1, 3)
    assert np.array_equal(tri3[:, 0], expect3)
    print("process_frame IR branch: OK")


def test_sim_gray():
    color = bytes((200, 100, 50, 0, 0, 0, 255, 255, 255))
    gray = color_to_gray(color)
    assert len(gray) == len(color)
    for i in range(0, len(gray), 3):
        assert gray[i] == gray[i + 1] == gray[i + 2], "sim IR must be grey"
    assert gray[3] == 0 and gray[6] == 255, "luminance endpoints"
    print("sim color_to_gray: OK")


if __name__ == "__main__":
    test_tone_map()
    test_process_frame_ir_branch()
    test_sim_gray()
    print("\nALL IR TESTS PASSED")
