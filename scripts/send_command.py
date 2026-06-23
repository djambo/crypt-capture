"""
Send a control command to the running preview server (which forwards it to the
nodes). Connects to the same WebSocket the browser uses — so this is the
no-browser way to drive the control plane from the central machine.

    # set the depth mask to 0.4m .. 4.0m on all nodes:
    python3 -m scripts.send_command --host 127.0.0.1 --port 8080 set-depth --min 400 --max 4000

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

    sd = sub.add_parser("set-depth", help="set the working depth mask (mm)")
    sd.add_argument("--min", type=int)
    sd.add_argument("--max", type=int)

    args = ap.parse_args()
    if args.cmd == "set-depth":
        command = {"cmd": "set_depth"}
        if args.min is not None:
            command["min"] = args.min
        if args.max is not None:
            command["max"] = args.max
        send(args.host, args.port, command)


if __name__ == "__main__":
    main()
