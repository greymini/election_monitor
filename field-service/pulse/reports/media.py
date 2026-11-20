"""Media intake (LLD §5.3, §8 media_fetch).

At receipt only Telegram's ids and metadata are written (status 'pending'), and a media_fetch job is
queued: nothing is lost if the download comes later or fails. The job asks getFile, downloads within
the Bot API limit, strips image metadata, stores by sha256 (identical bytes are stored once) and
links the stored object to the asset. After the last failed attempt the asset is marked 'failed'
and the report keeps everything else.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from pulse.common import imagemeta
from pulse.common.clock import utcnow
from pulse.common.storage import Storage
from pulse.reports import service as reports
from pulse.worker import queue

log = logging.getLogger(__name__)

DOC_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
KIND_OF = {"photo": "photo", "voice": "voice", "audio": "voice", "video": "video", "video_note": "video",
           "document": "document"}


@dataclass(frozen=True)
class Item:
    tg_kind: str
    kind: str
    file_id: str
    file_unique_id: str
    file_size: int | None
    mime: str | None
    duration_s: int | None
    width: int | None
    height: int | None
    media_group_id: str | None

    @property
    def receipt_capable(self) -> bool:
        return self.kind == "photo" or (self.kind == "document" and self.mime in DOC_TYPES)


@dataclass(frozen=True)
class Refusal:
    reason: str          # too_big | image_unsupported | type_unsupported


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


def describe(message: dict[str, Any], max_bytes: int) -> Item | Refusal | Location | None:
    """What a message carries, if anything this intake handles. None: no media and no location."""
    group = message.get("media_group_id")
    if message.get("location") or message.get("venue"):
        loc = message.get("location") or message["venue"]["location"]
        return Location(lat=float(loc["latitude"]), lon=float(loc["longitude"]))
    if message.get("photo"):
        best = max(message["photo"], key=lambda p: (p.get("file_size") or 0, p.get("width", 0) * p.get("height", 0)))
        return _item("photo", best, group, max_bytes, mime="image/jpeg")
    for tg_kind in ("voice", "audio", "video", "video_note"):
        if message.get(tg_kind):
            obj = message[tg_kind]
            return _item(tg_kind, obj, group, max_bytes, mime=obj.get("mime_type"))
    if message.get("document"):
        doc = message["document"]
        mime = (doc.get("mime_type") or "").lower()
        if mime not in DOC_TYPES:
            return Refusal("image_unsupported" if mime.startswith("image/") else "type_unsupported")
        return _item("document", doc, group, max_bytes, mime=mime)
    if message.get("sticker") or message.get("animation") or message.get("contact") or message.get("poll"):
        return Refusal("type_unsupported")
    return None


def _item(tg_kind: str, obj: dict[str, Any], group: str | None, max_bytes: int, *, mime: str | None) -> Item | Refusal:
    size = obj.get("file_size")
    if size is not None and size > max_bytes:
        return Refusal("too_big")
    return Item(tg_kind=tg_kind, kind=KIND_OF[tg_kind], file_id=obj["file_id"], file_unique_id=obj["file_unique_id"],
                file_size=size, mime=mime, duration_s=obj.get("duration"), width=obj.get("width"),
                height=obj.get("height"), media_group_id=group)


def register(conn: psycopg.Connection, member: dict[str, Any], item: Item, *, now: datetime | None = None) -> dict[str, Any]:
    """Write the asset at receipt and queue its download."""
    now = now or utcnow()
    asset = conn.execute(
        """INSERT INTO field.media_asset (ac_id, kind, tg_kind, tg_file_size, mime, duration_s, width, height,
               tg_file_id, tg_file_unique_id, uploaded_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (member["ac_id"], item.kind, item.tg_kind, item.file_size, item.mime, item.duration_s, item.width, item.height,
         item.file_id, item.file_unique_id, member["member_id"], now),
    ).fetchone()
    queue.enqueue(conn, "media_fetch", {"asset_id": str(asset["asset_id"])}, run_after=now)
    return asset


class PermanentFailure(Exception):
    """Retrying cannot help (file too big for the Bot API, unsupported image)."""


def _linked_reports(conn: psycopg.Connection, asset_id: UUID) -> list[UUID]:
    return [r["report_id"] for r in conn.execute("SELECT report_id FROM field.report_media WHERE asset_id = %s",
                                                 (asset_id,))]


def fetch(conn: psycopg.Connection, client, storage: Storage, asset_id: UUID, *, max_bytes: int,
          now: datetime | None = None) -> str:
    """The media_fetch job body. Returns 'stored', 'reused' or 'already'. Raises on failure."""
    now = now or utcnow()
    asset = conn.execute("SELECT * FROM field.media_asset WHERE asset_id = %s FOR UPDATE", (asset_id,)).fetchone()
    if asset is None or asset["status"] == "stored":
        return "already"
    same = conn.execute(
        """SELECT sha256, storage_key, bytes FROM field.media_asset
           WHERE tg_file_unique_id = %s AND status = 'stored' LIMIT 1""",
        (asset["tg_file_unique_id"],),
    ).fetchone()
    if same is not None and storage.exists(same["storage_key"]):
        _mark_stored(conn, asset_id, same["sha256"], same["storage_key"], same["bytes"], now, reused=True)
        return "reused"

    info = client.get_file(asset["tg_file_id"])
    if (info.get("file_size") or 0) > max_bytes:
        raise PermanentFailure("file larger than the Bot API download limit")
    data = client.download_file(info["file_path"], max_bytes=max_bytes)
    if asset["kind"] == "photo" or (asset["mime"] or "").startswith("image/"):
        try:
            data = imagemeta.strip(data, asset["mime"])
        except imagemeta.UnsupportedImage as e:
            raise PermanentFailure(str(e)) from None
    obj = storage.put(data)
    _mark_stored(conn, asset_id, obj.sha256, obj.key, obj.bytes, now, reused=not obj.created)
    return "stored"


def _mark_stored(conn, asset_id, sha256, key, size, now, *, reused: bool) -> None:
    conn.execute(
        """UPDATE field.media_asset SET status = 'stored', sha256 = %s, storage_key = %s, bytes = %s, stored_at = %s,
               error = NULL WHERE asset_id = %s""",
        (sha256, key, size, now, asset_id),
    )
    for report_id in _linked_reports(conn, asset_id):
        reports.event(conn, report_id, "media_stored", actor=None, now=now,
                      data={"asset_id": str(asset_id), "sha256": sha256, "deduplicated": reused})


def mark_failed(conn: psycopg.Connection, asset_id: UUID, error: str, *, now: datetime | None = None) -> list[UUID]:
    """Final failure: the asset is failed, the report keeps everything else. Returns the linked reports."""
    now = now or utcnow()
    conn.execute("UPDATE field.media_asset SET status = 'failed', error = %s WHERE asset_id = %s AND status <> 'stored'",
                 (error[:500], asset_id))
    linked = _linked_reports(conn, asset_id)
    for report_id in linked:
        reports.event(conn, report_id, "media_failed", actor=None, now=now, data={"asset_id": str(asset_id)})
    return linked

