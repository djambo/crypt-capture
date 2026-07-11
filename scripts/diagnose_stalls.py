"""
Find WHERE the per-sensor stream dips live: radio link vs node vs shared.

Run on the RELAY PC while the rig streams (nothing to install on the nodes):

    python -m scripts.diagnose_stalls --nodes 0=192.168.1.203,1=192.168.1.209,2=192.168.1.210

It does three things at once, continuously:
  1. connects to the relay's WebSocket like a viewer and counts each sensor's
     frames per second (peeking the sensor_id header byte — no decode cost);
  2. probes every node's reachability 4x/second with a TCP connect + RTT
     measurement (connection REFUSED still counts as reachable — the host
     answered; only a TIMEOUT is loss, so it needs no open port and no ICMP
     locale parsing);
  3. when a sensor's rate dips below 40% of its rolling median, it looks at
     that node's probes over the same seconds and prints a VERDICT:

       STALL s1 ... link: 3/8 probes lost, rtt max 1400ms  -> RADIO/LINK
       STALL s1 ... link CLEAN (rtt max 12ms)              -> NODE-SIDE
       STALL s0+s1+s2 together                             -> SHARED
         (router/AP/interference or relay/viewer side)

Let it run through a few dips (walk the volume like normal — carry the laptop
if that's the suspicion), then paste the STALL lines.
"""

import argparse
import collections
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import websocket

PROBE_INTERVAL = 0.25   # per-node reachability probes per second: 4
PROBE_TIMEOUT = 1.0     # a probe slower than this counts as LOST
RTT_SPIKE_MS = 250.0    # a reachable-but-slow probe marks the link as bad
DIP_FRACTION = 0.4      # a second below 40% of the rolling median = a dip
MIN_HEALTHY_FPS = 8.0   # don't judge dips until the sensor has a real baseline


class NodeProbe(threading.Thread):
    """Continuously measure one node's reachability + RTT (TCP connect)."""

    def __init__(self, ip, port=22):
        super().__init__(daemon=True)
        self.ip = ip
        self.port = port
        self.results = collections.deque(maxlen=64)  # (t, ok, rtt_ms)
        self.lock = threading.Lock()

    def run(self):
        while True:
            t0 = time.time()
            ok = True
            try:
                s = socket.create_connection((self.ip, self.port),
                                             timeout=PROBE_TIMEOUT)
                s.close()
            except socket.timeout:
                ok = False
            except OSError:
                # Refused/unreachable-with-answer = the host responded fast —
                # reachability is what we measure, not the port.
                pass
            rtt = (time.time() - t0) * 1000.0
            with self.lock:
                self.results.append((t0, ok, rtt))
            time.sleep(max(0.0, PROBE_INTERVAL - (time.time() - t0)))

    def window(self, since):
        with self.lock:
            recent = [r for r in self.results if r[0] >= since]
        lost = sum(1 for _, ok, _ in recent if not ok)
        worst = max((rtt for _, ok, rtt in recent if ok), default=0.0)
        return len(recent), lost, worst


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--host", default="127.0.0.1", help="relay host")
    ap.add_argument("--port", type=int, default=8080, help="relay ws port")
    ap.add_argument("--nodes", required=True,
                    help="sensor=ip pairs, e.g. 0=192.168.1.203,1=...,2=...")
    ap.add_argument("--probe-port", type=int, default=22,
                    help="TCP port probed on each node (any port works — "
                         "refused still proves reachability; default ssh)")
    args = ap.parse_args()

    probes = {}
    for pair in args.nodes.split(","):
        sid, ip = pair.split("=", 1)
        p = NodeProbe(ip.strip(), args.probe_port)
        p.start()
        probes[int(sid)] = p

    sock = socket.create_connection((args.host, args.port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if not websocket.client_handshake(sock, args.host, args.port):
        raise SystemExit("WebSocket handshake failed")
    sock.settimeout(0.5)
    print("connected to relay ws://%s:%d — watching. Ctrl-C to stop.\n"
          % (args.host, args.port))

    counts = collections.Counter()
    history = {sid: collections.deque(maxlen=20) for sid in probes}
    win_start = time.time()

    while True:
        try:
            msg = websocket.read_frame(sock)
            if msg is None or msg[0] == websocket.OP_CLOSE:
                raise SystemExit("relay closed the connection")
            opcode, data = msg
            if opcode == websocket.OP_BINARY and len(data) >= 20:
                counts[struct.unpack_from("<I", data, 8)[0]] += 1
        except socket.timeout:
            pass

        now = time.time()
        if now - win_start < 1.0:
            continue

        # One line per second: per-sensor fps + per-node link state.
        parts = []
        stalled = []
        for sid in sorted(probes):
            fps = counts.get(sid, 0) / (now - win_start)
            hist = history[sid]
            baseline = sorted(hist)[len(hist) // 2] if hist else 0.0
            n, lost, worst = probes[sid].window(now - 3.0)
            link = ("LOST %d/%d" % (lost, n)) if lost else (
                "rtt%3.0fms" % worst)
            mark = ""
            if baseline >= MIN_HEALTHY_FPS and fps < baseline * DIP_FRACTION:
                stalled.append((sid, fps, baseline, lost, n, worst))
                mark = " <<DIP"
            parts.append("s%d %5.1ffps [%s]%s" % (sid, fps, link, mark))
            hist.append(fps)
        print(" | ".join(parts))

        if stalled:
            if len(stalled) == len(probes):
                print("  >> STALL on ALL sensors together -> SHARED cause "
                      "(router/AP/interference burst, or relay/PC side)")
            for sid, fps, base, lost, n, worst in stalled:
                if lost > 0 or worst > RTT_SPIKE_MS:
                    print("  >> STALL s%d (%.1f vs ~%.0f fps): link to %s "
                          "degraded — %d/%d probes lost, worst rtt %.0f ms "
                          "-> RADIO/LINK" % (sid, fps, base,
                                             probes[sid].ip, lost, n, worst))
                else:
                    print("  >> STALL s%d (%.1f vs ~%.0f fps): link to %s "
                          "CLEAN (worst rtt %.0f ms) -> NODE-SIDE "
                          "(camera/SDK/USB or node CPU)"
                          % (sid, fps, base, probes[sid].ip, worst))

        counts.clear()
        win_start = now


if __name__ == "__main__":
    main()
