"""
Azure Kinect capture-mode catalog + resolver (the "camera controls" data model).

This is the *hardware-independent* core of the camera-control feature: a catalog
of the sensor's selectable modes and a pure function that merges a requested
change onto the current config, validates it, and clamps illegal combinations.
It deliberately imports **nothing** beyond the stdlib (no pyk4a, no numpy) so it:

  * runs in unit tests on any dev box (the real node needs pyk4a + a sensor),
  * is reused by both the real node (`node/kinect_node.py`, which maps these
    string names onto pyk4a enums) and the simulator (`node/sim_node.py`, which
    uses the derived grid dims + FOV to synthesize matching frames), and
  * stays Python-3.6-safe for the Jetson.

Two independent knobs the user drives live from the UI:

  * **depth_mode** — the Azure Kinect's four depth/FOV modes. Narrow FOV (NFOV)
    is tighter + longer range; wide FOV (WFOV) sees ~120° but shorter range.
    "unbinned" is full depth resolution, "2x2binned" is quarter-res but lower
    noise / longer range / higher max fps.
  * **color_resolution** — the RGB sensor resolution. Higher = more color detail
    sampled onto the cloud.

Plus a **geometry** knob that selects how the point cloud is built:

  * ``"depth"`` (default) — color is warped *into* the depth grid; the cloud has
    one point per depth pixel (≤ 1024×1024). Cheap, fewer points.
  * ``"color"`` — depth is warped *into* the color grid; the cloud has one point
    per *color* pixel, so you get a much denser, full-color-resolution cloud
    (the "color-aligned point cloud"). Far more points → the streaming adapts
    via the preview stride / relay decimation.

Both paths still emit the same wire frame (a depth grid + aligned RGB + the
matching camera intrinsics), so the relay and viewer are geometry-agnostic — only
*which* camera's intrinsics/dimensions the node reports changes.
"""

# Depth modes: name -> (width, height, hfov_deg, vfov_deg). Dimensions are the
# Azure Kinect DK depth-engine outputs; FOVs are the published optics.
DEPTH_MODES = {
    "NFOV_UNBINNED":  (640, 576, 75.0, 65.0),
    "NFOV_2X2BINNED": (320, 288, 75.0, 65.0),
    "WFOV_UNBINNED":  (1024, 1024, 120.0, 120.0),
    "WFOV_2X2BINNED": (512, 512, 120.0, 120.0),
}

# Color resolutions: name -> (width, height, hfov_deg, vfov_deg). 16:9 modes use
# the wide color FOV; the 4:3 modes (1536P/3072P) keep the full vertical FOV.
COLOR_RESOLUTIONS = {
    "720P":  (1280, 720, 90.0, 59.0),
    "1080P": (1920, 1080, 90.0, 59.0),
    "1440P": (2560, 1440, 90.0, 59.0),
    "2160P": (3840, 2160, 90.0, 59.0),
    "1536P": (2048, 1536, 90.0, 74.3),
    "3072P": (4096, 3072, 90.0, 74.3),
}

FPS_OPTIONS = (5, 15, 30)
GEOMETRIES = ("depth", "color")

# Sensible default — matches the historical hardcoded kinect_node config.
DEFAULT_CONFIG = {
    "depth_mode": "NFOV_UNBINNED",
    "color_resolution": "720P",
    "fps": 30,
    "geometry": "depth",
}


def max_fps(depth_mode, color_resolution):
    """Highest frame rate the Azure Kinect allows for this mode combination.

    The two hard caps in the SDK: WFOV *unbinned* depth maxes at 15 fps (the
    depth engine can't sustain 30 at 1Mpx), and the 3072P color mode maxes at
    15 fps. Everything else supports up to 30.
    """
    m = 30
    if depth_mode == "WFOV_UNBINNED":
        m = min(m, 15)
    if color_resolution == "3072P":
        m = min(m, 15)
    return m


def resolve(current, requested):
    """Merge `requested` onto `current`, validate, and clamp illegal combos.

    `current` and `requested` are dicts with any of the keys
    ``depth_mode``, ``color_resolution``, ``fps``, ``geometry`` (requested may be
    partial; None values are ignored). Returns
    ``(config, (grid_w, grid_h, hfov_deg, vfov_deg), notes)`` where:

      * ``config`` is the full normalized config dict,
      * the grid tuple describes the point-cloud grid for the chosen geometry
        (depth dims+FOV for "depth", color dims+FOV for "color") — used by the
        simulator and for logging,
      * ``notes`` is a list of human-readable adjustments (e.g. fps clamped).

    Raises ValueError on an unknown mode/resolution/geometry name.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in (current or {}).items() if v is not None})
    cfg.update({k: v for k, v in (requested or {}).items() if v is not None})

    notes = []

    dm = str(cfg["depth_mode"]).upper()
    if dm not in DEPTH_MODES:
        raise ValueError("unknown depth_mode %r (choose from %s)"
                         % (cfg["depth_mode"], ", ".join(sorted(DEPTH_MODES))))

    cr = str(cfg["color_resolution"]).upper()
    if cr not in COLOR_RESOLUTIONS:
        raise ValueError("unknown color_resolution %r (choose from %s)"
                         % (cfg["color_resolution"],
                            ", ".join(sorted(COLOR_RESOLUTIONS))))

    geom = str(cfg["geometry"]).lower()
    if geom not in GEOMETRIES:
        raise ValueError("unknown geometry %r (choose from %s)"
                         % (cfg["geometry"], ", ".join(GEOMETRIES)))

    try:
        fps = int(cfg["fps"])
    except (TypeError, ValueError):
        raise ValueError("fps must be an integer, got %r" % (cfg["fps"],))
    if fps not in FPS_OPTIONS:
        raise ValueError("unknown fps %r (choose from %s)"
                         % (fps, ", ".join(str(f) for f in FPS_OPTIONS)))
    cap = max_fps(dm, cr)
    if fps > cap:
        notes.append("fps %d not supported by %s + %s; clamped to %d"
                     % (fps, dm, cr, cap))
        fps = cap

    config = {"depth_mode": dm, "color_resolution": cr,
              "fps": fps, "geometry": geom}

    if geom == "color":
        grid = COLOR_RESOLUTIONS[cr]
    else:
        grid = DEPTH_MODES[dm]

    return config, grid, notes
