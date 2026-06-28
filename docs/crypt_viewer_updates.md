# Capture → Viewer update log (crypt-capture → crypt)

> **For the `crypt` viewer agent.** This is a one-way changelog of changes in the
> **`crypt-capture`** repo that affect the viewer (mainly the `CPV1` preview
> protocol and how to render it). It is delivered as a standalone file so it
> **does not overwrite this repo's own `CLAUDE.md`** — that file is yours and
> evolves with your local work. **Read the unapplied entries below, fold the
> relevant bits into your code / your `CLAUDE.md`, then mark them applied.**
>
> Newest entries on top. The original full handoff (repo relationship, North
> Star, base `CPV1` spec, v0 viewer sketch) was delivered separately as your
> initial `CLAUDE.md`; these entries amend it.

## How to use this file (note)
Two recent server-side fixes need **no viewer change** — they just make the
points correct: (a) **lens-distortion correction** (the relay now undistorts via
the Kinect's coefficients, so flat surfaces stop bowing into cones), and (b) a
**speckle filter** on the node (removes isolated ToF-noise points after
background capture, default on). Optional: add a small **"Denoise" slider**
(0–4, default 2) that sends `{"cmd":"set_denoise","min_neighbors":N}` if you want
users to tune it; 0 turns it off.

## How to use this file
- Each entry has a date, a summary, the **protocol impact**, and a concrete
  **viewer action** (often a code snippet).
- After applying an entry, change its `Status:` to `applied <date>` so the next
  drop is easy to diff.
- The authoritative protocol spec lives in `crypt-capture/docs/preview_protocol.md`;
  the key parts are restated here so you don't need that file.

---

## 2026-06-28 — IMU orientation: gravity vector in `CPV1` (floor/up)
**Status: NEW — not yet applied**

**Summary.** Each sensor now ships a **gravity (down) unit vector** from its
Kinect accelerometer, giving the cloud an initial orientation (which way is down
/ where the floor is) before any extrinsic calibration. The node reads the IMU
and sends it to the relay (new internal `CIMU` handshake message); the relay
re-expresses it in the cloud/view frame and attaches it to **every** `CPV1`
frame as a new optional trailing block. Use it to draw a floor grid and a
camera-orientation gizmo, and (later) to seed floor-plane detection in the cloud.

**Protocol impact (additive, backward compatible).**
- `CPV1` header unchanged (20 bytes). **`flags` bit2 (`0x4`) = gravity present.**
- New trailing block **after** positions (and rgb, if present): **`3 × float32`**
  = a normalized down vector in the view frame (`x` right, `y` up, `z` toward
  viewer), so for a level camera it's ≈ `(0, -1, 0)`. World "up" is its negation.
- Offset: `20 + count*12`, plus `count*3` when rgb (bit1) is set.
- bit2 may be 0 on any frame (no IMU) — keep handling its absence.

**⚠️ Gotcha.** When rgb is present the gravity block starts at a **non-4-byte-
aligned** offset, so a `new Float32Array(buffer, offset, 3)` view **throws**
(`start offset … multiple of 4`). Read it with a `DataView` instead:

```js
// after reading count, flags, and advancing `off` past positions (+ rgb):
let gravity = null
if (flags & 0x4 && off + 12 <= buffer.byteLength) {
  gravity = [dv.getFloat32(off, true),
             dv.getFloat32(off + 4, true),
             dv.getFloat32(off + 8, true)]   // cloud-frame down unit vector
}
```

**Viewer action.** Render the orientation: a subtle floor `GridHelper` tilted so
its +Y normal aligns to `-gravity` (the IMU-estimated floor under the cloud),
and a small gizmo at the **camera origin** (0,0,0 — the cloud is in camera space)
= a wireframe cube + R/G/B axis sticks + a "down" stick along `gravity`. The gap
between the axis-aligned gizmo and the tilted grid shows the camera's tilt.

**Hardware note (FYI, no viewer action).** The Kinect IMU has its own axes
(rotated from the depth optical frame by a factory extrinsic pyk4a doesn't
reliably expose), so on real hardware the "down" direction may need an axis/sign
tweak — the same iterative bring-up the depth/colour paths went through. The
`sim_node` feeds a known-good, slightly-tilted vector so the whole path + your
viewer are testable headless (you'll see a ~10° floor tilt).

---

## 2026-06-25 — Camera controls (pick which Kinect data to send)
**Status: NEW — not yet applied**

**Summary.** A new upstream command, `set_camera`, lets the UI pick the Azure
Kinect capture mode **live** — the depth FOV mode and the alignment direction
(plus color resolution / fps, available but not surfaced in the UI yet). The
stream adapts automatically: the node restarts the sensor when needed, re-reads
its intrinsics, and re-sends the `CCAL` handshake, so the relay rebuilds the
cloud at the new resolution. **No `CPV1` change and no rendering change** — points
just arrive at a different density. (Implemented & verified end-to-end headless:
flipping alignment took the sim cloud from ~98k pts on a 640×576 grid to a denser
1280×720 color grid, intrinsics rebuilt each time.)

**Why you'd use it.** `depth_to_color` alignment streams **one point per color
pixel** instead of one per depth pixel → far more color detail / a denser cloud,
which is what you want for a higher-res colored mesh. Cost: more points and some
holes where depth is sparse. `color_to_depth` (default) is the original path.

**Command (WebSocket text → server → node), all fields optional:**
```json
{"cmd":"set_camera",
 "depth_mode":"NFOV_UNBINNED",   // or NFOV_2X2BINNED, WFOV_2X2BINNED, WFOV_UNBINNED
 "align":"color_to_depth",       // or depth_to_color
 "color_resolution":"720P",      // 720P..3072P (optional; mostly matters in depth_to_color)
 "fps":30}                        // 5/15/30, auto-clamped (optional)
```
- `depth_mode` / `color_resolution` / `fps` restart the sensor (~1 s gap).
- `align` is a free per-frame switch (instant).
- No ack — the feedback is the cloud changing. **A camera change resets the
  node's background plate** (grid is a different size), so re-capture background
  after changing the camera.

**Viewer action — two dropdowns in the control panel:**
1. **depth FOV** select: NFOV narrow (640×576) / NFOV narrow binned (320×288) /
   WFOV wide binned (512×512) / WFOV wide (1024², 15fps).
2. **align** select: "colour → depth (default)" / "depth → colour (more colour)".
3. On change, send the current `{cmd:"set_camera", depth_mode, align}` (the node
   ignores unchanged fields); re-send on reconnect (a fresh node starts at its
   defaults); and clear the background-status label (the plate is gone).

```js
function sendCamera() {
  ws.send(JSON.stringify({ cmd: 'set_camera', depth_mode, align }))
}
```
Note: `depth_to_color` at higher color resolutions can exceed the viewer's
`MAX_POINTS` / the server's `--max-points` (200k default) — both clamp safely;
raise them if you want the full density.

---

## 2026-06-23 — "Capture Background" button (subject-only points)
**Status: NEW — not yet applied**

**Summary.** New upstream commands let the user snapshot the empty scene once and
then stream **only the subject** (points closer than the background) — floor/walls
removed at any distance, far fewer points, cleaner look. Add a small UI for it.
(No protocol change to `CPV1`; these are upstream JSON commands like `set_depth`.)

**Commands (WebSocket text → server → node):**
- `{"cmd":"capture_bg","frames":60}` — average ~60 frames into a background plate,
  then auto-enable subtraction. **The scene must be empty during capture** (user
  steps out). At 30 fps, 60 frames ≈ 2 s.
- `{"cmd":"clear_bg"}` — turn subtraction off (stream everything in range again).
- `{"cmd":"set_bg_margin","mm":50}` — tolerance that absorbs depth wobble; raise
  if background flickers back in, lower if subject edges get eaten. Default 50.

**Viewer action — a "Capture Background" button:**
1. On click: show a label like **"Capturing background — step out of frame…"**,
   then send `{"cmd":"capture_bg","frames":60}`.
2. There's no completion message back from the node yet, so drive the label
   optimistically off the known duration: `frames / ~30fps` seconds (+~0.5 s
   buffer) → then show **"Background set — subject only"**. (A real ack channel
   can come later if needed.)
3. Add a **"Clear"** control → `{"cmd":"clear_bg"}`, and optionally a margin
   slider (200…0 mm, default 50) → `set_bg_margin`.

```js
function captureBackground(frames = 60) {
  showLabel("Capturing background — step out of frame…");
  ws.send(JSON.stringify({ cmd: "capture_bg", frames }));
  setTimeout(() => showLabel("Background set — subject only"), frames/30*1000 + 500);
}
function clearBackground() { ws.send(JSON.stringify({ cmd: "clear_bg" })); }
```

Pairs naturally with the dual-FPS work below: once only the subject streams, the
point count drops a lot and render fps should jump.

---

## 2026-06-23 — Rendering perf + dual FPS HUD (IMPORTANT)
**Status: NEW — not yet applied**

**Why.** Full-resolution streams render at only ~8 fps in the viewer even though
the Jetson is producing faster, but shrinking the cloud (fewer points) hits
30 fps. That means the viewer is **render-bound**, and we currently can't tell
because there's only one fps number. Two asks: (1) show **received fps** vs
**rendered fps** separately, and (2) fix the rendering so it isn't the
bottleneck. (No protocol change — `CPV1` is unchanged.)

**1. Dual FPS HUD.** Track and display both:
- **recv fps** — increment a counter in `ws.onmessage`; report per second.
- **render fps** — increment in your `requestAnimationFrame` loop; report per second.

If recv is high but render is low → render-bound (apply the fixes below). If recv
is low → network/relay-bound (reduce points via the node's `--preview-stride` or
the upcoming subject-clipping; the server also now logs its own `fps in / pts /
KB/f` so you can compare all three numbers).

**2. Rendering performance fixes (these are almost certainly your 8 fps):**
- **Stop calling `geometry.computeBoundingSphere()` every message** — it's O(N)
  per frame over every point and is the classic point-cloud perf killer. Instead
  set `points.frustumCulled = false` once and never compute it.
- **Don't do heavy work in `onmessage`.** Just copy the latest bytes into the
  attribute and set a dirty flag; do the actual `needsUpdate`/draw in the rAF
  loop. One render per animation frame, not per network message (they decouple
  cleanly and fix the "two frame rates").
- **Preallocate once** (`MAX` points), `attr.setUsage(THREE.DynamicDrawUsage)`,
  `geometry.setDrawRange(0, count)`, and set `attr.updateRange = {offset:0,
  count: count*itemSize}` so only the live points upload to the GPU each frame.
- Avoid reallocating `Float32Array`/`BufferAttribute` per frame.

```js
points.frustumCulled = false;            // no bounding-sphere needed
posAttr.setUsage(THREE.DynamicDrawUsage);
colAttr.setUsage(THREE.DynamicDrawUsage);
let latest = null, recv = 0, rend = 0;
ws.onmessage = (e) => { latest = e.data; recv++; };   // cheap: just stash + count
function frame() {
  requestAnimationFrame(frame);
  if (latest) {
    const dv = new DataView(latest), flags = dv.getUint32(4,true);
    const count = Math.min(dv.getUint32(16,true), MAX);
    posAttr.array.set(new Float32Array(latest, 20, count*3));
    posAttr.updateRange = {offset:0, count: count*3}; posAttr.needsUpdate = true;
    if (flags & 2) { colAttr.array.set(new Uint8Array(latest, 20+count*12, count*3));
                     colAttr.updateRange = {offset:0, count: count*3}; colAttr.needsUpdate = true; }
    geometry.setDrawRange(0, count); latest = null;
  }
  controls.update(); renderer.render(scene, camera); rend++;
}
// once per second: hud.textContent = `recv ${recv} fps · render ${rend} fps · ${count} pts`; recv=rend=0;
```

**3. Quality.** The cloud looks rough as flat square dots. Cheap wins: round
points (a small radial-alpha sprite texture or a tiny fragment shader discarding
outside `length(gl_PointCoord-0.5)>0.5`), and size-by-distance
(`sizeAttenuation: true`, tune `size`). Eye-Dome Lighting / surfel splatting are
the bigger upgrades later (the rendering R&D branches have prior art).

**Design note.** We will **never** want to push the full point count — the plan
is to clip on the capture side and stream only the subject's points
(background-plate subtraction is coming on the node). So optimize the viewer for
"tens of thousands of points at 30 fps," not hundreds of thousands.

---

## 2026-06-23 — Control plane: viewer can set the depth mask live
**Status: NEW — not yet applied**

**Summary.** The viewer can now send commands **upstream** over the same
WebSocket (viewer → server → node), and the first one lets the user tune the
**depth mask** live — i.e. how much background is kept. The capture node masks
out everything outside `[min,max]` millimetres before sending; the user noticed
the cloud cuts off at a fixed distance, and this makes that adjustable from the
UI. Low-rate and independent of the frame stream (no perf impact).

**Protocol (upstream).** Send a WebSocket **text** message with a JSON command:
```js
ws.send(JSON.stringify({ cmd: "set_depth", min: 400, max: 4000 })); // millimetres
```
- `min`/`max` are depth-mask bounds in mm; either is optional.
- The server forwards it to all connected nodes, which apply it within a frame
  or two (you'll see the point count change).
- Defaults on the node are min 500 / max 2500 mm. Sensible UI range: ~200–6000 mm.

**Viewer action.** Add a small control (two sliders or number inputs for
near/far, in mm) and send the command on change (debounce ~100 ms so you don't
spam while dragging). Example:
```js
function setDepth(minMm, maxMm) {
  if (ws.readyState === WebSocket.OPEN)
    ws.send(JSON.stringify({ cmd: "set_depth", min: minMm, max: maxMm }));
}
```
No response message is sent back yet; the feedback is simply the cloud updating.
(A status/echo channel can come later if the UI needs to show confirmed values.)

---

## 2026-06-23 — Live color in `CPV1` (rgb block)
**Status: NEW — not yet applied**

**Summary.** The preview stream now carries **per-point color**. The capture node
warps the Kinect color image into the depth camera's geometry and sends RGB for
the foreground pixels; the relay attaches it to each point. Previously every
point was white (geometry only).

**Protocol impact (additive, backward compatible).**
- `CPV1` header is unchanged: `magic(4s) flags(u32) sensor(u32) frame(u32) count(u32)`, 20 bytes, little-endian.
- **`flags` bit1 (`0x2`) = rgb present.** (bit0 = positions, always 1.)
- Layout when bit1 is set:
  - positions: `count × 3 × float32` at byte `20` (unchanged)
  - **rgb: `count × 3 × uint8` at byte `20 + count*12`** — one triple per point,
    **same order as positions**, 0–255 per channel.
- bit1 may be 0 on any frame (e.g. camera color dropped) — **keep handling the
  geometry-only case**; don't assume color is always there.

**Viewer action.** Add a normalized `color` attribute + `vertexColors`, and read
the rgb block when present:

```js
// geometry setup
geom.setAttribute("color",
  new THREE.BufferAttribute(new Uint8Array(MAX*3), 3, /*normalized*/ true));
const mat = new THREE.PointsMaterial({ size: 0.006, sizeAttenuation: true,
                                       vertexColors: true });

// in ws.onmessage, after reading count:
const flags = dv.getUint32(4, true);
geom.attributes.position.array.set(new Float32Array(e.data, 20, count*3));
geom.attributes.position.needsUpdate = true;
if (flags & 2) {                                   // rgb present
  geom.attributes.color.array.set(new Uint8Array(e.data, 20 + count*12, count*3));
  geom.attributes.color.needsUpdate = true;
}
geom.setDrawRange(0, count);
```

`normalized: true` maps 0–255 → 0–1 for you. No other changes; the run commands
and connection URL are the same.

**Heads-up (capture side, FYI only — no viewer action).** The node now sends
color uncompressed (raw foreground RGB, no JPEG). Fine for one camera; we'll
switch to JPEG/NVENC before 4 cameras. If `count` and payload sizes look bigger
than before, that's the color block.
