"""
Relay per-sensor worker pool (--workers) equivalence.

--workers > 1 fans the relay's heavy stateless stage (unproject + build) across
a thread pool so one sensor's stream can use multiple cores. It MUST produce the
exact same CPV1 bytes, in the same order, as the single-threaded path — the
parallelism is a throughput optimization, not a behaviour change.

This drives the same frames through workers=1 and workers=4 with recording
active (which keeps every frame, in order, and — unlike calibration — still uses
the parallel path) and asserts the broadcast bytes match exactly.
"""

import shutil
import socket
import tempfile

import numpy as np

from central.preview_server import PreviewServer
from protocol import rvl
from protocol.frame import Frame, encode_calib


def _frame(sid, fid, w, h, seed):
    rng = np.random.RandomState(seed)
    depth = (1000 + rng.randint(0, 800, size=(h, w))).astype(np.uint16)
    depth[:2, :] = 0            # some invalid pixels (holes) too
    depth[-2:, :] = 0
    return Frame(sensor_id=sid, frame_id=fid, timestamp_ns=fid,
                 width=w, height=h, depth=rvl.compress(depth), color=b"",
                 depth_rvl=True, color_aligned=False, stride=1).encode()


def _capture(workers, frames, w, h):
    """Run `frames` through a relay reader at the given worker count with
    recording active (keeps every frame in order) and return the ordered list
    of broadcast CPV1 payloads."""
    recdir = tempfile.mkdtemp()
    try:
        server = PreviewServer(rig_calib="", workers=workers,
                               recordings_dir=recdir)
        got = []
        server._broadcast = lambda sid, payload: got.append(bytes(payload))
        server._recorder.start(name="eq")     # keep every frame, in order
        srv, cli = socket.socketpair()
        cli.sendall(encode_calib(0, w, h, 40.0, 40.0, w / 2.0, h / 2.0))
        for fb in frames:
            cli.sendall(fb)
        cli.shutdown(socket.SHUT_WR)
        server._serve_node(srv, ("test", 0))
        cli.close()
        server._recorder.stop()
        return got
    finally:
        shutil.rmtree(recdir, ignore_errors=True)


def test_parallel_matches_sequential():
    w, h = 40, 40
    frames = [_frame(0, i, w, h, seed=i) for i in range(12)]

    seq = _capture(1, frames, w, h)
    par = _capture(4, frames, w, h)

    assert len(seq) == len(frames), "sequential dropped frames: %d" % len(seq)
    assert len(par) == len(seq), \
        "worker count changed frame count: %d vs %d" % (len(par), len(seq))
    for i, (a, b) in enumerate(zip(seq, par)):
        assert a == b, "frame %d differs: parallel output not identical" % i


if __name__ == "__main__":
    test_parallel_matches_sequential()
    print("relay workers equivalence OK")
