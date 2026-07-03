"""
Send a control command to the running preview server (which forwards it to the
nodes). Connects to the same WebSocket the browser uses — so this is the
no-browser way to drive the control plane from the central machine.

    # capture a background plate on all nodes (step out of frame first!):
    python3 -m scripts.send_command --host 127.0.0.1 --port 8080 capture-bg --frames 60

The browser UI sends the exact same JSON; see docs/preview_protocol.md.
"""

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import websocket


def send(host, port, command):
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if not websocket.client_handshake(sock, host, port):
        raise SystemExit("WebSocket handshake failed")
    payload = json.dumps(command).encode("utf-8")
    # client -> server frames must be masked (RFC 6455)
    sock.sendall(websocket.encode_frame(payload, opcode=websocket.OP_TEXT, mask=True))
    try:
        sock.sendall(websocket.encode_frame(b"", opcode=websocket.OP_CLOSE, mask=True))
    except OSError:
        pass
    sock.close()
    print("sent:", command)


def main():
    ap = argparse.ArgumentParser(description="send a control command to the relay")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cb = sub.add_parser("capture-bg", help="snapshot the empty scene, then keep "
                        "only points closer than it (step out first!)")
    cb.add_argument("--frames", type=int, default=60, help="frames to average")

    sub.add_parser("clear-bg", help="disable background subtraction")

    bm = sub.add_parser("set-bg-margin", help="background tolerance (mm)")
    bm.add_argument("--mm", type=int, required=True)

    dn = sub.add_parser("set-denoise", help="speckle filter strength (min valid "
                        "8-neighbours to keep a point; 0 = off)")
    dn.add_argument("--min-neighbors", type=int, required=True)

    sc = sub.add_parser("set-camera", help="change depth FOV mode / color "
                        "resolution / fps / alignment live (streaming adapts)")
    sc.add_argument("--depth-mode",
                    choices=["NFOV_UNBINNED", "NFOV_2X2BINNED",
                             "WFOV_2X2BINNED", "WFOV_UNBINNED"])
    sc.add_argument("--color-resolution",
                    choices=["720P", "1080P", "1440P", "1536P", "2160P", "3072P"])
    sc.add_argument("--fps", type=int, choices=[5, 15, 30])
    sc.add_argument("--align", choices=["color_to_depth", "depth_to_color"])

    cf = sub.add_parser("calibrate-fine", help="run the marker-ball wand pass "
                        "AT THE RELAY (same flow as the viewer's Fine Align "
                        "button); progress/results go to connected viewers")
    cf.add_argument("--seconds", type=float, default=30.0)
    cf.add_argument("--ball-radius", type=float, default=0.05)
    cf.add_argument("--min-points", type=int, default=None)
    cf.add_argument("--max-points", type=int, default=None)

    cr = sub.add_parser("calibrate-rough", help="run the Tier-1 rough align "
                        "at the relay (IMU leveling + body-centroid track)")
    cr.add_argument("--seconds", type=float, default=10.0)
    cr.add_argument("--min-points", type=int, default=None)

    fl = sub.add_parser("calibrate-floor", help="level each camera to its own "
                        "detected floor plane (relay-side; floor must be in "
                        "view — clear background subtraction first). Meant "
                        "for uncalibrated/rough rigs; a fine (wand) calib is "
                        "already mm-coplanar")
    fl.add_argument("--seconds", type=float, default=3.0)

    sub.add_parser("reload-rig-calib", help="make the relay re-read "
                   "rig_calib.json now")

    sub.add_parser("clear-rig-calib", help="reset alignment: cancel any "
                   "running calibration, delete rig_calib.json, back to raw "
                   "per-camera frames")

    args = ap.parse_args()
    if args.cmd == "capture-bg":
        send(args.host, args.port, {"cmd": "capture_bg", "frames": args.frames})
    elif args.cmd == "clear-bg":
        send(args.host, args.port, {"cmd": "clear_bg"})
    elif args.cmd == "set-bg-margin":
        send(args.host, args.port, {"cmd": "set_bg_margin", "mm": args.mm})
    elif args.cmd == "set-denoise":
        send(args.host, args.port,
             {"cmd": "set_denoise", "min_neighbors": args.min_neighbors})
    elif args.cmd == "set-camera":
        command = {"cmd": "set_camera"}
        if args.depth_mode is not None:
            command["depth_mode"] = args.depth_mode
        if args.color_resolution is not None:
            command["color_resolution"] = args.color_resolution
        if args.fps is not None:
            command["fps"] = args.fps
        if args.align is not None:
            command["align"] = args.align
        send(args.host, args.port, command)
    elif args.cmd == "calibrate-fine":
        command = {"cmd": "calibrate_fine", "seconds": args.seconds,
                   "ball_radius": args.ball_radius}
        if args.min_points is not None:
            command["min_points"] = args.min_points
        if args.max_points is not None:
            command["max_points"] = args.max_points
        send(args.host, args.port, command)
    elif args.cmd == "calibrate-rough":
        command = {"cmd": "calibrate_rough", "seconds": args.seconds}
        if args.min_points is not None:
            command["min_points"] = args.min_points
        send(args.host, args.port, command)
    elif args.cmd == "calibrate-floor":
        send(args.host, args.port,
             {"cmd": "calibrate_floor", "seconds": args.seconds})
    elif args.cmd == "reload-rig-calib":
        send(args.host, args.port, {"cmd": "reload_rig_calib"})
    elif args.cmd == "clear-rig-calib":
        send(args.host, args.port, {"cmd": "clear_rig_calib"})


if __name__ == "__main__":
    main()
