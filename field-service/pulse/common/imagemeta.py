"""Strip location and device metadata from images (LLD §11) without re-encoding the pixels.

    JPEG  drop APP1 (EXIF, XMP), APP2-APP15 except ICC colour profiles (APP2 "ICC_PROFILE"),
          APP13 (IPTC/Photoshop) and COM; keep everything from SOS onwards untouched
    PNG   drop eXIf, tEXt, zTXt, iTXt and tIME chunks
    WebP  drop EXIF and XMP chunks and clear the matching flags in VP8X

`strip(data, mime)` returns cleaned bytes, or raises UnsupportedImage for formats it cannot clean
(callers refuse those instead of storing them with metadata). Malformed input raises too.
"""
from __future__ import annotations

import struct

SUPPORTED = {"image/jpeg", "image/png", "image/webp"}


class UnsupportedImage(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def strip(data: bytes, mime: str | None = None) -> bytes:
    kind = sniff(data)
    if kind is None:
        raise UnsupportedImage(f"cannot clean metadata from {mime or 'unknown'} data")
    return {"image/jpeg": _jpeg, "image/png": _png, "image/webp": _webp}[kind](data)


def _jpeg(data: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            raise UnsupportedImage("malformed JPEG: expected a marker")
        while i < n and data[i] == 0xFF:            # fill bytes
            i += 1
        if i >= n:
            raise UnsupportedImage("malformed JPEG: truncated")
        marker = data[i]
        i += 1
        if marker == 0xD9:                          # EOI
            out += b"\xff\xd9"
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # markers without a length
            out += bytes([0xFF, marker])
            continue
        if i + 2 > n:
            raise UnsupportedImage("malformed JPEG: truncated length")
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            raise UnsupportedImage("malformed JPEG: bad segment length")
        segment = data[i:i + length]
        i += length
        if marker == 0xDA:                          # SOS: copy the rest (scan data) unchanged
            out += bytes([0xFF, marker]) + segment + data[i:]
            return bytes(out)
        keep = True
        if 0xE1 <= marker <= 0xEF:                  # APP1..APP15
            keep = marker == 0xE2 and segment[2:14] == b"ICC_PROFILE\x00"
        elif marker == 0xFE:                        # COM
            keep = False
        if keep:
            out += bytes([0xFF, marker]) + segment
    raise UnsupportedImage("malformed JPEG: no image data")


_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _png(data: bytes) -> bytes:
    out = bytearray(data[:8])
    i = 8
    while i < len(data):
        if i + 8 > len(data):
            raise UnsupportedImage("malformed PNG: truncated chunk")
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            raise UnsupportedImage("malformed PNG: bad chunk length")
        if ctype not in _PNG_DROP:
            out += data[i:end]
        i = end
        if ctype == b"IEND":
            return bytes(out)
    raise UnsupportedImage("malformed PNG: no IEND")


def _webp(data: bytes) -> bytes:
    chunks = bytearray()
    i = 12
    end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    vp8x_at = None
    while i + 8 <= end:
        ctype = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        stop = i + 8 + size + (size & 1)
        if stop > end + 1:
            raise UnsupportedImage("malformed WebP: bad chunk size")
        if ctype not in (b"EXIF", b"XMP "):
            if ctype == b"VP8X":
                vp8x_at = len(chunks)
            chunks += data[i:stop]
        i = stop
    if vp8x_at is not None:                         # clear EXIF (0x08) and XMP (0x04) flags
        flags_at = vp8x_at + 8
        chunks[flags_at] &= ~0x0C & 0xFF
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WEBP" + bytes(chunks)
