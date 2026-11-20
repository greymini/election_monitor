"""Placeholder media for the demo's sample reports, built with the standard library only."""
from __future__ import annotations

import io
import math
import struct
import wave
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(rgb: tuple[int, int, int], width: int = 320, height: int = 240) -> bytes:
    """A solid colour with a lighter diagonal band, so different reports look different."""
    r, g, b = rgb
    light = bytes(min(255, c + 60) for c in rgb)
    base = bytes((r, g, b))
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")                        # filter type 0
        for x in range(width):
            row += light if abs((x - y * width // height) % 160) < 24 else base
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + _chunk(b"IEND", b""))


def wav(seconds: float = 3.0, hz: float = 220.0, rate: int = 8000) -> bytes:
    """A quiet tone standing in for a voice note (the fake ASR engine does not listen to it)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * hz * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()
