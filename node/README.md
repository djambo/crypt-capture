# node/

Edge capture node (one per Azure Kinect).

- `sim_node.py` — **simulated** node (no hardware): synthesizes masked depth +
  color, RVL-encodes, streams via the wire protocol. Lets the whole spine be
  built/tested without sensors.
- **Real node (TODO):** pyk4a → per-view AI matting (RVM/BGMv2) → RVL depth +
  NVENC color → same `Frame` messages. Drop-in replacement for `sim_node`.
