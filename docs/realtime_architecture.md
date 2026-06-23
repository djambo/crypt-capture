# Real-time capture web app — architecture & feasibility

> Scope-defining doc (2026-06). The earlier roadmap drifted toward an offline
> post-production pipeline (capture → mesh → glTF). This corrects course: the
> product is a **live, networked capture web app**. Recording is one mode of it,
> not the whole thing. Read this before building; it sets the architecture the
> MVP slices implement.

## What we're building

A web app, served from a **central machine on the LAN**, that:

1. **Connects to N remote Jetsons** (1 Azure Kinect per Jetson) over Ethernet.
2. **Live-streams** their point clouds in real time for monitoring/framing.
3. On a **trigger**, tells every node to **record** a full-fidelity clip to the
   node's **own local disk**, then lets you **download** the clips for
   post-processing.
4. End-state: 4 sensors **fused into one aligned point cloud**.
   **MVP: one camera, with live preview + trigger-record-download working end
   to end.**

Confirmed product decisions:
- **Recording is local-on-node, downloaded after.** Each Jetson writes its take
  to its own disk at full rate; the wire only carries the *preview* live + a
  download afterward. A network hiccup can never corrupt a recording.
- **Build order: live preview first**, then record/download, then N nodes, then
  multi-view alignment/fusion.

## North Star (where this is heading)

The capture app is the foundation for a **WebXR/VR "edge of reality"
experience**: prerecorded volumetric clips and **real-time** volumetric streams
of whoever enters the capture volume, blended in the same space so the viewer
**can't tell which is which** — including pre-recording the user and letting them
**step out of their own body**. Far future: stream between two sites →
volumetric **teleportation** (the WAN case, where the LAN bandwidth headroom
above no longer applies).

Two design constraints this imposes on everything below:

1. **One shared world coordinate frame.** Live and recorded content must register
   to the same metric space. So extrinsic calibration / world-anchoring is
   **foundational, not a fusion-era afterthought** — needed as soon as live and
   recorded coexist (even with one rig). The 4-sensor seam-dissolving *fusion*
   stays later (M5); the coordinate frame comes early.
2. **Source-agnostic representation.** The renderer consumes "point-cloud frames
   in world space" and must not care whether they arrived live over the wire or
   off local disk. Therefore the **live preview and the recorded take share one
   representation** — record in the wire/take format, don't invent a second one.

WebXR also tightens the **live** path's motion-to-photon budget (~20 ms),
strengthening the eventual WebSocket → WebRTC/WebTransport move. The
prerecorded-in-VR path is local playback and has no such budget — so the live
path is the latency-critical one.

## Feasibility: the network is not the bottleneck — compute is

Per Azure Kinect, NFOV unbinned depth (640×576, uint16):

| stream | size | rate @30fps |
|---|---|---|
| Raw depth | 0.74 MB/frame | ~177 Mbit/s |
| RVL depth (~14×, more after range-mask) | ~50 KB/frame | **~10–15 Mbit/s** |
| Color (720p NVENC H.264) | — | **~8 Mbit/s** |
| **Per sensor** | | **~20–25 Mbit/s** |
| **4 sensors** | | **~80–100 Mbit/s** |

Gigabit Ethernet is 1000 Mbit/s — **4 sensors fit ~10× over**. Bandwidth is a
non-issue on a wired LAN. The real constraints, in priority order:

1. **Encode on the node.** Today's RVL is pure-Python (~1 fps on the Nano). Real
   time needs a **vectorized/C RVL**. This is the gating item, and the strongest
   reason to evaluate the **Orin** (faster CPU + a hardware **NVENC** for color;
   the original Nano has no NVENC). See the Orin eval plan below.
2. **Browser ingest.** Browsers cannot take raw TCP. The central app bridges to
   the browser via **WebSocket** (v0), with WebRTC/WebTransport as a latency
   upgrade.
3. **Browser decode + render budget** (RVL decode → unproject → three.js
   points). The `crypt` repo already has the rendering half.

## Components

```
 Jetson node (×N)                Central (web app server)         Browser
 ┌───────────────────┐          ┌────────────────────────┐      ┌──────────────┐
 │ pyk4a capture      │          │ control plane (fan-out │      │ three.js     │
 │  → range mask      │  TCP     │   arm/record/stop)     │  WS  │ point cloud  │
 │  → RVL depth       │ ───────► │ preview relay          │ ───► │ (from crypt) │
 │  (+ NVENC color)   │ preview  │ serves web UI          │      │ controls:    │
 │                    │          │ (Phase 2: align+fuse)  │      │  connect /   │
 │ control listener   │ ◄─────── │                        │      │  record /    │
 │ recorder → local   │ commands │                        │      │  download    │
 │   disk (full rate) │          │ proxies downloads ─────┼──────┤  recordings  │
 │ HTTP file server   │ ◄────────┴── download (HTTP) ─────┴──────┤  browser     │
 └───────────────────┘                                          └──────────────┘
```

### Node (Jetson)
- **Capture** (existing `node/kinect_node.py`): pyk4a → range-mask → RVL depth.
  Color via NVENC on Orin (MJPG passthrough as a stub today).
- **Two consumers off one capture loop:**
  - **Preview**: downsampled / frame-dropped, best-effort, streamed live.
  - **Recorder**: on trigger, writes a full-rate take to **local disk** in the
    existing take format (`frames/<id>/sensorN.depth.rvl` + color + manifest).
    *Option:* record raw depth (22 MB/s, any SSD handles it) and RVL-compress at
    download time — sidesteps the need for fast RVL on the record path.
- **Control listener**: accepts `arm / record / stop / status` commands.
- **HTTP file server**: lists takes and serves them (tar/zip) for download.

### Central (web app server)
- **Control plane**: one trigger fans out to all nodes (this is the HW-sync
  story at the app level; physical 3.5 mm daisy-chain handles frame-level sync).
- **Preview relay**: node TCP preview → browser WebSocket. For v0 it can also
  **decode + unproject** (reuse `protocol/rvl.py`) and push a compact binary
  point list, so the browser needs no Wasm RVL yet.
- **Serves the web UI** and **proxies/links downloads**.
- **Phase 2**: extrinsic calibration + alignment/fusion of the 4 views.

### Browser
- three.js point-cloud renderer (port from `crypt`), WebSocket client, controls
  (connect nodes, arm/record/stop, recordings browser with download links).

## Transport choices (browser ↔ central)

| option | pros | cons | verdict |
|---|---|---|---|
| **WebSocket** | TCP, simple, reliable, binary frames, trivial on LAN | head-of-line blocking; reliable-only (late frames still delivered) | **v0 for control + preview** |
| **WebRTC DataChannel** | low latency, can drop late frames (unreliable mode) | ICE/signaling complexity (host candidates only on LAN) | preview upgrade if latency hurts |
| **WebTransport (HTTP/3/QUIC)** | unreliable datagrams + reliable streams, modern | needs TLS certs; newer browsers; less mature server libs | revisit later |

**Decision:** WebSocket for v0 (control + preview). Move *preview* to WebRTC/
WebTransport only if real-time latency or backpressure becomes a problem.

**Where to decode/unproject for preview:**
- **v0 — central-side decode**: central runs existing `rvl.py`, unprojects,
  downsamples, sends binary XYZ(+RGB). Browser just renders. No Wasm needed.
- **v1 — browser-side decode**: central forwards RVL + calib; browser decodes in
  a Web Worker (Wasm RVL). Smaller wire, offloads central. Build after v0 works.

## Orin vs Nano evaluation plan

Goal: decide whether an **Orin** can run the full node pipeline at real-time
rates, and whether one device can capture **and** record simultaneously.

- **Device note:** evaluate the **Orin NX**, *not* the Orin Nano — the **Nano
  lacks NVENC** (no hardware color encode). ("orange new Jetson" = Orin family.)
- **Metrics to capture:**
  - RVL encode fps: pure-Python vs vectorized NumPy vs C, on the Orin CPU.
  - NVENC color encode: available? sustained fps at 720p/1080p?
  - End-to-end capture → preview-stream fps, and CPU/GPU load + power + thermal.
  - **Simultaneous capture + full-rate local record** fps (the real test).
- **Risk to verify first:** the Azure Kinect **depth engine** binary on a
  current JetPack (5/6). We do **not** need Body Tracking (no ARM support, and
  irrelevant to point clouds) — only depth, which the SDK provides. Confirm the
  depth engine loads and streams on the target OS before committing.
- Azure Kinect is discontinued; the **Orbbec Femto Bolt** is the successor and a
  fallback if Kinect-on-Orin proves painful.

## MVP milestones (single camera, live-preview first)

- **M0 — Fast RVL.** Vectorized NumPy `compress`/`decompress` (gating for
  real-time; also unblocks central-side preview decode).
- **M1 — Control plane.** Central ↔ node command channel
  (`arm/record/stop/status`); node grows a control listener.
- **M2 — Live preview.** Single node → central decode/downsample → browser
  three.js points over WebSocket. *(Proves the streaming-feasibility question.)*
- **M3 — Record + download.** Trigger → node records full-rate to local disk →
  HTTP file server → recordings browser + download in the UI.
- **M4 — N nodes.** Node discovery/registry; trigger fans out to all.
- **M5 (Phase 2) — Aligned/fused.** Extrinsic calibration (marker + ICP) →
  true *aligned* multi-view point cloud; TSDF fusion for a watertight surface is
  the later fidelity step.

Note: "aligned point clouds" requires **extrinsic calibration between sensors**
(M5). For the single-camera MVP, alignment is trivial — one view, identity
extrinsics.

## Open questions / risks

- **Fast RVL** is on the critical path for both preview and (optionally) record.
- **Orin NVENC + depth engine on JetPack 5/6** — verify before hardware spend.
- **Clock/frame sync across nodes** for preview alignment (physical sync handles
  capture; the app needs a frame-id/timestamp convention — see `protocol.md`).
- **WebSocket backpressure** under load → may force the WebRTC/WebTransport move.
- **Wasm RVL** needed for the v1 browser-side-decode preview.
- **Color licensing/path**: NVENC on node; per-view AI matting (RVM is GPL-3.0)
  vs a permissive model — unchanged from prior notes, still open.
