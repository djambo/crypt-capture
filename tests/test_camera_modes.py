"""
Tests for node/camera_modes.py — the hardware-independent camera-control model.

Stdlib only (no numpy/pyk4a), so it runs anywhere:
    python3 -m tests.test_camera_modes
"""

from node import camera_modes as cm


def test_defaults_and_merge():
    cfg, grid, notes = cm.resolve(cm.DEFAULT_CONFIG, {})
    assert cfg == {"depth_mode": "NFOV_UNBINNED", "color_resolution": "720P",
                   "fps": 30, "geometry": "depth"}, cfg
    assert grid == cm.DEPTH_MODES["NFOV_UNBINNED"], grid
    assert notes == [], notes

    # A partial request merges onto the current config; None values are ignored.
    cfg, _, _ = cm.resolve(cfg, {"depth_mode": "WFOV_2X2BINNED", "fps": None})
    assert cfg["depth_mode"] == "WFOV_2X2BINNED"
    assert cfg["color_resolution"] == "720P"   # unchanged
    assert cfg["fps"] == 30


def test_case_insensitive_names():
    cfg, _, _ = cm.resolve({}, {"depth_mode": "nfov_unbinned",
                                "color_resolution": "1080p",
                                "geometry": "Color"})
    assert cfg["depth_mode"] == "NFOV_UNBINNED"
    assert cfg["color_resolution"] == "1080P"
    assert cfg["geometry"] == "color"


def test_geometry_selects_grid():
    # depth geometry -> depth-mode grid; color geometry -> color-resolution grid.
    _, grid_d, _ = cm.resolve({}, {"depth_mode": "NFOV_UNBINNED",
                                   "geometry": "depth"})
    assert grid_d == cm.DEPTH_MODES["NFOV_UNBINNED"]

    _, grid_c, _ = cm.resolve({}, {"color_resolution": "1080P",
                                   "geometry": "color"})
    assert grid_c == cm.COLOR_RESOLUTIONS["1080P"]
    assert grid_c[0] == 1920 and grid_c[1] == 1080


def test_fps_clamped_for_wfov_unbinned():
    cfg, _, notes = cm.resolve({}, {"depth_mode": "WFOV_UNBINNED", "fps": 30})
    assert cfg["fps"] == 15, cfg
    assert notes and "clamped" in notes[0]


def test_fps_clamped_for_3072p_color():
    cfg, _, notes = cm.resolve({}, {"color_resolution": "3072P", "fps": 30})
    assert cfg["fps"] == 15, cfg
    assert notes

    # 15 fps is fine for 3072P -> no clamp, no note.
    cfg, _, notes = cm.resolve({}, {"color_resolution": "3072P", "fps": 15})
    assert cfg["fps"] == 15 and notes == []


def test_legal_combo_keeps_30():
    cfg, _, notes = cm.resolve({}, {"depth_mode": "NFOV_UNBINNED",
                                    "color_resolution": "2160P", "fps": 30})
    assert cfg["fps"] == 30 and notes == []


def test_invalid_names_raise():
    for bad in ({"depth_mode": "ULTRAFOV"},
                {"color_resolution": "8K"},
                {"geometry": "sideways"},
                {"fps": 24}):
        try:
            cm.resolve({}, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for %r" % (bad,))


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("all %d camera-mode tests passed" % len(fns))


if __name__ == "__main__":
    _run()
