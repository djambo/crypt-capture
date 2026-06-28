"""
LAN auto-discovery of the central preview relay (so the node doesn't need a
hardcoded IP — handy when the laptop running central gets a new DHCP lease).

How it works (UDP broadcast, stdlib-only, 3.6-safe):

    node                                   central (preview_server)
     |  "who is central for rig X?"  --->   (responder thread bound on
     |        (UDP broadcast)               DISCOVERY_PORT)
     |  <---  "I am, my node port is P"     replies by unicast to the asker
     |        (the node learns central's IP from the reply's source address)
     v
    connects TCP to (reply_ip, P)

The **rig id** is a short name shared by one capture rig's node(s) + relay; it
lets two rigs share a LAN without cross-wiring (and is the "id" you configure
instead of an IP). Broadcast can be blocked on some Wi-Fi (AP/client isolation);
if so, fall back to an mDNS hostname (`mylaptop.local`) or a DHCP reservation —
see docs/jetson_setup.md.

Wire format (text, easy to eyeball with tcpdump):
    query:  b"CRYPTDISC1 Q <rig_id>"
    reply:  b"CRYPTDISC1 R <rig_id> <node_tcp_port>"
"""

import socket
import threading
import time

DISCOVERY_PORT = 9001            # UDP; distinct from the TCP node port (9000)
DEFAULT_RIG_ID = "crypt"
MAGIC = b"CRYPTDISC1"


def encode_query(rig_id=DEFAULT_RIG_ID):
    return MAGIC + b" Q " + rig_id.encode("utf-8")


def parse_query(data):
    """Return the rig_id from a query datagram, or None if it isn't one."""
    parts = data.split(b" ", 2)
    if len(parts) != 3 or parts[0] != MAGIC or parts[1] != b"Q":
        return None
    try:
        return parts[2].decode("utf-8")
    except UnicodeDecodeError:
        return None


def encode_reply(rig_id, node_port):
    return (MAGIC + b" R " + rig_id.encode("utf-8") + b" "
            + str(int(node_port)).encode("ascii"))


def parse_reply(data):
    """Return (rig_id, node_port) from a reply datagram, or None if invalid."""
    parts = data.split(b" ")
    if len(parts) != 4 or parts[0] != MAGIC or parts[1] != b"R":
        return None
    try:
        return parts[2].decode("utf-8"), int(parts[3])
    except (UnicodeDecodeError, ValueError):
        return None


def start_responder(node_port, rig_id=DEFAULT_RIG_ID, port=DISCOVERY_PORT,
                    host=""):
    """Answer discovery queries for `rig_id` with this relay's node TCP port.

    Spawns a daemon thread and returns (socket, thread). Run on the central
    machine alongside the preview server.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))

    def loop():
        while True:
            try:
                data, addr = sock.recvfrom(512)
            except OSError:
                break                              # socket closed -> stop
            if parse_query(data) == rig_id:
                try:
                    sock.sendto(encode_reply(rig_id, node_port), addr)
                except OSError:
                    pass

    th = threading.Thread(target=loop, name="discovery-responder", daemon=True)
    th.start()
    return sock, th


def discover_central(rig_id=DEFAULT_RIG_ID, port=DISCOVERY_PORT,
                     attempts=5, attempt_timeout=1.0,
                     broadcast_addr="255.255.255.255"):
    """Broadcast for the central relay and return (host, node_port), or None.

    Tries `attempts` times, each waiting up to `attempt_timeout` seconds for a
    reply whose rig_id matches. `broadcast_addr` is overridable so tests can use
    a loopback target instead of a real broadcast.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    query = encode_query(rig_id)
    try:
        for _ in range(max(1, attempts)):
            try:
                sock.sendto(query, (broadcast_addr, port))
            except OSError:
                time.sleep(attempt_timeout)
                continue
            deadline = time.time() + attempt_timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(512)
                except socket.timeout:
                    break
                except OSError:
                    break
                parsed = parse_reply(data)
                if parsed is not None and parsed[0] == rig_id:
                    return addr[0], parsed[1]      # central's IP + node TCP port
        return None
    finally:
        sock.close()
