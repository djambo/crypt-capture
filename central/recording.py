"""
Scene recording: capture the LIVE preview stream to disk, replayable in the
viewer (docs/preview_protocol.md, "Scene recording").

The relay is the one place where the fully-processed frames already exist as
encoded bytes — RVL-decoded, unprojected, background-subtracted, rig-registered
`CPV1` messages, exactly what every viewer renders. Recording tees those bytes
to disk, so a recorded take IS the wire stream: playback feeds the same
source-agnostic renderer with zero re-encoding, which is what makes live and
recorded content indistinguishable (the North Star).

Seamlessness contract: `add_frame` is called from the node handler threads and
must NEVER block or slow them — it appends to an in-memory queue (a reference,
no copy) and returns; a dedicated writer thread drains the queue to disk. If
the disk can't keep up past a generous buffer cap, frames are DROPPED AND
COUNTED (reported in the meta + status) rather than stalling the live path:
the experience being uninterrupted beats recording completeness.

File format `CPR1` (one file per take, `<id>.cpr`):

    header: magic "CPR1" (4s) | u16 version=1 | u16 reserved
    per frame: f64 t (seconds since recording start, little-endian)
             | u32 payload_len | payload (one verbatim CPV1 message)

Alongside it a JSON sidecar `<id>.json` holds the metadata (name, duration,
frames, sensors, max point count, bytes, drops) — listing recordings only
reads sidecars, and a crashed take (no sidecar) is invisible until repaired.
Central-only (x86/3.8+), like preview_server.
"""

import json
import os
import re
import struct
import threading
import time

RECORDING_MAGIC = b"CPR1"
RECORDING_VERSION = 1
_FILE_HEADER = struct.Struct("<4sHH")      # magic, version, reserved
_FRAME_HEADER = struct.Struct("<dI")       # t (s since start), payload length

# Take ids are generated (timestamps) and validated on lookup so an id from
# the network can never traverse paths.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# In-memory backlog cap: past this the writer is falling behind the stream
# (dead/slow disk) and we drop-and-count instead of growing unbounded or —
# worse — backpressuring the live path. ~35 s of a subtracted 4-cam stream.
MAX_BUFFER_BYTES = 512 * 1024 * 1024


def _safe_id(rec_id):
    """A recording id usable as a filename, or None."""
    if isinstance(rec_id, str) and _ID_RE.match(rec_id) and ".." not in rec_id:
        return rec_id
    return None


def take_path(directory, rec_id):
    """Path of a take's data file for a (validated) id, or None."""
    rid = _safe_id(rec_id)
    if rid is None:
        return None
    if not rid.endswith(".cpr"):
        rid += ".cpr"
    return os.path.join(directory, rid)


class TakeRecorder:
    """Records the relay's outgoing CPV1 stream to one `.cpr` take at a time.

    start() / add_frame() / stop(); `add_frame` is thread-safe and
    non-blocking (node handler threads call it inline). One writer thread per
    active take drains the queue to disk and finalizes the sidecar on stop.
    """

    def __init__(self, directory="recordings",
                 max_buffer_bytes=MAX_BUFFER_BYTES):
        self.directory = directory
        self.max_buffer_bytes = max_buffer_bytes
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._active = None                # state dict while recording
        self._thread = None

    @property
    def recording(self):
        return self._active is not None

    def start(self, name=None):
        """Begin a new take. Returns its meta dict, or the CURRENT take's meta
        if one is already recording (idempotent — a second Record press from
        another viewer must not clobber a running take)."""
        with self._lock:
            if self._active is not None:
                return dict(self._active["meta"])
            os.makedirs(self.directory, exist_ok=True)
            t0 = time.time()
            rec_id = time.strftime("take-%Y%m%d-%H%M%S", time.localtime(t0))
            path = os.path.join(self.directory, rec_id + ".cpr")
            n = 1
            while os.path.exists(path):       # same-second restart
                n += 1
                rec_id_n = "%s-%d" % (rec_id, n)
                path = os.path.join(self.directory, rec_id_n + ".cpr")
            if n > 1:
                rec_id = "%s-%d" % (rec_id, n)
            meta = {
                "id": rec_id,
                "name": str(name) if name else rec_id,
                "format": "CPR1",
                "version": RECORDING_VERSION,
                "created": t0,
            }
            f = open(path, "wb")
            f.write(_FILE_HEADER.pack(RECORDING_MAGIC, RECORDING_VERSION, 0))
            self._active = {
                "meta": meta, "path": path, "file": f, "t0": t0,
                "queue": [], "queued_bytes": 0, "stopping": False,
                "frames": 0, "bytes": 0, "dropped": 0,
                "sensors": set(), "max_count": 0, "last_t": 0.0,
            }
            self._thread = threading.Thread(target=self._writer,
                                            args=(self._active,), daemon=True)
            self._thread.start()
            return dict(meta)

    def add_frame(self, payload):
        """Tee one outgoing CPV1 message into the active take. Non-blocking:
        stores a reference + timestamp; the writer thread does the disk I/O.
        No-op when not recording (guard is one attribute read)."""
        state = self._active
        if state is None:
            return
        t = time.time() - state["t0"]
        with self._cv:
            if state["stopping"]:
                return
            if state["queued_bytes"] + len(payload) > self.max_buffer_bytes:
                state["dropped"] += 1     # disk too slow — never stall live
                return
            state["queue"].append((t, payload))
            state["queued_bytes"] += len(payload)
            self._cv.notify()

    def status(self):
        """Live stats for the record_status broadcast (None when idle)."""
        state = self._active
        if state is None:
            return None
        return {
            "id": state["meta"]["id"],
            "name": state["meta"]["name"],
            "seconds": round(time.time() - state["t0"], 1),
            "frames": state["frames"] + len(state["queue"]),
            "bytes": state["bytes"] + state["queued_bytes"],
            "dropped": state["dropped"],
        }

    def stop(self):
        """Finish the active take: drain the queue, write the sidecar, return
        the final meta dict (None if nothing was recording)."""
        with self._cv:
            state = self._active
            if state is None:
                return None
            state["stopping"] = True
            self._active = None            # add_frame stops seeing it now
            self._cv.notify()
        self._thread.join()
        self._thread = None
        meta = state["meta"]
        meta.update({
            "duration": round(state["last_t"], 3),
            "frames": state["frames"],
            "bytes": state["bytes"],
            "sensors": sorted(state["sensors"]),
            "max_count": state["max_count"],
            "dropped": state["dropped"],
        })
        sidecar = os.path.join(self.directory, meta["id"] + ".json")
        with open(sidecar, "w") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        return dict(meta)

    def _writer(self, state):
        """Writer thread: drain the queue to disk until stop() drains us dry.
        Also accumulates the stats that end up in the sidecar (peeking
        sensor_id/count out of each CPV1 header — offsets 8 and 16)."""
        f = state["file"]
        while True:
            with self._cv:
                while not state["queue"] and not state["stopping"]:
                    self._cv.wait()
                batch = state["queue"]
                state["queue"] = []
                state["queued_bytes"] = 0
                done = state["stopping"] and not batch
            if done:
                break
            for t, payload in batch:
                f.write(_FRAME_HEADER.pack(t, len(payload)))
                f.write(payload)
                state["frames"] += 1
                state["bytes"] += len(payload)
                state["last_t"] = t
                if len(payload) >= 20:
                    sid, = struct.unpack_from("<I", payload, 8)
                    count, = struct.unpack_from("<I", payload, 16)
                    state["sensors"].add(sid)
                    if count > state["max_count"]:
                        state["max_count"] = count
        f.close()


def read_take(path):
    """Yield (t, payload) for every frame of a `.cpr` take. Raises ValueError
    on a bad header; a truncated tail (crash mid-write) ends iteration."""
    with open(path, "rb") as f:
        head = f.read(_FILE_HEADER.size)
        if len(head) < _FILE_HEADER.size:
            raise ValueError("not a CPR1 take: %s" % path)
        magic, version, _ = _FILE_HEADER.unpack(head)
        if magic != RECORDING_MAGIC:
            raise ValueError("bad magic %r in %s" % (magic, path))
        if version > RECORDING_VERSION:
            raise ValueError("take version %d newer than reader" % version)
        while True:
            fh = f.read(_FRAME_HEADER.size)
            if len(fh) < _FRAME_HEADER.size:
                return
            t, length = _FRAME_HEADER.unpack(fh)
            payload = f.read(length)
            if len(payload) < length:
                return                     # truncated tail
            yield t, payload


def list_recordings(directory="recordings"):
    """All finished takes (sidecar metas), newest first."""
    items = []
    try:
        names = os.listdir(directory)
    except OSError:
        return items
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name)) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("id"):
            items.append(meta)
    items.sort(key=lambda m: m.get("created", 0), reverse=True)
    return items


def delete_recording(directory, rec_id):
    """Remove a take's data file + sidecar. Returns True if anything went."""
    rid = _safe_id(rec_id)
    if rid is None:
        return False
    removed = False
    for suffix in (".cpr", ".json"):
        try:
            os.remove(os.path.join(directory, rid + suffix))
            removed = True
        except OSError:
            pass
    return removed
