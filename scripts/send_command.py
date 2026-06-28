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


if __name__ == "__main__":
    main()
