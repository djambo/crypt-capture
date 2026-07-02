"""
Azure Kinect capture-mode tables — pure data, NO pyk4a import.

Both the real node (`node/kinect_node.py`) and the simulator (`node/sim_node.py`)
share these so they agree on grid dimensions and fps limits, and so the logic is
unit-testable on a box without the SDK (`tests/test_camera.py`). `kinect_node`
maps these string names onto the pyk4a enums; everything else works off the
strings and dimensions here.

The user picks a *depth FOV mode* and an *alignment direction* live from the UI
(`set_camera` control command); the node restarts the sensor as needed and the
stream adapts. Python-3.6-safe (dicts + tuples only).
"""

# Depth FOV mode -> grid (width, height) and the sensor's max fps for that mode.
# (Azure Kinect DK datasheet.) WFOV unbinned is 1 MP so the sensor caps it at
# 15 fps; the rest reach 30.
DEPTH_MODES = {
    "NFOV_UNBINNED":  {"dims": (640, 576),   "max_fps": 30},
    "NFOV_2X2BINNED": {"dims": (320, 288),   "max_fps": 30},
    "WFOV_2X2BINNED": {"dims": (512, 512),   "max_fps": 30},
    "WFOV_UNBINNED":  {"dims": (1024, 1024), "max_fps": 15},
}

# Color resolution -> (width, height) and max fps. 3072p (12 MP, 4:3) caps at
# 15 fps. These only change the cloud meaningfully in depth_to_color alignment,
# where the point grid IS the color image.
COLOR_RESOLUTIONS = {
    "720P":  {"dims": (1280, 720),  "max_fps": 30},
    "1080P": {"dims": (1920, 1080), "max_fps": 30},
    "1440P": {"dims": (2560, 1440), "max_fps": 30},
    "1536P": {"dims": (2048, 1536), "max_fps": 30},
    "2160P": {"dims": (3840, 2160), "max_fps": 30},
    "3072P": {"dims": (4096, 3072), "max_fps": 15},
}

# Alignment direction = which camera's geometry the streamed point grid lives in:
#   color_to_depth : color is warped into the DEPTH grid (one point per depth
#                    pixel, point count = depth res). The original/default path.
#   depth_to_color : depth is warped into the COLOR grid (one point per COLOR
#                    pixel) -> far more color detail / a denser cloud, at the
#                    cost of more points + holes where depth is sparse.
ALIGN_MODES = ("color_to_depth", "depth_to_color")

FPS_CHOICES = (5, 15, 30)

DEFAULTS = {
    "depth_mode": "NFOV_UNBINNED",
    "color_resolution": "720P",
    "fps": 30,
    # color_to_depth is the default: native depth grid (~2.5x smaller than the
    # colour grid) -> the node holds a sensor-limited 30 fps. depth_to_color
    # (more colour detail; registers via CEXT) costs ~2.5x the masking/RVL work.
    "align": "color_to_depth",
}


def max_fps(depth_mode, color_resolution):
    """Highest fps both the depth mode and color resolution support."""
    return min(DEPTH_MODES[depth_mode]["max_fps"],
               COLOR_RESOLUTIONS[color_resolution]["max_fps"])


def clamp_fps(fps, depth_mode, color_resolution):
    """Snap a requested fps down to what the chosen modes actually allow."""
    f = fps if fps in FPS_CHOICES else 30
    return min(f, max_fps(depth_mode, color_resolution))


def grid_dims(depth_mode, color_resolution, align):
    """(width, height) of the streamed point grid for a config — depth res for
    color_to_depth, color res for depth_to_color."""
    if align == "depth_to_color":
        return COLOR_RESOLUTIONS[color_resolution]["dims"]
    return DEPTH_MODES[depth_mode]["dims"]


def apply_camera_command(cfg, cmd):
    """Fold a `set_camera` command dict into `cfg` (mutated in place), ignoring
    unknown/unchanged fields. Returns a dict of what actually changed, plus a
    "restart" bool: True when the change requires restarting the sensor (depth
    mode / color resolution / fps) vs. a free per-frame switch (alignment).
    """
    changed = {}
    restart = False

    dm = cmd.get("depth_mode")
    if dm in DEPTH_MODES and dm != cfg["depth_mode"]:
        cfg["depth_mode"] = dm
        changed["depth_mode"] = dm
        restart = True

    cr = cmd.get("color_resolution")
    if cr in COLOR_RESOLUTIONS and cr != cfg["color_resolution"]:
        cfg["color_resolution"] = cr
        changed["color_resolution"] = cr
        restart = True

    if "fps" in cmd:
        try:
            fps = int(cmd["fps"])
        except (TypeError, ValueError):
            fps = None
        if fps in FPS_CHOICES and fps != cfg["fps"]:
            cfg["fps"] = fps
            changed["fps"] = fps
            restart = True

    al = cmd.get("align")
    if al in ALIGN_MODES and al != cfg["align"]:
        cfg["align"] = al
        changed["align"] = al          # no restart — just a per-frame choice

    changed["restart"] = restart
    return changed
