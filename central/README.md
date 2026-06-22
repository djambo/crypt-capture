# central/

- `recorder.py` — accepts N node streams, groups by synced `frame_id`, writes a
  take to disk.
- **TODO:** capture trigger/control (arm/record/stop to nodes); offline
  processing — calibration (marker + ICP), TSDF fusion → watertight mesh
  sequence, glTF/meshopt export.
