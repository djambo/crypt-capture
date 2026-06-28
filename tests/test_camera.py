"""
Camera-mode table + set_camera command tests (headless; no pyk4a needed).

These cover node/camera_modes.py — the pure logic shared by kinect_node and
sim_node for the live camera controls (depth FOV mode / color res / fps /
alignment). The pyk4a enum mapping in kinect_node is a thin lookup and isn't
exercised here (it needs the SDK).

Run: python3 -m tests.test_camera
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from node import camera_modes as cm


def test_fps_clamp():
    # NFOV unbinned + 720p both do 30.
    assert cm.clamp_fps(30, "NFOV_UNBINNED", "720P") == 30
    # WFOV unbinned caps the sensor at 15.
    assert cm.clamp_fps(30, "WFOV_UNBINNED", "720P") == 15
    # 3072p color caps at 15 too.
    assert cm.clamp_fps(30, "NFOV_UNBINNED", "3072P") == 15
    # A lower request is honored.
    assert cm.clamp_fps(5, "NFOV_UNBINNED", "720P") == 5
    # An out-of-set value falls back to 30 then clamps.
    assert cm.clamp_fps(99, "WFOV_UNBINNED", "720P") == 15


def test_grid_dims_follow_alignment():
    # color_to_depth -> depth grid; depth_to_color -> color grid.
    assert cm.grid_dims("NFOV_UNBINNED", "720P", "color_to_depth") == (640, 576)
    assert cm.grid_dims("NFOV_UNBINNED", "720P", "depth_to_color") == (1280, 720)
    assert cm.grid_dims("WFOV_UNBINNED", "1080P", "color_to_depth") == (1024, 1024)
    assert cm.grid_dims("WFOV_UNBINNED", "1080P", "depth_to_color") == (1920, 1080)


def test_apply_command_restart_flags():
    cfg = dict(cm.DEFAULTS)

    # Depth mode change requires a sensor restart.
    changed = cm.apply_camera_command(cfg, {"depth_mode": "WFOV_UNBINNED"})
    assert cfg["depth_mode"] == "WFOV_UNBINNED"
    assert changed["restart"] is True
    assert changed["depth_mode"] == "WFOV_UNBINNED"

    # Alignment change is a free per-frame switch (no restart). The default is
    # depth_to_color, so switch the other way to exercise a real change.
    changed = cm.apply_camera_command(cfg, {"align": "color_to_depth"})
    assert cfg["align"] == "color_to_depth"
    assert changed["restart"] is False
    assert changed["align"] == "color_to_depth"

    # A no-op command reports nothing changed (only the restart key).
    changed = cm.apply_camera_command(cfg, {"align": "color_to_depth"})
    assert list(changed.keys()) == ["restart"]
    assert changed["restart"] is False

    # Unknown / invalid values are ignored.
    changed = cm.apply_camera_command(cfg, {"depth_mode": "BOGUS", "fps": "x"})
    assert list(changed.keys()) == ["restart"]
    assert cfg["depth_mode"] == "WFOV_UNBINNED"

    # fps change requires a restart and is validated against FPS_CHOICES.
    changed = cm.apply_camera_command(cfg, {"fps": 15})
    assert cfg["fps"] == 15 and changed["restart"] is True
    changed = cm.apply_camera_command(cfg, {"fps": 7})
    assert cfg["fps"] == 15 and list(changed.keys()) == ["restart"]


def run():
    test_fps_clamp()
    test_grid_dims_follow_alignment()
    test_apply_command_restart_flags()
    print("camera tests: OK")


if __name__ == "__main__":
    run()
