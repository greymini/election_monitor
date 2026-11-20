"""A TelegramClient that keeps everything in memory, for the browser demo.

It records what the bot would have sent, per chat, as the current state of each message (sent,
edited in place, buttons removed, deleted), so the demo page can draw a phone screen. It also serves
files the demo "uploaded", so media_fetch downloads them as it would from Telegram. Thread-safe: the
worker thread and request handlers share one instance.
"""
from __future__ import annotations

import hashlib
import itertools
import threading
from typing import Any

from pulse.bot.client import TelegramApiError


class MemoryTelegramClient:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._messages: dict[tuple[int, int], dict[str, Any]] = {}
        self._toasts: dict[int, list[dict[str, Any]]] = {}
        self._callback_chats: dict[str, int] = {}
        self._rev: dict[int, int] = {}
        self._files: dict[str, bytes] = {}
        self._mimes: dict[str, str] = {}

    # ---- demo side --------------------------------------------------------------------------

    def next_message_id(self) -> int:
        with self._lock:
            return next(self._ids)

    def add_file(self, data: bytes, mime: str | None = None) -> tuple[str, str]:
        """Store bytes as Telegram would; returns (file_id, file_unique_id). Same bytes, same unique id."""
        unique = "u" + hashlib.sha256(data).hexdigest()[:20]
        with self._lock:
            file_id = f"demo-{next(self._ids)}-{unique}"
            self._files[file_id] = data
            self._mimes[file_id] = mime or "application/octet-stream"
        return file_id, unique

    def file(self, file_id: str) -> bytes | None:
        with self._lock:
            return self._files.get(file_id)

    def file_mime(self, file_id: str) -> str | None:
        with self._lock:
            return self._mimes.get(file_id)

    def record_user_message(self, chat_id: int, message_id: int, **content: Any) -> None:
        """What the person typed or attached, shown on their side of the pane."""
        with self._lock:
            self._put(chat_id, message_id, {"from": "user", **content})

    def expect_callback(self, callback_query_id: str, chat_id: int) -> None:
        with self._lock:
            self._callback_chats[callback_query_id] = chat_id

    def chat(self, chat_id: int) -> dict[str, Any]:
        with self._lock:
            msgs = sorted((dict(m) for (c, _), m in self._messages.items() if c == chat_id),
                          key=lambda m: m["message_id"])
            return {"rev": self._rev.get(chat_id, 0), "messages": msgs, "toasts": list(self._toasts.get(chat_id, []))}

    def message(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        with self._lock:
            m = self._messages.get((chat_id, message_id))
            return dict(m) if m else None

    def clear(self) -> None:
        """Forget every chat (after loading sample data); files stay so stored media keeps working."""
        with self._lock:
            self._messages.clear()
            self._toasts.clear()
            self._callback_chats.clear()
            for chat_id in self._rev:
                self._rev[chat_id] += 1

    # ---- internals --------------------------------------------------------------------------

    def _bump(self, chat_id: int) -> None:
        self._rev[chat_id] = self._rev.get(chat_id, 0) + 1

    def _put(self, chat_id: int, message_id: int, content: dict[str, Any]) -> None:
        self._messages[(chat_id, message_id)] = {"chat_id": chat_id, "message_id": message_id, "edits": 0,
                                                 "deleted": False, **content}
        self._bump(chat_id)

    def _edit(self, chat_id: int, message_id: int, **changes: Any) -> bool:
        with self._lock:
            m = self._messages.get((chat_id, message_id))
            if m is None:            # a card sent before the demo's chat history was cleared
                m = {"chat_id": chat_id, "message_id": message_id, "from": "bot", "text": None,
                     "reply_markup": None, "edits": 0, "deleted": False}
                self._messages[(chat_id, message_id)] = m
            if m["deleted"]:
                raise TelegramApiError("editMessageText", "Bad Request: message to edit not found", 400)
            m.update(changes)
            m["edits"] += 1
            self._bump(chat_id)
            return True

    # ---- TelegramClient protocol ------------------------------------------------------------

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        with self._lock:
            mid = next(self._ids)
            self._put(chat_id, mid, {"from": "bot", "text": text, "reply_markup": reply_markup})
        return {"message_id": mid, "chat": {"id": chat_id, "type": "private"}, "text": text}

    def send_photo(self, chat_id: int, photo: str, caption: str | None = None, reply_markup: dict | None = None) -> dict:
        with self._lock:
            mid = next(self._ids)
            self._put(chat_id, mid, {"from": "bot", "text": caption, "photo": photo, "reply_markup": reply_markup})
        return {"message_id": mid, "chat": {"id": chat_id, "type": "private"}}

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> bool:
        return self._edit(chat_id, message_id, text=text, reply_markup=reply_markup)

    def edit_message_caption(self, chat_id: int, message_id: int, caption: str, reply_markup: dict | None = None) -> bool:
        return self._edit(chat_id, message_id, text=caption, reply_markup=reply_markup)

    def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None = None) -> bool:
        return self._edit(chat_id, message_id, reply_markup=reply_markup)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        with self._lock:
            chat_id = self._callback_chats.pop(callback_query_id, None)
            if chat_id is not None and text:
                self._toasts.setdefault(chat_id, []).append({"text": text, "seq": next(self._ids)})
                self._bump(chat_id)
        return True

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        with self._lock:
            m = self._messages.get((chat_id, message_id))
            if m is None:
                raise TelegramApiError("deleteMessage", "Bad Request: message to delete not found", 400)
            m["deleted"] = True
            self._bump(chat_id)
        return True

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        return []                     # the demo hands updates straight to the dispatcher

    def get_webhook_info(self) -> dict:
        return {"url": ""}

    def get_file(self, file_id: str) -> dict:
        data = self.file(file_id)
        if data is None:
            raise TelegramApiError("getFile", "Bad Request: invalid file_id", 400)
        return {"file_id": file_id, "file_size": len(data), "file_path": f"files/{file_id}"}

    def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        data = self.file(file_path.split("/", 1)[1])
        if data is None:
            raise TelegramApiError("downloadFile", "HTTP 404", 404)
        if len(data) > max_bytes:
            raise TelegramApiError("downloadFile", "file larger than the download limit", 413)
        return data

    def close(self) -> None:
        pass
