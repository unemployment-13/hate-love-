"""Normalize WeChat reader records into app-friendly message fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


RAW_TYPE_MAP = {
    0: "text",
    1: "text",
    3: "image",
    34: "voice",
    42: "contact",
    43: "video",
    47: "emoji",
    48: "location",
    49: "file",
    50: "call",
    10000: "system",
}

TYPE_ALIASES = {
    "text": "text",
    "image": "image",
    "voice": "voice",
    "video": "video",
    "emoji": "emoji",
    "file": "file",
    "location": "location",
    "system": "system",
    "call": "system",
}


def normalize_wechat_record(record: Dict[str, Any], index: int) -> Dict[str, Any]:
    is_self = bool(record.get("is_self") or record.get("isSend") == 1 or record.get("is_send") == 1)
    sender = _first_text(
        record,
        ("sender", "sender_name", "display_name", "accountName", "senderUsername", "from"),
    )
    if not sender:
        sender = "我" if is_self else "对方"

    raw_type = record.get("raw_type", record.get("localType", record.get("type")))
    message_type = normalize_message_type(raw_type)
    content = _first_text(record, ("content", "text", "parsedContent", "message", "body"))
    if not content:
        content = placeholder_for_type(message_type)

    return {
        "id": f"m_{index:06d}",
        "sender": sender,
        "sender_role": "self" if is_self else "other",
        "timestamp": normalize_timestamp(record.get("timestamp", record.get("createTime"))),
        "text": content,
        "message_type": message_type,
        "source": "wechat_local",
        "confidence": 0.96,
        "needs_review": False,
        "raw": {
            "platform": "wechat",
            "session_id": record.get("session_id") or record.get("talker") or record.get("sessionId"),
            "wechat_message_id": record.get("id") or record.get("localId") or record.get("serverId"),
            "raw_type": raw_type,
            "is_self": is_self,
            "sender_wxid": record.get("sender_wxid") or record.get("senderUsername"),
        },
    }


def normalize_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return _timestamp_to_iso(float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and number > 0:
        return _timestamp_to_iso(number)
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).isoformat(timespec="seconds")
    except ValueError:
        return None


def normalize_message_type(raw_type: Any) -> str:
    if isinstance(raw_type, str):
        lowered = raw_type.strip().lower()
        if lowered.isdigit():
            return RAW_TYPE_MAP.get(int(lowered), "unknown")
        return TYPE_ALIASES.get(lowered, "unknown")
    if isinstance(raw_type, (int, float)):
        return RAW_TYPE_MAP.get(int(raw_type), "unknown")
    return "text"


def placeholder_for_type(message_type: str) -> str:
    return {
        "image": "[图片]",
        "voice": "[语音消息]",
        "video": "[视频]",
        "emoji": "[表情]",
        "file": "[文件]",
        "location": "[位置]",
        "system": "[系统消息]",
        "unknown": "[未知消息]",
    }.get(message_type, "")


def _timestamp_to_iso(value: float) -> Optional[str]:
    if value > 10_000_000_000:
        value = value / 1000
    try:
        return datetime.fromtimestamp(value).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def _first_text(record: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""

