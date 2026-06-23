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
