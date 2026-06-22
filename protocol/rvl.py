"""
RVL — Fast Lossless Depth Image Compression.

Faithful Python port of Andrew Wilson's reference C codec
("Fast Lossless Depth Image Compression", ISS 2017, Microsoft Research):
https://www.microsoft.com/en-us/research/uploads/prod/2018/09/p100-wilson.pdf

RVL is the depth-stream codec each capture node uses before sending frames
over the LAN: it is lossless on 16-bit depth, tiny (~100 lines), and fast on
any CPU (x86 or ARM/Jetson). It exploits the long runs of zeros that masked
depth images contain (everything outside the human is 0), plus small deltas
between adjacent valid-depth pixels.

Public API:
    compress(depth: Sequence[int]) -> bytes
    decompress(data: bytes, num_pixels: int) -> array('H')

`depth` is row-major unsigned 16-bit depth (0 = invalid/background).
"""

from array import array
import struct

_U32 = 0xFFFFFFFF


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


def compress(depth):
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


def decompress(data, num_pixels):
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
