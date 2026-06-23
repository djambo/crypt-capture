"""
Control channel — central → node commands (the M1 control plane).

The frame stream is one-way (node → central). This is the reverse, low-rate
direction: the central app sends small JSON commands *down the same TCP socket*
the node already opened (TCP is full-duplex), so no extra connection is needed
and the frame hot path is untouched. The node runs a tiny reader thread that
blocks on `read_command` and applies each command.

Wire format (little-endian): magic `CTL1` + u32 length + UTF-8 JSON body.
Commands are dicts with a `"cmd"` key, e.g.:
    {"cmd": "set_depth", "min": 500, "max": 4000}     # depth mask, millimetres
(arm / record / stop will reuse this same channel later.)

Stdlib only and Python-3.6-safe — this runs on the Jetson node.
"""

import json
import struct
import threading

MAGIC = b"CTL1"
_HEADER = struct.Struct("<4sI")
HEADER_SIZE = _HEADER.size


def _recv_exactly(sock, n):
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            return b""
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def encode(command):
    """Encode a command dict to bytes."""
    body = json.dumps(command).encode("utf-8")
    return _HEADER.pack(MAGIC, len(body)) + body


def read_command(sock):
    """Read one command dict from a socket, or None on clean close."""
    head = _recv_exactly(sock, HEADER_SIZE)
    if not head:
        return None
    magic, n = _HEADER.unpack(head)
    if magic != MAGIC:
        raise ValueError("bad control magic %r — stream desynced" % (magic,))
    body = _recv_exactly(sock, n) if n else b""
    if n and not body:
        return None
    return json.loads(body.decode("utf-8"))


def start_reader(sock, on_command):
    """Spawn a daemon thread that reads commands and calls on_command(dict).

    Returns the thread. Exits quietly when the socket closes.
    """
    def loop():
        try:
            while True:
                cmd = read_command(sock)
                if cmd is None:
                    break
                try:
                    on_command(cmd)
                except Exception as exc:        # a bad command must not kill the node
                    print("control: error handling %r: %s" % (cmd, exc))
        except (OSError, ValueError):
            pass
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
