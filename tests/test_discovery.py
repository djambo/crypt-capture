"""
LAN auto-discovery tests (headless, stdlib-only).

Covers the query/reply wire encode+parse and a real loopback round-trip:
a responder advertising a node port, found by discover_central over UDP.

Run: python3 -m tests.test_discovery
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import discovery


def test_query_roundtrip():
    q = discovery.encode_query("rigA")
    assert discovery.parse_query(q) == "rigA"
    # Non-queries and mismatched magic parse to None.
    assert discovery.parse_query(b"garbage") is None
    assert discovery.parse_query(discovery.encode_reply("rigA", 9000)) is None
    print("query roundtrip: OK")


def test_reply_roundtrip():
    r = discovery.encode_reply("rigB", 9100)
    assert discovery.parse_reply(r) == ("rigB", 9100)
    assert discovery.parse_reply(b"nope") is None
    assert discovery.parse_reply(discovery.encode_query("rigB")) is None
    print("reply roundtrip: OK")


def test_loopback_discovery():
    """Responder + discover over the loopback (no real broadcast needed)."""
    port = 49321                                    # arbitrary high UDP port
    sock, _ = discovery.start_responder(9000, rig_id="rigC", port=port)
    try:
        found = discovery.discover_central(
            "rigC", port=port, attempts=3, attempt_timeout=0.5,
            broadcast_addr="127.0.0.1")
        assert found is not None, "responder did not answer"
        host, node_port = found
        assert host in ("127.0.0.1", "0.0.0.0"), host
        assert node_port == 9000, node_port
        print("loopback discovery: found %s:%d  OK" % (host, node_port))

        # A different rig id on the same responder must NOT match.
        miss = discovery.discover_central(
            "other", port=port, attempts=2, attempt_timeout=0.3,
            broadcast_addr="127.0.0.1")
        assert miss is None, miss
        print("rig-id mismatch ignored: OK")
    finally:
        sock.close()


if __name__ == "__main__":
    test_query_roundtrip()
    test_reply_roundtrip()
    test_loopback_discovery()
    print("all discovery tests passed")
