"""
Dump the Azure Kinect depth-camera intrinsics needed to unproject depth into
metric 3D for meshing. Run once on the node (the Kinect must be connected); the
result is per-device + per-depth-mode constant.

    python3 -m node.dump_calibration --out takes/real1/calib.json

Writes fx, fy, cx, cy (pinhole intrinsics) for the NFOV_UNBINNED depth mode —
matching what node/kinect_node.py captures.
"""

import argparse
import json

import pyk4a
from pyk4a import PyK4A, Config, DepthMode, ColorResolution, FPS


def main():
    ap = argparse.ArgumentParser(description="dump Azure Kinect depth intrinsics")
    ap.add_argument("--out", default="calib.json")
    args = ap.parse_args()

    k4a = PyK4A(Config(
        depth_mode=DepthMode.NFOV_UNBINNED,
        color_resolution=ColorResolution.RES_720P,
        camera_fps=FPS.FPS_30,
    ))
    k4a.start()
    try:
        mat = k4a.calibration.get_camera_matrix(pyk4a.CalibrationType.DEPTH)
    finally:
        k4a.stop()

    calib = {
        "camera": "azure_kinect",
        "depth_mode": "NFOV_UNBINNED",
        "fx": float(mat[0][0]),
        "fy": float(mat[1][1]),
        "cx": float(mat[0][2]),
        "cy": float(mat[1][2]),
    }
    with open(args.out, "w") as f:
        json.dump(calib, f, indent=2)
    print("wrote %s: %s" % (args.out, calib))


if __name__ == "__main__":
    main()
