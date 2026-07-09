"""
RVL — Fast Lossless Depth Image Compression.

Faithful Python port of Andrew Wilson's reference C codec
("Fast Lossless Depth Image Compression", ISS 2017, Microsoft Research):
https://www.microsoft.com/en-us/research/uploads/prod/2018/09/p100-wilson.pdf

RVL is the depth-stream codec each capture node uses before sending frames
over the LAN: it is lossless on 16-bit depth, tiny, and fast on any CPU
(x86 or ARM/Jetson). It exploits the long runs of zeros that masked depth
images contain (everything outside the human is 0), plus small deltas between
adjacent valid-depth pixels.

Two implementations live here, producing **bit-identical** output:
  * `_compress_py` / `_decompress_py` — pure-stdlib reference (no deps; works
    on the Nano's Python 3.6 and anywhere numpy is missing). Per-pixel Python
    loops → ~1 fps on a weak CPU.
  * `_compress_np` / `_decompress_np` — vectorized NumPy. This is the M0
    real-time path: the per-pixel arithmetic and the VLE nibble packing are
    done with array ops, leaving only a short per-*segment* loop on decode.

The public `compress` / `decompress` dispatch to NumPy when it is importable
and fall back to the pure-Python reference otherwise. The two are exercised
against each other in `tests/test_rvl.py`.

Public API:
    compress(depth: Sequence[int]) -> bytes
    decompress(data: bytes, num_pixels: int) -> array('H')

`depth` is row-major unsigned 16-bit depth (0 = invalid/background).
"""

from array import array
import struct

try:
    import numpy as _np
except ImportError:                                   # keep the spine dep-free
    _np = None

# Optional Numba JIT for the DECODE hot path on the relay. RVL decode is
# inherently SEQUENTIAL (variable-length bit codes — a pointer chase the NumPy
# path can't fully vectorize), which is exactly where a JIT-compiled scalar loop
# beats NumPy: near-C speed with no build step. Import-guarded like NumPy, so a
# box without numba just uses the NumPy path (both bit-identical to the
# reference). The node's ENCODE path is left on NumPy (it's already fast enough
# on the Jetson, and numba on ARM is heavier to install).
try:
    from numba import njit as _njit
    _HAVE_NUMBA = True
except ImportError:
    _HAVE_NUMBA = False

_U32 = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Pure-Python reference (no numpy) — also the spec the numpy path must match.
# ---------------------------------------------------------------------------

class _Writer:
    __slots__ = ("words", "word", "nibbles")

    def __init__(self):
        self.words = []
        self.word = 0
        self.nibbles = 0

    def encode_vle(self, value):
        # Variable-length encoding: 3 data bits + 1 continuation bit per nibble.
        while True:
            nibble = value & 0x7
            value >>= 3
            if value:
                nibble |= 0x8
            self.word = ((self.word << 4) | nibble) & _U32
            self.nibbles += 1
            if self.nibbles == 8:
                self.words.append(self.word)
                self.nibbles = 0
                self.word = 0
            if not value:
                break

    def finish(self):
        if self.nibbles:
            self.words.append((self.word << (4 * (8 - self.nibbles))) & _U32)
        return self.words


class _Reader:
    __slots__ = ("words", "idx", "word", "nibbles")

    def __init__(self, words):
        self.words = words
        self.idx = 0
        self.word = 0
        self.nibbles = 0

    def decode_vle(self):
        value = 0
        bits = 29
        while True:
            if self.nibbles == 0:
                self.word = self.words[self.idx]
                self.idx += 1
                self.nibbles = 8
            nibble = self.word & 0xF0000000
            shifted = (nibble << 1) & _U32
            value |= shifted >> bits if bits >= 0 else shifted << (-bits)
            self.word = (self.word << 4) & _U32
            self.nibbles -= 1
            bits -= 3
            if not (nibble & 0x80000000):
                break
        return value


def _zigzag(delta):
    # Map signed delta to an unsigned value (small magnitudes -> small codes).
    return ((delta << 1) ^ (delta >> 31)) & _U32


def _unzigzag(positive):
    return (positive >> 1) ^ -(positive & 1)


def _compress_py(depth):
    """Compress a row-major unsigned-16-bit depth sequence to RVL bytes."""
    w = _Writer()
    n = len(depth)
    i = 0
    previous = 0
    while i < n:
        zeros = 0
        while i < n and depth[i] == 0:
            i += 1
            zeros += 1
        w.encode_vle(zeros)

        start = i
        nonzeros = 0
        while i < n and depth[i] != 0:
            i += 1
            nonzeros += 1
        w.encode_vle(nonzeros)

        for j in range(start, start + nonzeros):
            current = depth[j]
            delta = current - previous
            w.encode_vle(_zigzag(delta))
            previous = current

    words = w.finish()
    return struct.pack("<%dI" % len(words), *words)


def _decompress_py(data, num_pixels):
    """Inverse of compress(); returns an array('H') of length num_pixels."""
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    r = _Reader(words)
    out = array("H", bytes(2 * num_pixels))
    pos = 0
    previous = 0
    remaining = num_pixels
    while remaining > 0:
        zeros = r.decode_vle()
        remaining -= zeros
        for _ in range(zeros):
            out[pos] = 0
            pos += 1
        nonzeros = r.decode_vle()
        remaining -= nonzeros
        for _ in range(nonzeros):
            current = (previous + _unzigzag(r.decode_vle())) & 0xFFFF
            out[pos] = current
            pos += 1
            previous = current
    return out


# ---------------------------------------------------------------------------
# Vectorized NumPy path (M0). Bit-identical to the reference above.
# ---------------------------------------------------------------------------

def _vle_pack(vals):
    """VLE-encode an array of non-negative ints and bit-pack to RVL bytes.

    Mirrors `_Writer`: each value -> nibbles of 3 data bits (LSB group first)
    with a continuation bit on all but the last; nibbles packed 8/word with the
    first nibble in the most-significant position; words little-endian.
    """
    v = _np.asarray(vals, dtype=_np.uint64).ravel()
    if v.size == 0:
        return b""

    # Nibbles per value = max(1, ceil(bitlen/3)); bounded (<=11 for 32-bit vals).
    counts = _np.ones(v.size, dtype=_np.int64)
    tmp = v >> _np.uint64(3)
    while tmp.any():
        counts += tmp > 0
        tmp >>= _np.uint64(3)
    maxnib = int(counts.max())

    k = _np.arange(maxnib, dtype=_np.uint64)                  # nibble index
    data = (v[:, None] >> (_np.uint64(3) * k)[None, :]) & _np.uint64(0x7)
    cont = (k[None, :] < (counts[:, None] - 1)) * _np.uint64(0x8)
    nib = (data | cont).astype(_np.uint8)
    valid = k[None, :] < counts[:, None]
    stream = nib[valid]                                       # row-major = order

    pad = (-stream.size) % 8
    if pad:
        stream = _np.concatenate([stream, _np.zeros(pad, _np.uint8)])
    grp = stream.reshape(-1, 8).astype(_np.uint32)
    sh = (4 * (7 - _np.arange(8))).astype(_np.uint32)        # [28,24,...,0]
    words = _np.bitwise_or.reduce(grp << sh[None, :], axis=1)
    return words.astype("<u4").tobytes()


def _compress_np(depth):
    d = _np.ascontiguousarray(depth).astype(_np.uint16, copy=False).ravel()
    n = d.size
    if n == 0:
        return b""

    mask = d != 0
    # Run-length structure of the mask (alternating zero / nonzero runs).
    starts = _np.concatenate(([0], _np.flatnonzero(mask[1:] != mask[:-1]) + 1))
    lengths = _np.diff(_np.concatenate((starts, [n]))).astype(_np.int64)
    run_nz = mask[starts]
    if run_nz[0]:                              # stream always starts with zeros
        lengths = _np.concatenate(([0], lengths))
    if lengths.size & 1:                       # pair as (zeros, nonzeros)
        lengths = _np.concatenate((lengths, [0]))
    Z = lengths[0::2]
    NZ = lengths[1::2]
    S = Z.size

    # Zigzag deltas across all nonzero pixels in order (previous starts at 0).
    nz_vals = d[mask].astype(_np.int64)
    n_nz = nz_vals.size
    deltas = nz_vals.copy()
    if n_nz:
        deltas[1:] -= nz_vals[:-1]
    zz = ((deltas << 1) ^ (deltas >> 63)) & _U32

    # Value stream per segment: [Z_k, NZ_k, zz of segment k...].
    total = 2 * S + n_nz
    vals = _np.empty(total, dtype=_np.uint64)
    bstart = _np.zeros(S, dtype=_np.int64)
    _np.cumsum((2 + NZ)[:-1], out=bstart[1:])
    vals[bstart] = Z
    vals[bstart + 1] = NZ
    if n_nz:
        seg = _np.repeat(_np.arange(S), NZ)
        cum_nz = _np.zeros(S, dtype=_np.int64)
        _np.cumsum(NZ[:-1], out=cum_nz[1:])
        local = _np.arange(n_nz) - cum_nz[seg]
        vals[bstart[seg] + 2 + local] = zz
    return _vle_pack(vals)


def _vle_unpack(data):
    """Inverse of `_vle_pack`: RVL bytes -> uint64 array of decoded values."""
    words = _np.frombuffer(data, dtype="<u4")
    if words.size == 0:
        return _np.empty(0, dtype=_np.uint64)
    sh = (4 * (7 - _np.arange(8))).astype(_np.uint32)
    nibs = ((words[:, None] >> sh[None, :]) & _np.uint32(0xF)).astype(_np.uint8).ravel()
    bits = (nibs & 0x7).astype(_np.uint64)
    ends = (nibs & 0x8) == 0                  # last nibble of each value
    value_id = _np.cumsum(ends) - ends        # 0-based value index per nibble
    nvals = int(value_id[-1]) + 1
    # Local nibble position within its value -> bit shift (3 bits per nibble).
    counts = _np.bincount(value_id, minlength=nvals)
    vstart = _np.zeros(nvals, dtype=_np.int64)
    _np.cumsum(counts[:-1], out=vstart[1:])
    local = _np.arange(nibs.size, dtype=_np.int64) - vstart[value_id]
    contrib = bits << (_np.uint64(3) * local.astype(_np.uint64))
    # contrib values fit well under 2**53, so float64 bincount is exact.
    return _np.bincount(value_id, weights=contrib.astype(_np.float64),
                        minlength=nvals).astype(_np.uint64)


def _decompress_np(data, num_pixels):
    out = _np.zeros(num_pixels, dtype=_np.uint16)
    if num_pixels == 0:
        return array("H", out.tobytes())
    vals = _vle_unpack(data)

    # Parse (zeros, nonzeros) segment headers. The recurrence is data-dependent
    # (a pointer chase), so this short loop runs once per *segment*, not pixel;
    # masked depth has long runs => few segments. Trailing zero-padding values
    # are ignored because we stop at num_pixels.
    Zs, NZs = [], []
    p = pixels = 0
    while pixels < num_pixels:
        z = int(vals[p]); nz = int(vals[p + 1])
        Zs.append(z); NZs.append(nz)
        pixels += z + nz
        p += 2 + nz
    Z = _np.array(Zs, dtype=_np.int64)
    NZ = _np.array(NZs, dtype=_np.int64)
    S = Z.size
    n_nz = int(NZ.sum())
    if n_nz == 0:
        return array("H", out.tobytes())

    bstart = _np.zeros(S, dtype=_np.int64)
    _np.cumsum((2 + NZ)[:-1], out=bstart[1:])
    cum_nz = _np.zeros(S, dtype=_np.int64)
    _np.cumsum(NZ[:-1], out=cum_nz[1:])
    seg = _np.repeat(_np.arange(S), NZ)
    local = _np.arange(n_nz) - cum_nz[seg]

    zz = vals[bstart[seg] + 2 + local]
    dd = (zz >> _np.uint64(1)).astype(_np.int64) ^ -(zz & _np.uint64(1)).astype(_np.int64)
    nz_vals = (_np.cumsum(dd) & 0xFFFF).astype(_np.uint16)   # running depth

    pix_start = _np.zeros(S, dtype=_np.int64)
    _np.cumsum((Z + NZ)[:-1], out=pix_start[1:])
    out[(pix_start + Z)[seg] + local] = nz_vals
    return array("H", out.tobytes())


# ---------------------------------------------------------------------------
# Numba-JIT decode (relay hot path). One tight sequential pass — the same
# algorithm as `_decompress_py`, compiled to near-C. Bit-identical to both
# other paths (cross-checked in tests/test_rvl.py).
# ---------------------------------------------------------------------------

if _HAVE_NUMBA:

    @_njit(cache=True)
    def _decode_vle_nb(words, idx, word, nibbles):
        # Mirrors _Reader.decode_vle. uint32 wraparound in numba == the
        # reference's `& _U32` masks; `value` is uint64 (fits any RVL value).
        value = _np.uint64(0)
        bits = 29
        while True:
            if nibbles == 0:
                word = words[idx]
                idx += 1
                nibbles = 8
            nibble = word & _np.uint32(0xF0000000)
            # Explicit uint32() casts force the 32-bit wrap the reference gets
            # via `& _U32` — numba widens `uint32 << n` to a signed 64-bit type,
            # which would otherwise leak high bits into the next nibble read.
            shifted = _np.uint32(nibble << _np.uint32(1))
            if bits >= 0:
                value |= _np.uint64(shifted) >> _np.uint64(bits)
            else:
                value |= _np.uint64(shifted) << _np.uint64(-bits)
            word = _np.uint32(word << _np.uint32(4))
            nibbles -= 1
            bits -= 3
            if (nibble & _np.uint32(0x80000000)) == 0:
                break
        return value, idx, word, nibbles

    @_njit(cache=True)
    def _decompress_numba(words, num_pixels):
        out = _np.zeros(num_pixels, dtype=_np.uint16)
        idx = 0
        word = _np.uint32(0)
        nibbles = 0
        pos = 0
        previous = _np.int64(0)
        while pos < num_pixels:
            zeros, idx, word, nibbles = _decode_vle_nb(words, idx, word, nibbles)
            for _z in range(_np.int64(zeros)):
                if pos >= num_pixels:                    # guard malformed streams
                    break
                pos += 1                                 # out already zero-filled
            nonzeros, idx, word, nibbles = _decode_vle_nb(words, idx, word, nibbles)
            for _n in range(_np.int64(nonzeros)):
                if pos >= num_pixels:
                    break
                zz, idx, word, nibbles = _decode_vle_nb(words, idx, word, nibbles)
                delta = _np.int64(zz >> _np.uint64(1)) ^ -_np.int64(zz & _np.uint64(1))
                previous = (previous + delta) & 0xFFFF
                out[pos] = _np.uint16(previous)
                pos += 1
        return out


# ---------------------------------------------------------------------------
# Public dispatch: Numba (decode) / NumPy when available, else pure Python.
# ---------------------------------------------------------------------------

def compress(depth):
    """Compress a row-major unsigned-16-bit depth sequence to RVL bytes."""
    if _np is not None:
        return _compress_np(depth)
    return _compress_py(depth)


def decoder_name():
    """Which decompress() implementation THIS process will use ('numba',
    'numpy' or 'python') — for the relay's startup log. The numba path is an
    import-guarded optional dep, so "installed on the machine" is not the same
    as "active in this process" (wrong venv / a failed import falls back
    silently); the relay prints this so a 10x-slower decode is visible."""
    if _HAVE_NUMBA and _np is not None:
        return "numba"
    if _np is not None:
        return "numpy"
    return "python"


def decompress(data, num_pixels):
    """Inverse of compress(); returns an array('H') of length num_pixels.

    Prefers the Numba JIT path (near-C sequential decode) on the relay, else the
    vectorized NumPy path, else the pure-Python reference — all bit-identical."""
    if _HAVE_NUMBA and num_pixels and _np is not None:
        words = _np.frombuffer(data, dtype=_np.uint32)
        return array("H", _decompress_numba(words, num_pixels).tobytes())
    if _np is not None:
        return _decompress_np(data, num_pixels)
    return _decompress_py(data, num_pixels)
