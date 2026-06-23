"""
Headless preview client — verifies the live stream WITHOUT a browser.

Connects to the preview server's WebSocket, parses the CPV1 point-cloud frames
(see docs/preview_protocol.md), and prints per-frame point counts + the observed
frame rate. This is the no-browser way to confirm the M2 pipeline works:

    # terminal 1 — the relay:
    python3 -m central.preview_server
    # terminal 2 — a node (simulated; no hardware needed):
    python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 300
    # terminal 3 — this client:
    python3 -m scripts.preview_client --frames 30

A non-zero, sane point count and a steady fps means capture→decode→unproject→
relay all work; the real browser viewer (in the `crypt` repo) consumes the same
messages.
"""

import argparse
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import websocket

_HEADER = struct.Struct("<4sIIII")


def parse_preview(payload):
    magic, flags, sensor, frame_id, count = _HEADER.unpack_from(payload, 0)
    if magic != b"CPV1":
        raise ValueError("bad preview magic %r" % (magic,))
    off = _HEADER.size
    pos_bytes = count * 3 * 4
    positions = memoryview(payload)[off:off + pos_bytes]
    return {
        "flags": flags, "sensor": sensor, "frame_id": frame_id,
        "count": count, "positions": positions,
    }


def run(host, port, frames):
    sock = socket.create_connection((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if not websocket.client_handshake(sock, host, port):
        raise SystemExit("WebSocket handshake failed")
    print("connected to ws://%s:%d/ — waiting for frames..." % (host, port))

    got = 0
    t0 = None
    total_pts = 0
    try:
        while got < frames:
            msg = websocket.read_frame(sock)
            if msg is None:
                print("server closed the connection")
                break
            opcode, payload = msg
            if opcode != websocket.OP_BINARY:
                continue
            info = parse_preview(payload)
            if t0 is None:
                t0 = time.time()
            got += 1
            total_pts += info["count"]
            # sanity: positions block length matches count
            assert len(info["positions"]) == info["count"] * 12
            if got <= 3 or got % 10 == 0:
                print("frame %d: sensor %d, %d points (%d bytes)"
                      % (info["frame_id"], info["sensor"], info["count"], len(payload)))
        if got and t0:
            dt = max(1e-6, time.time() - t0)
            print("\nreceived %d frames, avg %d pts/frame, %.1f fps"
                  % (got, total_pts // got, (got - 1) / dt if got > 1 else 0.0))
        else:
            print("no frames received — is a node streaming to the server?")
    finally:
        try:
            sock.sendall(websocket.encode_frame(b"", opcode=websocket.OP_CLOSE, mask=True))
        except OSError:
            pass
        sock.close()


def main():
    ap = argparse.ArgumentParser(description="headless preview WebSocket client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()
    run(args.host, args.port, args.frames)


if __name__ == "__main__":
    main()
