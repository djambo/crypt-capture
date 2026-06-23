# CLAUDE.md — crypt (viewer) : live point-cloud viewer handoff

> Drop this in the **`crypt`** repo as `CLAUDE.md` (or merge into the existing
> one). It is a self-contained handoff from the `crypt-capture` repo: enough to
> build the live viewer without any other context. Keep it current as you work.

## The two repos (and where you are)

- **`crypt-capture`** — the capture/delivery pipeline: Azure Kinect on a Jetson →
  depth mask → RVL compress → stream. A central "preview server" decodes those
  frames, unprojects them to a 3D point cloud, and relays them to browsers over
  a WebSocket.
- **`crypt`** (THIS repo) — the three.js (r148) renderer / viewer + the eventual
  **WebXR** experience. Your job: render what the preview server sends.

## North Star (why this exists)

`crypt` is an "edge of reality" framework. End goal: a **WebXR/VR** experience
where the user can't tell **prerecorded** volumetric clips from **real-time**
streams of whoever steps into the capture volume (incl. "step out of your own
body"). Two consequences to honor from the start:

1. **One shared world coordinate frame** — live and recorded register to the
   same metric space.
2. **Source-agnostic rendering** — the renderer plays "point-cloud frames in
   world space" regardless of whether they came live over the wire or off disk.
   So build the live viewer around a generic "draw these XYZ(+RGB) points"
   core that prerecorded playback can reuse later.

## Immediate task

Build a minimal **live point-cloud viewer**: connect to the preview WebSocket,
parse `CPV1` binary frames, render them as a three.js point cloud with orbit
controls. No build step required (CDN importmap is fine for v0). This is the
"can I see my Jetson streaming into a web page" milestone.

## The wire format you consume: `CPV1` (little-endian binary)

One WebSocket **binary** message = one frame. Header (20 bytes) then payload:

| field | type | meaning |
|---|---|---|
| magic | `4s` | `CPV1` |
| flags | `u32` | bit0 = positions present (always 1); bit1 = `rgb` present |
| sensor_id | `u32` | source sensor (0..N-1) |
| frame_id | `u32` | capture frame index (low 32 bits) |
| count | `u32` | number of points |

Then:
1. **positions** — `count × 3 × float32`, metres, **view/world space**:
   `x` right, `y` up, `z` toward the viewer (camera looks down −z). Subject sits
   around `z ≈ −1.2 m`. Drop straight into a three.js position attribute.
2. **rgb** *(if flag bit1 set)* — `count × 3 × uint8`, one triple per point
   (same order as positions), starting at byte `20 + count*12`. **The server now
   sends this** (depth-aligned color). Read it into a `color` BufferAttribute
   (`normalized: true`) and set `vertexColors: true` so points show real color;
   still handle bit1 = 0 (geometry only) as a fallback.

`count` varies per frame (only valid points are sent, after downsampling) — read
it from the header every message; never assume a fixed size.

Bump the magic (`CPV2`…) for breaking changes; use a new flag bit for additive
optional blocks so older viewers can ignore them.

## How it all runs (3 pieces, 3 places)

```
Jetson (crypt-capture)        Laptop/central (crypt-capture)      Browser (crypt)
 kinect_node  ──TCP:9000──►   preview_server  ──WebSocket:8080──►  this viewer
```

1. **Central/laptop** — run the relay (from the `crypt-capture` checkout):
   ```bash
   python3 -m central.preview_server --stride 2     # nodes:9000  ws:8080
   ```
2. **Jetson** — stream the camera at the laptop (from `crypt-capture`):
   ```bash
   python3 -m node.kinect_node --host <LAPTOP_IP> --port 9000 --sensor 0 --frames 0
   ```
   (`--frames 0` = stream until Ctrl-C. No Kinect handy? Use `node.sim_node` with
   the same `--host/--port` for synthetic frames.)
3. **Browser** — serve this viewer and open it pointed at the relay:
   ```bash
   python3 -m http.server 5173        # from the viewer dir
   # open: http://localhost:5173/?ws=ws://<LAPTOP_IP>:8080
   ```
   (If the browser, relay, and you are all on the laptop, `ws://localhost:8080`.)

**Acceptance test:** the page shows a live, orbitable point cloud of whatever the
Jetson sees, updating in real time. With `sim_node` you'll see a moving blob —
proves the path without hardware.

## Suggested v0 implementation (no build step)

Single `index.html` using three r148 from a CDN importmap. Adapt to this repo's
existing renderer if one is already set up (reuse the point-cloud material /
camera rig the rendering R&D branches already have — see below).

```html
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#0a0a0c;overflow:hidden}
  #hud{position:fixed;top:8px;left:8px;color:#8f8;font:12px monospace}
</style>
<script type="importmap">{ "imports": {
  "three": "https://unpkg.com/three@0.148.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.148.0/examples/jsm/"
}}</script></head><body>
<div id="hud">connecting…</div>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const hud = document.getElementById("hud");
const wsUrl = new URLSearchParams(location.search).get("ws")
            || `ws://${location.hostname}:8080`;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(devicePixelRatio);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.01, 100);
camera.position.set(0, 0, 0.6);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0, -1.3);          // subject sits ~1.3m down -z

const MAX = 400000;                         // preallocate, update draw range
const geom = new THREE.BufferGeometry();
geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MAX*3), 3));
geom.setAttribute("color", new THREE.BufferAttribute(new Uint8Array(MAX*3), 3, true));
const mat = new THREE.PointsMaterial({ size: 0.006, sizeAttenuation: true, vertexColors: true });
const points = new THREE.Points(geom, mat);
scene.add(points);

let fps = 0, last = performance.now(), n = 0;
const ws = new WebSocket(wsUrl);
ws.binaryType = "arraybuffer";
ws.onopen  = () => hud.textContent = "connected " + wsUrl;
ws.onclose = () => hud.textContent = "disconnected";
ws.onmessage = (e) => {
  const dv = new DataView(e.data);
  if (dv.getUint32(0, false) !== 0x43505631) return;   // "CPV1"
  const flags = dv.getUint32(4, true);
  const count = Math.min(dv.getUint32(16, true), MAX);
  geom.attributes.position.array.set(new Float32Array(e.data, 20, count*3));
  geom.attributes.position.needsUpdate = true;
  if (flags & 2) {                                   // rgb present
    geom.attributes.color.array.set(new Uint8Array(e.data, 20 + count*12, count*3));
    geom.attributes.color.needsUpdate = true;
  }
  geom.setDrawRange(0, count);
  geom.computeBoundingSphere();
  n++; const t = performance.now();
  if (t-last > 500){ fps = (n*1000/(t-last)).toFixed(1); n=0; last=t;
    hud.textContent = `${wsUrl}  ${count} pts  ${fps} fps`; }
};

addEventListener("resize", () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script></body></html>
```

Notes / gotchas:
- WebSocket binary: set `binaryType = "arraybuffer"` (done above).
- Reallocating a `Float32Array` every frame is wasteful — preallocate `MAX` and
  use `setDrawRange(0, count)` (done above). Tune `MAX` to the server's
  `--max-points`.
- Color: the snippet above already reads the `rgb` block when `flags & 2`. The
  `color` attribute is `Uint8Array` with `normalized: true` so 0–255 maps to
  0–1, and the material has `vertexColors: true`.

## Existing rendering R&D in this repo (reuse it)

Prior prototypes (branches `…-edl`, `…-vat`, `…-mesh`, `…-surfel`, `…-ewa`,
`…-trimesh`): GL_POINTS sphere-impostors + Eye-Dome Lighting, Vertex Animation
Textures, PCA-normal surfel splatting, EWA blending, per-frame trimeshes. Key
learnings: flat per-splat color reads as "tiled cells" (fix via EWA blending or
a real interpolated mesh); the capture's per-point colors are high quality. For
the *live* path start simple (plain points), then layer EDL/splatting for looks.

## Roadmap for this repo

1. **v0 live viewer** (above) — see the Jetson stream. ← start here
2. **Nicer rendering** — point size by depth, EDL, eventually surfel/EWA so the
   live cloud reads as a surface, not dots.
3. **Color** — consume the `rgb` block when the server enables it.
4. **WebXR** — put the cloud in VR (tighter motion-to-photon budget; the live
   path is the latency-critical one).
5. **Prerecorded playback** — same renderer core, frames off disk instead of the
   wire (source-agnostic). This is what enables the "live vs recorded are
   indistinguishable" North Star.

## Keep this file current

Living doc — update it in the same change whenever a decision, the run commands,
the protocol version, or the roadmap status changes. A stale CLAUDE.md is worse
than none.
