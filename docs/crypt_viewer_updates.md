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

## How to use this file
- Each entry has a date, a summary, the **protocol impact**, and a concrete
  **viewer action** (often a code snippet).
- After applying an entry, change its `Status:` to `applied <date>` so the next
  drop is easy to diff.
- The authoritative protocol spec lives in `crypt-capture/docs/preview_protocol.md`;
  the key parts are restated here so you don't need that file.

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
