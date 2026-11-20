import struct
import zlib

import pytest

from pulse.common import imagemeta
from pulse.common.storage import LocalDiskStorage, Storage, key_for, sha256_hex

GPS = b"GPSLatitude 24.19 N GPSLongitude 86.30 E Model DemoPhone"


def make_jpeg(exif: bool = True, comment: bool = True) -> bytes:
    def seg(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body

    out = b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if exif:
        out += seg(0xE1, b"Exif\x00\x00" + GPS)
        out += seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>" + GPS + b"</x:xmpmeta>")
    out += seg(0xE2, b"ICC_PROFILE\x00\x01\x01" + b"colour")
    if comment:
        out += seg(0xFE, b"taken by " + GPS)
    out += seg(0xDB, b"\x00" + bytes(range(64)))
    out += seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\xff\x00\x56" + b"\xff\xd9"
    return out


def make_png(text: bool = True) -> bytes:
    def chunk(ctype: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    if text:
        out += chunk(b"tEXt", b"Comment\x00" + GPS) + chunk(b"eXIf", b"MM\x00*" + GPS)
    return out + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")


def make_webp(exif: bool = True) -> bytes:
    def chunk(ctype: bytes, body: bytes) -> bytes:
        return ctype + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")

    vp8x = bytes([0x08 | 0x04 if exif else 0, 0, 0, 0]) + b"\x00\x00\x00" + b"\x00\x00\x00"
    body = chunk(b"VP8X", vp8x) + chunk(b"VP8 ", b"\x00" * 10)
    if exif:
        body += chunk(b"EXIF", GPS) + chunk(b"XMP ", GPS)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def test_jpeg_metadata_removed_pixels_kept():
    raw = make_jpeg()
    clean = imagemeta.strip(raw, "image/jpeg")
    assert GPS not in clean and b"Exif" not in clean and b"xmpmeta" not in clean
    assert b"ICC_PROFILE" in clean                       # colour profile kept
    assert clean.endswith(b"\x12\x34\xff\x00\x56\xff\xd9")  # scan data untouched
    assert imagemeta.strip(clean) == clean               # idempotent


def test_png_and_webp_metadata_removed():
    clean = imagemeta.strip(make_png())
    assert GPS not in clean and clean.endswith(make_png(text=False)[-12:])
    assert imagemeta.strip(clean) == clean
    webp = imagemeta.strip(make_webp())
    assert GPS not in webp and webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    assert struct.unpack("<I", webp[4:8])[0] == len(webp) - 8
    assert webp[20] & 0x0C == 0                          # EXIF/XMP flags cleared in VP8X


def test_unsupported_or_broken_images_raise():
    with pytest.raises(imagemeta.UnsupportedImage):
        imagemeta.strip(b"\x00\x00\x00\x18ftypheic", "image/heic")
    with pytest.raises(imagemeta.UnsupportedImage):
        imagemeta.strip(make_jpeg()[:40])


def test_local_storage_is_content_addressed(tmp_path):
    store = LocalDiskStorage(tmp_path / "m")
    assert isinstance(store, Storage)
    a = store.put(b"same bytes")
    b = store.put(b"same bytes")
    assert a.created and not b.created and a.key == b.key == key_for(sha256_hex(b"same bytes"))
    assert store.get(a.key) == b"same bytes" and store.exists(a.key)
    assert len(list((tmp_path / "m").rglob("*"))) == 3   # sha256/ab/ and the one file
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
    store.delete(a.key)
    assert not store.exists(a.key)
