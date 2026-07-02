# Remote streaming — publishing the live viewer to the internet

Goal: a friend anywhere in the world opens a URL and watches the live
volumetric stream from the rig, in real time. This doc is the feasibility
assessment + runbook. Status: the two prerequisites are **done** (viewer is
Vercel-deployable; the relay survives slow WAN viewers); the runbook below
works today with zero further code.

## The shape of the problem

Two very different pieces travel very different distances:

1. **The page (viewer app)** — static files. Trivial: Vercel/Netlify/Pages.
2. **The stream** — a fat, stateful WebSocket. Vercel **cannot** host it
   (serverless: no long-lived sockets). The relay must be reachable at a
   public **`wss://`** endpoint (an https page blocks plain `ws://` — mixed
   content). The stream is the whole assessment.

## Measured numbers (grounding)

`CPV1` per point with color = 12 B pos + 3 B rgb = **15 B/pt**. Verified
end-to-end with `sim_node` → relay → headless client: **24.6k pts →
369 KB/frame**. So for a typical background-subtracted subject
(~20–30k pts, the 30 fps sweet spot on the Orin):

| point count | KB/frame | Mbps @30fps | @15fps | @10fps |
|---|---|---|---|---|
| 15k | 225 | 54 | 27 | 18 |
| 25k | 369 | **89** | 44 | 30 |
| 30k | 450 | 108 | 54 | 36 |

Rule of thumb at 25k pts: **fps ≈ link_Mbps ÷ 3**, capped at 30 by the
sensor. Egress volume: 30 fps ≈ **40 GB/hour per viewer** — fine for a home
uplink or a flat-rate VPS, ruinous on per-GB cloud egress (AWS et al.).

Upstream (Jetson → relay) is much lighter: RVL depth (~14×) + raw foreground
RGB ≈ 120–135 KB/frame ≈ **~30 Mbps @30fps** — and drops a lot further once
color goes JPEG (deferred item).

## Topology options (in build order)

### A. Today, zero code: laptop relay + Cloudflare Tunnel + Vercel page

```
Jetson ──TCP:9000──► laptop preview_server ──ws:8080──► cloudflared ══https/wss══► friend's browser
                                                          (Cloudflare edge)
```

- Deploy the `crypt` repo to Vercel (its `vercel.json` is committed; static
  Vite build). One-time: `vercel` CLI or "Import Git Repository" in the
  Vercel dashboard.
- On the laptop: `cloudflared tunnel --url http://localhost:8080` (a free
  **quick tunnel**, no account/domain needed) → prints
  `https://<random>.trycloudflare.com`, which proxies WebSockets with TLS.
  With a Cloudflare-managed domain, a **named tunnel** gives a stable
  hostname instead.
- Share: `https://<app>.vercel.app/?ws=wss://<random>.trycloudflare.com`.
- Don't use ngrok's free tier for this: 1 GB/month transfer ≈ 90 seconds of
  30 fps stream.

**Frame rate:** bounded by min(your uplink, friend's downlink) ÷ 3 Mbit.
Fiber both ends → **30 fps**. A typical 20–40 Mbps cable uplink → **7–13
fps**, still perfectly watchable because the relay now drops stale frames
per viewer (each viewer floats at their own link rate, always seeing the
newest frame; the browser HUD shows the achieved rate). Latency +50–150 ms
one-way intercontinental — irrelevant for spectating.

### B. Relay on a VPS (pay the uplink once, fan out in the cloud)

```
Jetson ──~30 Mbps──► VPS preview_server ──~90 Mbps × N viewers──► browsers
```

Run `central/preview_server.py` (stdlib+numpy, no other deps) on a small
flat-rate VPS (e.g. Hetzner ~€5/mo, 20 TB included ≈ 500 viewer-hours) with
a TLS terminator in front (caddy/nginx → ws:8080). The Jetson connects out
to the VPS (`--host <vps>`; LAN discovery obviously doesn't apply — set the
IP/hostname, or a WireGuard tunnel). Wins over A: your home uplink carries
the ~30 Mbps node stream **once** regardless of viewer count, and viewer
fan-out rides datacenter bandwidth. Viewers still need ~90 Mbps *down* for
30 fps at 25k pts (common on fiber/cable down, rare on mobile).

### C. Wire diet — `CPV2` quantized + deflate ✅ DONE (measured)

Implemented: positions are **bbox-quantized uint16, per-axis
delta-encoded** (9 B/pt with color; error ≤~0.02 mm, far below sensor
noise) and the relay negotiates **permessage-deflate** per connection
(browsers get it automatically; the delta encoding is what makes the bytes
compressible — raw float32 barely deflated). Measured on the sim stream at
24.6k pts: **369 KB → 221 KB raw → ~134 KB wire ≈ 33 Mbps @30 fps** (was
89). Spec: `docs/preview_protocol.md` (`CPV2`); the viewer parses CPV2
with a CPV1 fallback. 30 fps now fits ordinary ~35 Mbps broadband.

### D. Endgame — browser-side decode / WebRTC

Ship the node's own wire format to the browser: RVL depth (+ JPEG color)
decoded in a Web Worker/Wasm, unprojected client-side (the relay becomes a
dumb forwarder, or the intrinsics ride along). **~10–15 Mbps for 30 fps**,
works on almost any connection, and scales to 4 cameras. Already on the
deferred roadmap ("Web Worker / Wasm RVL"). WebRTC data channels (UDP,
congestion-controlled) fit here too and matter once WebXR's latency budget
bites; overkill for spectating.

## Realistic frame-rate answer

With `CPV2` + deflate now live (≈1.1 Mbit/frame at 25k pts instead of 3):
**fps ≈ link_Mbps ÷ 1.1**, so:

| setup | friend's fps |
|---|---|
| Tunnel from laptop, ≥35 Mbps uplink | **30** (sensor cap) |
| Tunnel from laptop, 20 Mbps uplink | ~18, smooth, newest-frame |
| B (VPS), friend on ≥35 Mbps down | **30** |
| D (RVL to browser), almost anywhere incl. good 4G/5G | **30** |

Recording is untouched by all of this — it's local-on-node full fidelity,
downloaded after; only the live preview rides the WAN.

## What changed in the code for this

- **Relay (`central/preview_server.py`): per-viewer latest-frame outbox.**
  `_broadcast` used to do a blocking `sendall` per client *from the node
  ingest loop* — one slow WAN viewer froze the pipeline for everyone.
  Each viewer now has a `_ViewerOutbox` (own sender thread + 1-slot
  mailbox): the ingest loop never blocks, each viewer drops stale frames
  independently and floats at their own link speed. Verified: a fully
  stalled viewer connected while a fast local viewer held the full ingest
  fps. Viewer reader disconnect-resets are also swallowed (WAN clients
  vanish abruptly).
- **Viewer (`crypt` repo): `vercel.json`** (static build → `dist/`) and a
  **scheme-aware default WS URL** (`wss:` on https pages); `?ws=` always
  wins.

## Open items

- **Access control:** a public relay exposes the *control channel* too —
  anyone with the URL can send `capture_bg`/`set_camera`. Fine for a quick
  demo link; before anything semi-permanent, add a shared token (e.g.
  `?key=` checked at the WS handshake, commands gated on it).
- `CPV2` quantization + compression (topology C) when >~13 fps over a
  normal uplink is wanted.
- JPEG color node→relay (~30 → ~10 Mbps uplink) — already deferred-listed
  for 4-cam, also helps topology B.
