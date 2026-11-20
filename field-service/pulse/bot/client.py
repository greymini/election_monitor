"""Thin Telegram Bot API client over httpx. No bot framework.

The token is part of every request URL, so never log URLs from here; callers should keep the
`httpx` logger at WARNING or above.
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx

API_BASE = "https://api.telegram.org"


class TelegramApiError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None,
                 retry_after: int | None = None):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.error_code = error_code
        self.retry_after = retry_after          # seconds, from a 429 response


def is_rate_limited(e: TelegramApiError) -> bool:
    return e.error_code == 429


def is_not_modified(e: TelegramApiError) -> bool:
    """Telegram rejects an edit that changes nothing; for us that edit has succeeded."""
    return "message is not modified" in str(e)


class TelegramClient(Protocol):
    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict: ...

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool: ...

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict | bool: ...

    def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None = None) -> dict | bool: ...

    def send_photo(self, chat_id: int, photo: str, caption: str | None = None, reply_markup: dict | None = None) -> dict: ...

    def edit_message_caption(self, chat_id: int, message_id: int, caption: str, reply_markup: dict | None = None) -> dict | bool: ...

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]: ...

    def delete_message(self, chat_id: int, message_id: int) -> bool: ...

    def get_webhook_info(self) -> dict: ...

    def get_file(self, file_id: str) -> dict: ...

    def download_file(self, file_path: str, *, max_bytes: int) -> bytes: ...


class HttpxTelegramClient:
    def __init__(self, token: str, *, http: httpx.Client | None = None, base_url: str = API_BASE):
        self._url = f"{base_url}/bot{token}/"
        self._file_url = f"{base_url}/file/bot{token}/"
        # long polling holds the request open for `timeout` seconds; leave headroom
        self._http = http or httpx.Client(timeout=httpx.Timeout(10.0, read=70.0))

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            resp = self._http.post(self._url + method, json={k: v for k, v in payload.items() if v is not None})
        except httpx.HTTPError as e:
            # strip the request (and with it the token-bearing URL) from what we re-raise
            raise TelegramApiError(method, type(e).__name__) from None
        try:
            body = resp.json()
        except ValueError:
            raise TelegramApiError(method, f"HTTP {resp.status_code}, non-JSON body", resp.status_code) from None
        if not body.get("ok"):
            raise TelegramApiError(method, body.get("description", "unknown error"), body.get("error_code"),
                                   (body.get("parameters") or {}).get("retry_after"))
        return body["result"]

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return self._call("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        return self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict | bool:
        try:
            return self._call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                                  "reply_markup": reply_markup})
        except TelegramApiError as e:
            if is_not_modified(e):
                return True
            raise

    def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None = None) -> dict | bool:
        try:
            return self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                         "reply_markup": reply_markup or {"inline_keyboard": []}})
        except TelegramApiError as e:
            if is_not_modified(e):
                return True
            raise

    def send_photo(self, chat_id: int, photo: str, caption: str | None = None, reply_markup: dict | None = None) -> dict:
        """photo: a Telegram file_id (re-sent without downloading)."""
        return self._call("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption,
                                        "reply_markup": reply_markup})

    def edit_message_caption(self, chat_id: int, message_id: int, caption: str, reply_markup: dict | None = None) -> dict | bool:
        try:
            return self._call("editMessageCaption", {"chat_id": chat_id, "message_id": message_id, "caption": caption,
                                                     "reply_markup": reply_markup})
        except TelegramApiError as e:
            if is_not_modified(e):
                return True
            raise

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Bots may delete their own messages, and incoming ones in private chats, within 48 hours."""
        return self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        return self._call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]})

    def get_webhook_info(self) -> dict:
        return self._call("getWebhookInfo", {})

    def set_webhook(self, url: str, secret_token: str, *, drop_pending_updates: bool = False) -> bool:
        """Telegram then posts every update to url with header X-Telegram-Bot-Api-Secret-Token."""
        return self._call("setWebhook", {"url": url, "secret_token": secret_token,
                                         "allowed_updates": ["message", "callback_query"],
                                         "drop_pending_updates": drop_pending_updates})

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        return self._call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_file(self, file_id: str) -> dict:
        """File object with file_path; the download link is valid for at least an hour."""
        return self._call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        """Stream the file, refusing to read past max_bytes."""
        try:
            with self._http.stream("GET", self._file_url + file_path) as resp:
                if resp.status_code != 200:
                    raise TelegramApiError("downloadFile", f"HTTP {resp.status_code}", resp.status_code)
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise TelegramApiError("downloadFile", "file larger than the download limit", 413)
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as e:
            raise TelegramApiError("downloadFile", type(e).__name__) from None

    def close(self) -> None:
        self._http.close()
