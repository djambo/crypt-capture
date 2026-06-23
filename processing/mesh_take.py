"""
Convert a recorded take into per-frame triangle meshes (PLY) you can view.

This is the depth-grid mesh — the payoff of keeping the depth structure: each
valid depth pixel is unprojected to metric 3D using the camera intrinsics, then
neighboring pixels are connected into triangles, CUTTING any edge where the
depth jump exceeds a threshold (so the silhouette stays clean instead of
webbing across to the background).

Single sensor = a single-viewpoint surface (front only). With 4 calibrated
sensors the same idea fuses to a full body (Phase 2).

    # one frame to eyeball:
    python3 -m processing.mesh_take --take takes/real1 --calib takes/real1/calib.json --frame 0
    # whole sequence:
    python3 -m processing.mesh_take --take takes/real1 --calib takes/real1/calib.json --all

Output: PLY meshes in <take>/mesh/ — open in MeshLab, Blender, or three.js PLYLoader.
"""

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from protocol import rvl


def load_calib(path):
    c = json.load(open(path))
    return c["fx"], c["fy"], c["cx"], c["cy"]


def frame_to_mesh(depth, w, h, fx, fy, cx, cy, edge_mm):
    """Unproject a depth grid to a triangle mesh with a depth-discontinuity cut."""
    vidx = [-1] * (w * h)
    vx, vy, vz = [], [], []
    for v in range(h):
        row = v * w
        for u in range(w):
            z = depth[row + u]
            if z == 0:
                continue
            Z = z / 1000.0                       # mm -> m
            vidx[row + u] = len(vx)
            vx.append((u - cx) * Z / fx)
            vy.append(-((v - cy) * Z / fy))      # flip Y so up is +Y
            vz.append(-Z)                        # camera looks down -Z in view space

    faces = []
    for v in range(h - 1):
        r0, r1 = v * w, (v + 1) * w
        for u in range(w - 1):
            # two triangles of the pixel quad; keep each only if all 3 corners
            # are valid AND no edge spans a depth discontinuity.
            for tri in ((r0 + u, r0 + u + 1, r1 + u + 1),
                        (r0 + u, r1 + u + 1, r1 + u)):
                a, b, c = tri
                da, db, dc = depth[a], depth[b], depth[c]
                if da == 0 or db == 0 or dc == 0:
                    continue
                if max(abs(da - db), abs(db - dc), abs(da - dc)) > edge_mm:
                    continue
                faces.append((vidx[a], vidx[b], vidx[c]))
    return (vx, vy, vz), faces


def write_ply(path, verts, faces):
    vx, vy, vz = verts
    with open(path, "wb") as f:
        f.write((
            "ply\nformat binary_little_endian 1.0\n"
            "element vertex %d\nproperty float x\nproperty float y\nproperty float z\n"
            "element face %d\nproperty list uchar int vertex_indices\n"
            "end_header\n" % (len(vx), len(faces))
        ).encode())
        pack_v = struct.Struct("<fff").pack
        for i in range(len(vx)):
            f.write(pack_v(vx[i], vy[i], vz[i]))
        pack_f = struct.Struct("<Biii").pack
        for a, b, c in faces:
            f.write(pack_f(3, a, b, c))


def mesh_frame(take, fid, fx, fy, cx, cy, edge_mm, out_dir):
    manifest = json.load(open(os.path.join(take, "manifest.json")))
    s0 = manifest["sensors"]["0"]
    w, h = s0["width"], s0["height"]
    path = os.path.join(take, "frames", "%06d" % fid, "sensor0.depth.rvl")
    depth = rvl.decompress(open(path, "rb").read(), w * h)
    verts, faces = frame_to_mesh(depth, w, h, fx, fy, cx, cy, edge_mm)
    out = os.path.join(out_dir, "frame_%06d.ply" % fid)
    write_ply(out, verts, faces)
    return len(verts[0]), len(faces), out


def main():
    ap = argparse.ArgumentParser(description="take -> per-frame depth-grid mesh (PLY)")
    ap.add_argument("--take", required=True)
    ap.add_argument("--calib", required=True, help="calib.json with fx,fy,cx,cy")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--edge-mm", type=float, default=50.0,
                    help="cut triangles whose depth edge exceeds this (mm)")
    args = ap.parse_args()

    fx, fy, cx, cy = load_calib(args.calib)
    out_dir = os.path.join(args.take, "mesh")
    os.makedirs(out_dir, exist_ok=True)
    manifest = json.load(open(os.path.join(args.take, "manifest.json")))
    fids = manifest["frame_ids"] if args.all else [args.frame]
    for fid in fids:
        nv, nf, out = mesh_frame(args.take, fid, fx, fy, cx, cy, args.edge_mm, out_dir)
        print("frame %d: %d verts, %d tris -> %s" % (fid, nv, nf, out))


if __name__ == "__main__":
    main()
