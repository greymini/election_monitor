"""Recorded Telegram update shapes (Bot API), with invented ids and names."""
from __future__ import annotations

USER_A = {"id": 700000101, "is_bot": False, "first_name": "Demo", "last_name": "Rep", "language_code": "hi"}
USER_B = {"id": 700000202, "is_bot": False, "first_name": "Demo", "last_name": "Other", "language_code": "hi"}
USER_C = {"id": 700000303, "is_bot": False, "first_name": "Demo", "last_name": "Third", "language_code": "hi"}
USER_D = {"id": 700000404, "is_bot": False, "first_name": "Demo", "last_name": "Fourth", "language_code": "hi"}
USER_E = {"id": 700000505, "is_bot": False, "first_name": "Demo", "last_name": "Fifth", "language_code": "hi"}
GROUP_CHAT = {"id": -100700000999, "title": "Demo Group", "type": "supergroup"}
DATE = 1795000000


def _private_chat(user: dict) -> dict:
    return {"id": user["id"], "first_name": user["first_name"], "last_name": user.get("last_name", ""), "type": "private"}


def text(update_id: int, body: str, user: dict = USER_A) -> dict:
    msg = {"message_id": update_id % 100000, "from": user, "chat": _private_chat(user), "date": DATE, "text": body}
    if body.startswith("/"):
        msg["entities"] = [{"offset": 0, "length": len(body.split()[0]), "type": "bot_command"}]
    return {"update_id": update_id, "message": msg}


def start(update_id: int, token: str | None, user: dict = USER_A) -> dict:
    return text(update_id, f"/start {token}" if token else "/start", user)


def group_text(update_id: int, body: str, user: dict = USER_A) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id % 100000, "from": user, "chat": GROUP_CHAT, "date": DATE, "text": body},
    }


def channel_post(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "channel_post": {
            "message_id": 1,
            "sender_chat": {"id": -100700000888, "title": "Demo Channel", "type": "channel"},
            "chat": {"id": -100700000888, "title": "Demo Channel", "type": "channel"},
            "date": DATE,
            "text": "hello",
        },
    }


def photo(update_id: int, user: dict = USER_A) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id % 100000,
            "from": user,
            "chat": _private_chat(user),
            "date": DATE,
            "photo": [
                {"file_id": "DEMO-FILE-small", "file_unique_id": "DEMOu1", "file_size": 1200, "width": 90, "height": 67},
                {"file_id": "DEMO-FILE-large", "file_unique_id": "DEMOu2", "file_size": 48000, "width": 800, "height": 600},
            ],
        },
    }


def callback(update_id: int, data: str, user: dict = USER_A, chat: dict | None = None, message_id: int = 42) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq-{update_id}",
            "from": user,
            "message": {
                "message_id": message_id,
                "from": {"id": 1234567, "is_bot": True, "first_name": "Pulse Test", "username": "pulse_test_bot"},
                "chat": chat or _private_chat(user),
                "date": DATE,
                "text": "menu",
            },
            "chat_instance": "-555000111",
            "data": data,
        },
    }


# ---- media (recorded shapes; ids and sizes invented) -----------------------------------------

def _media_message(update_id: int, user: dict, payload: dict, caption: str | None, group: str | None) -> dict:
    msg = {"message_id": update_id % 100000, "from": user, "chat": _private_chat(user), "date": DATE, **payload}
    if caption is not None:
        msg["caption"] = caption
    if group is not None:
        msg["media_group_id"] = group
    return {"update_id": update_id, "message": msg}


def photo_msg(update_id: int, file_id: str, *, user: dict = USER_A, size: int = 48000, caption: str | None = None,
              group: str | None = None, unique: str | None = None) -> dict:
    sizes = [
        {"file_id": file_id + "-s", "file_unique_id": (unique or file_id) + "-s", "file_size": 1200, "width": 90, "height": 67},
        {"file_id": file_id, "file_unique_id": unique or file_id, "file_size": size, "width": 1280, "height": 960},
    ]
    return _media_message(update_id, user, {"photo": sizes}, caption, group)


def voice_msg(update_id: int, file_id: str, *, user: dict = USER_A, duration: int = 40, size: int = 64000,
              caption: str | None = None) -> dict:
    return _media_message(update_id, user, {"voice": {"file_id": file_id, "file_unique_id": file_id, "duration": duration,
                                                      "mime_type": "audio/ogg", "file_size": size}}, caption, None)


def video_msg(update_id: int, file_id: str, *, user: dict = USER_A, size: int = 900000, duration: int = 20) -> dict:
    return _media_message(update_id, user, {"video": {"file_id": file_id, "file_unique_id": file_id, "duration": duration,
                                                      "width": 640, "height": 360, "mime_type": "video/mp4",
                                                      "file_size": size}}, None, None)


def document_msg(update_id: int, file_id: str, mime: str, *, user: dict = USER_A, size: int = 30000,
                 caption: str | None = None) -> dict:
    return _media_message(update_id, user, {"document": {"file_id": file_id, "file_unique_id": file_id,
                                                         "file_name": "scan", "mime_type": mime, "file_size": size}},
                          caption, None)


def location_msg(update_id: int, *, user: dict = USER_A, lat: float = 24.1912, lon: float = 86.3012) -> dict:
    return _media_message(update_id, user, {"location": {"latitude": lat, "longitude": lon}}, None, None)


def sticker_msg(update_id: int, *, user: dict = USER_A) -> dict:
    return _media_message(update_id, user, {"sticker": {"file_id": "stk", "file_unique_id": "stk", "type": "regular",
                                                        "width": 512, "height": 512, "is_animated": False,
                                                        "is_video": False}}, None, None)
