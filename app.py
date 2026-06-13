#!/usr/bin/env python3
"""Local chat relationship analyzer.

V1 keeps the deployment model from V0: Python standard library only for the
web server and analysis pipeline. Screenshot OCR is optional and uses the
bundled macOS Vision helper when available.
"""

from __future__ import annotations

import cgi
import csv
import html as html_lib
import io
import json
import mimetypes
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from wechat import collector as wechat_collector
from wechat.detector import detect_wechat, public_detection_payload
from wechat.normalizer import normalize_wechat_record

ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
SCRIPTS_DIR = ROOT_DIR / "scripts"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
NEW_CONVERSATION_GAP = timedelta(hours=6)
REPLY_INTERVAL_CAP = timedelta(days=7)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CURRENT_YEAR = 2026

CONVERSATIONS: Dict[str, Dict[str, Any]] = {}
WECHAT_ACCOUNTS: Dict[str, Dict[str, Any]] = {}


@dataclass
class Message:
    id: str
    sender: str
    sender_role: str
    timestamp: Optional[str]
    text: str
    message_type: str
    source: str
    confidence: float = 1.0
    needs_review: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportResult:
    messages: List[Message]
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    source_type: str = "unknown"
    confidence: float = 1.0
    ocr_blocks: List[Dict[str, Any]] = field(default_factory=list)


TXT_PATTERNS = [
    re.compile(
        r"^\[(?P<ts>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\]\s*"
        r"(?P<sender>[^:：]+)[:：]\s*(?P<text>.*)$"
    ),
    re.compile(
        r"^(?P<ts>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s+"
        r"(?P<sender>[^:：]+)[:：]\s*(?P<text>.*)$"
    ),
    re.compile(r"^(?P<sender>[^:：\n]{1,80})[:：]\s*(?P<text>.*)$"),
]

TIME_ANCHOR_PATTERN = re.compile(
    r"(?:(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*)?"
    r"(?:(?P<period>上午|下午|晚上|凌晨|中午)\s*)?"
    r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})"
)

TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
]

TIME_FIELDS = ("timestamp", "time", "date", "datetime", "created_at", "send_time")
SENDER_FIELDS = ("sender", "name", "from", "speaker", "user", "nickname")
TEXT_FIELDS = ("text", "message", "content", "body", "msg", "msg_text")
SYSTEM_MARKERS = (
    "撤回了一条消息",
    "你撤回了一条消息",
    "对方撤回了一条消息",
    "加入了群聊",
    "退出了群聊",
    "已开启",
    "系统消息",
    "this message was deleted",
    "joined the group",
    "left the group",
    "messages and calls are end-to-end encrypted",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff"}
MESSAGE_TYPES = {
    "text",
    "voice",
    "image",
    "video",
    "emoji",
    "file",
    "location",
    "system",
    "unknown",
    "empty",
}


def decode_upload(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return text.strip()


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value)
    text = normalize_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}", text):
        try:
            numeric = float(text)
            return datetime.fromtimestamp(numeric / 1000 if numeric > 10_000_000_000 else numeric)
        except (OSError, OverflowError, ValueError):
            return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return parse_chinese_time_anchor(text)


def parse_chinese_time_anchor(value: str, base: Optional[datetime] = None) -> Optional[datetime]:
    text = normalize_text(value)
    match = TIME_ANCHOR_PATTERN.search(text)
    if not match:
        return None

    base_dt = base or datetime(CURRENT_YEAR, 1, 1)
    month = int(match.group("month")) if match.group("month") else base_dt.month
    day = int(match.group("day")) if match.group("day") else base_dt.day
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    period = match.group("period") or ""
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if period == "中午" and hour < 11:
        hour += 12
    try:
        return datetime(base_dt.year, month, day, hour, minute)
    except ValueError:
        return None


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def is_system_message(sender: str, text: str) -> bool:
    value = f"{sender} {text}".strip().lower()
    return any(marker in value for marker in SYSTEM_MARKERS)


def infer_message_type(text: str) -> str:
    compact = normalize_text(text)
    if re.fullmatch(r"\[?语音\s*\d+\s*秒\]?", compact) or re.fullmatch(r"\d+[\"”]\s*[（(]?", compact):
        return "voice"
    if compact in {"[图片]", "[图片/表情]", "[表情]", "[动画表情]"}:
        return "image"
    if compact in {"[视频]"}:
        return "video"
    if compact in {"[文件]"}:
        return "file"
    if compact in {"[位置]"}:
        return "location"
    if compact in {"[系统消息]"}:
        return "system"
    return "text"


def make_message(
    index: int,
    sender: str,
    text: str,
    source: str,
    timestamp: Optional[datetime] = None,
    confidence: float = 1.0,
    needs_review: bool = False,
    raw: Optional[Dict[str, Any]] = None,
) -> Message:
    return Message(
        id=f"m_{index:06d}",
        sender=sender,
        sender_role="unknown",
        timestamp=to_iso(timestamp),
        text=text,
        message_type=infer_message_type(text),
        source=source,
        confidence=round(float(confidence), 3),
        needs_review=needs_review,
        raw=raw or {},
    )


def reindex_messages(messages: List[Message]) -> None:
    for index, message in enumerate(messages, start=1):
        if not message.id:
            message.id = f"m_{index:06d}"


def parse_txt(content: str, source: str = "txt") -> Tuple[List[Message], List[Dict[str, Any]]]:
    messages: List[Message] = []
    errors: List[Dict[str, Any]] = []

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        raw = raw_line.strip()
        if not raw:
            continue

        match = None
        for pattern in TXT_PATTERNS:
            match = pattern.match(raw)
            if match:
                break

        if not match:
            errors.append(
                {"line": line_number, "raw": raw_line, "reason": "无法识别这一行的聊天格式"}
            )
            continue

        sender = normalize_text(match.group("sender"))
        text = normalize_text(match.group("text"))
        timestamp = parse_datetime(match.groupdict().get("ts"))

        if not sender:
            errors.append({"line": line_number, "raw": raw_line, "reason": "缺少发送者"})
            continue
        if not text:
            continue
        if is_system_message(sender, text):
            continue

        messages.append(
            make_message(
                len(messages) + 1,
                sender,
                text,
                source,
                timestamp,
                confidence=0.9 if timestamp else 0.72,
                needs_review=timestamp is None,
                raw={"line": line_number, "text": raw_line},
            )
        )

    return messages, errors


def sniff_csv_dialect(content: str) -> csv.Dialect:
    sample = content[:2048]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def find_field(fieldnames: Iterable[str], aliases: Iterable[str]) -> Optional[str]:
    normalized = {name.strip().lower().lstrip("\ufeff"): name for name in fieldnames if name}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def parse_csv_content(
    content: str,
    mapping: Optional[Dict[str, str]] = None,
    source: str = "csv_export",
) -> Tuple[List[Message], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    messages: List[Message] = []
    stream = io.StringIO(content)

    try:
        reader = csv.DictReader(stream, dialect=sniff_csv_dialect(content))
    except csv.Error as exc:
        return [], [{"line": 1, "raw": "", "reason": f"CSV 解析失败：{exc}"}]

    if not reader.fieldnames:
        return [], [{"line": 1, "raw": "", "reason": "CSV 缺少表头"}]

    mapping = mapping or {}
    sender_field = mapping.get("sender_field") or find_field(reader.fieldnames, SENDER_FIELDS)
    text_field = mapping.get("text_field") or find_field(reader.fieldnames, TEXT_FIELDS)
    time_field = mapping.get("time_field") or find_field(reader.fieldnames, TIME_FIELDS)

    if sender_field not in reader.fieldnames:
        sender_field = None
    if text_field not in reader.fieldnames:
        text_field = None
    if time_field and time_field not in reader.fieldnames:
        time_field = None

    if not sender_field or not text_field:
        missing = []
        if not sender_field:
            missing.append("sender/name/from 或手动 sender_field")
        if not text_field:
            missing.append("text/message/content 或手动 text_field")
        return [], [{"line": 1, "raw": ",".join(reader.fieldnames), "reason": f"CSV 缺少必要字段：{', '.join(missing)}"}]

    for row_number, row in enumerate(reader, start=2):
        sender = normalize_text(row.get(sender_field))
        text = normalize_text(row.get(text_field))
        timestamp = parse_datetime(row.get(time_field)) if time_field else None

        if not sender:
            errors.append({"line": row_number, "raw": dict(row), "reason": "缺少发送者"})
            continue
        if not text:
            continue
        if is_system_message(sender, text):
            continue

        messages.append(
            make_message(
                len(messages) + 1,
                sender,
                text,
                source,
                timestamp,
                confidence=0.92 if timestamp else 0.78,
                needs_review=timestamp is None,
                raw={"line": row_number, "row": dict(row)},
            )
        )

    return messages, errors


def first_value(data: Dict[str, Any], aliases: Iterable[str]) -> Any:
    normalized = {str(key).lower(): key for key in data.keys()}
    for alias in aliases:
        key = normalized.get(alias)
        if key is not None:
            return data.get(key)
    return None


def parse_json_content(content: str) -> Tuple[List[Message], List[Dict[str, Any]]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return [], [{"line": 1, "raw": "", "reason": f"JSON 解析失败：{exc}"}]

    if isinstance(payload, dict):
        records = payload.get("messages") or payload.get("data") or payload.get("items")
    else:
        records = payload
    if not isinstance(records, list):
        return [], [{"line": 1, "raw": "", "reason": "JSON 需要是消息数组，或包含 messages/data/items 数组"}]

    messages: List[Message] = []
    errors: List[Dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            errors.append({"line": index, "raw": item, "reason": "消息项不是对象"})
            continue
        sender = normalize_text(first_value(item, SENDER_FIELDS))
        text = normalize_text(first_value(item, TEXT_FIELDS))
        timestamp = parse_datetime(first_value(item, TIME_FIELDS))
        if not sender:
            errors.append({"line": index, "raw": item, "reason": "缺少发送者"})
            continue
        if not text:
            continue
        messages.append(
            make_message(
                len(messages) + 1,
                sender,
                text,
                "json_export",
                timestamp,
                confidence=0.9 if timestamp else 0.78,
                needs_review=timestamp is None,
                raw={"item": item},
            )
        )
    return messages, errors


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        text = normalize_text(data)
        if text:
            self.parts.append(text)


def parse_html_content(content: str) -> Tuple[List[Message], List[Dict[str, Any]]]:
    parser = TextExtractor()
    parser.feed(content)
    text = "\n".join(parser.parts)
    messages, errors = parse_txt(text, source="html_export")
    if messages:
        return messages, errors
    return [], errors + [{"line": 1, "raw": "", "reason": "HTML 文本已提取，但未识别出标准聊天行"}]


def parse_upload(
    filename: str,
    raw: bytes,
    mapping: Optional[Dict[str, str]] = None,
) -> Tuple[List[Message], List[Dict[str, Any]]]:
    result = import_file(filename, raw, mapping)
    return result.messages, result.errors


def import_file(
    filename: str,
    raw: bytes,
    mapping: Optional[Dict[str, str]] = None,
) -> ImportResult:
    suffix = Path(filename or "").suffix.lower()
    content = decode_upload(raw)
    if suffix == ".csv":
        messages, errors = parse_csv_content(content, mapping=mapping)
        return ImportResult(messages, errors, source_type="csv_export", confidence=0.9)
    if suffix == ".txt":
        messages, errors = parse_txt(content, source="txt_export")
        return ImportResult(messages, errors, source_type="txt_export", confidence=0.82)
    if suffix == ".json":
        messages, errors = parse_json_content(content)
        return ImportResult(messages, errors, source_type="json_export", confidence=0.88)
    if suffix in {".html", ".htm"}:
        messages, errors = parse_html_content(content)
        return ImportResult(messages, errors, source_type="html_export", confidence=0.72)
    return ImportResult([], [{"line": 0, "raw": filename, "reason": "支持 .txt/.csv/.json/.html 和截图图片"}], source_type="unsupported", confidence=0)


def run_vision_ocr(image_paths: List[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    swift = shutil.which("swift")
    script = SCRIPTS_DIR / "vision_ocr.swift"
    if not swift or not script.exists():
        return [], [{"line": 0, "raw": "", "reason": "本机未找到 Swift 或 Vision OCR 脚本，无法本地识别截图"}]

    command = [swift, str(script)] + [str(path) for path in image_paths]
    env = os.environ.copy()
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/private/tmp/chat_analyzer_clang_cache")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], [{"line": 0, "raw": "", "reason": f"OCR 调用失败：{exc}"}]

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return [], [{"line": 0, "raw": "", "reason": f"OCR 识别失败：{detail}"}]

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return [], [{"line": 0, "raw": completed.stdout[:300], "reason": f"OCR 输出不是有效 JSON：{exc}"}]
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return [], [{"line": 0, "raw": payload, "reason": "OCR 输出缺少 blocks 数组"}]
    return blocks, []


def block_side(block: Dict[str, Any]) -> str:
    x = float(block.get("x", 0))
    width = float(block.get("width", 0))
    center = x + width / 2
    if center >= 0.58:
        return "right"
    if center <= 0.48:
        return "left"
    return "center"


def is_time_anchor_text(text: str) -> bool:
    compact = normalize_text(text)
    return bool(TIME_ANCHOR_PATTERN.search(compact)) and len(compact) <= 24


def normalize_ocr_text(text: str) -> str:
    text = normalize_text(text)
    text = text.replace("秒", "秒")
    if re.fullmatch(r"\d+\s*[\"”]", text):
        seconds = re.sub(r"\D", "", text)
        return f"[语音 {seconds}秒]"
    return text


def import_screenshots(files: List[Tuple[str, bytes]]) -> ImportResult:
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    image_paths: List[Path] = []
    temp_dir_obj = tempfile.TemporaryDirectory(prefix="chat_ocr_", dir="/private/tmp")
    temp_dir = Path(temp_dir_obj.name)

    try:
        for index, (filename, raw) in enumerate(files, start=1):
            suffix = Path(filename).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                warnings.append({"line": index, "raw": filename, "reason": "不是支持的截图格式，已跳过"})
                continue
            path = temp_dir / f"{index:03d}{suffix}"
            path.write_bytes(raw)
            image_paths.append(path)

        if not image_paths:
            return ImportResult([], errors + warnings, source_type="screenshot_ocr", confidence=0)

        blocks, ocr_errors = run_vision_ocr(image_paths)
        if ocr_errors:
            return ImportResult([], errors + ocr_errors + warnings, source_type="screenshot_ocr", confidence=0)

        blocks.sort(key=lambda item: (int(item.get("image_index", 0)), -float(item.get("y", 0))))
        messages: List[Message] = []
        current_time: Optional[datetime] = None
        previous_key: Optional[Tuple[str, str, Optional[str]]] = None

        for block_index, block in enumerate(blocks, start=1):
            text = normalize_ocr_text(block.get("text", ""))
            if not text:
                continue
            side = block_side(block)
            time_anchor = parse_chinese_time_anchor(text, current_time)
            if side == "center" and is_time_anchor_text(text) and time_anchor:
                current_time = time_anchor
                continue
            if side == "center":
                warnings.append({"line": block_index, "raw": text, "reason": "OCR 文本位于中间区域，已标记为待确认"})

            sender = "我" if side == "right" else "对方"
            confidence = float(block.get("confidence", 0.65) or 0.65)
            needs_review = side == "center" or confidence < 0.7
            key = (sender, text, to_iso(current_time))
            if key == previous_key:
                continue
            previous_key = key
            messages.append(
                make_message(
                    len(messages) + 1,
                    sender,
                    text,
                    "screenshot_ocr",
                    current_time,
                    confidence=max(0.45, min(confidence, 0.88)),
                    needs_review=needs_review,
                    raw={
                        "image": block.get("image"),
                        "image_index": block.get("image_index"),
                        "bbox": [block.get("x"), block.get("y"), block.get("width"), block.get("height")],
                        "side": side,
                        "ocr_text": block.get("text"),
                    },
                )
            )

        if not messages:
            errors.append({"line": 0, "raw": "", "reason": "OCR 完成但未识别出聊天气泡文本"})
        return ImportResult(
            messages,
            errors,
            warnings,
            source_type="screenshot_ocr",
            confidence=0.7,
            ocr_blocks=blocks,
        )
    finally:
        temp_dir_obj.cleanup()


def import_wechat_records(records: List[Dict[str, Any]]) -> ImportResult:
    messages: List[Message] = []
    errors: List[Dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append({"line": index, "raw": record, "reason": "微信消息不是对象"})
            continue
        normalized = normalize_wechat_record(record, len(messages) + 1)
        text = normalize_text(normalized["text"])
        if not text:
            continue
        message_type = normalized["message_type"]
        if message_type not in MESSAGE_TYPES:
            message_type = "unknown"
        messages.append(
            Message(
                id=normalized["id"],
                sender=normalize_text(normalized["sender"]) or "未知发送者",
                sender_role=normalized["sender_role"],
                timestamp=normalized["timestamp"],
                text=text,
                message_type=message_type,
                source="wechat_local",
                confidence=float(normalized["confidence"]),
                needs_review=bool(normalized["needs_review"]),
                raw=normalized["raw"],
            )
        )
    return ImportResult(
        messages,
        errors,
        source_type="wechat_local",
        confidence=0.96 if messages else 0,
    )


def assign_self_sender(messages: List[Message], self_sender: str) -> None:
    for message in messages:
        if message.sender == self_sender:
            message.sender_role = "self"
        elif message.sender:
            message.sender_role = "other"
        else:
            message.sender_role = "unknown"


def unique_senders(messages: List[Message]) -> List[str]:
    senders: List[str] = []
    for message in messages:
        if message.sender not in senders:
            senders.append(message.sender)
    return senders


def message_to_dict(message: Message) -> Dict[str, Any]:
    return asdict(message)


def parse_message_time(message: Message) -> Optional[datetime]:
    if not message.timestamp:
        return None
    try:
        return datetime.fromisoformat(message.timestamp)
    except ValueError:
        return None


def role_label(message: Message) -> str:
    return message.sender_role if message.sender_role in {"self", "other"} else "unknown"


def sorted_timed_messages(messages: List[Message]) -> List[Tuple[datetime, Message]]:
    timed_messages = [
        (parse_message_time(message), message)
        for message in messages
        if parse_message_time(message) is not None
    ]
    timed_messages.sort(key=lambda item: item[0])
    return timed_messages


def compute_metrics(messages: List[Message]) -> Dict[str, Any]:
    total_messages = len(messages)
    role_counts = {"self": 0, "other": 0, "unknown": 0}
    role_chars = {"self": 0, "other": 0, "unknown": 0}
    role_questions = {"self": 0, "other": 0, "unknown": 0}
    sender_counts: Dict[str, int] = {}
    sender_chars: Dict[str, int] = {}

    for message in messages:
        role = role_label(message)
        length = len(message.text)
        role_counts[role] += 1
        role_chars[role] += length
        if is_question(message.text):
            role_questions[role] += 1
        sender_counts[message.sender] = sender_counts.get(message.sender, 0) + 1
        sender_chars[message.sender] = sender_chars.get(message.sender, 0) + length

    role_avg_lengths = {
        role: round(role_chars[role] / role_counts[role], 2) if role_counts[role] else 0
        for role in role_counts
    }
    sender_summaries = [
        {
            "sender": sender,
            "message_count": sender_counts[sender],
            "char_count": sender_chars[sender],
            "avg_length": round(sender_chars[sender] / sender_counts[sender], 2),
        }
        for sender in sorted(sender_counts)
    ]

    timed_messages = sorted_timed_messages(messages)
    daily_map: Dict[str, Dict[str, Any]] = {}
    for timestamp, message in timed_messages:
        date_key = timestamp.date().isoformat()
        day = daily_map.setdefault(
            date_key, {"date": date_key, "total": 0, "self": 0, "other": 0, "unknown": 0}
        )
        role = role_label(message)
        day["total"] += 1
        day[role] += 1

    conversation_starts = 0
    starts_by_role = {"self": 0, "other": 0, "unknown": 0}
    previous_time: Optional[datetime] = None
    for timestamp, message in timed_messages:
        if previous_time is None or timestamp - previous_time > NEW_CONVERSATION_GAP:
            conversation_starts += 1
            starts_by_role[role_label(message)] += 1
        previous_time = timestamp

    other_start_ratio = (
        round(starts_by_role["other"] / conversation_starts, 4) if conversation_starts else None
    )

    reply_intervals_seconds: List[float] = []
    included_reply_intervals_seconds: List[float] = []
    reply_interval_points: List[Dict[str, Any]] = []
    long_reply_interval_count = 0
    previous_pair: Optional[Tuple[datetime, Message]] = None

    for timestamp, message in timed_messages:
        if previous_pair:
            previous_timestamp, previous_message = previous_pair
            seconds = (timestamp - previous_timestamp).total_seconds()
            if seconds >= 0 and message.sender != previous_message.sender:
                excluded = seconds > REPLY_INTERVAL_CAP.total_seconds()
                reply_intervals_seconds.append(seconds)
                if excluded:
                    long_reply_interval_count += 1
                else:
                    included_reply_intervals_seconds.append(seconds)
                reply_interval_points.append(
                    {
                        "timestamp": timestamp.isoformat(timespec="seconds"),
                        "date": timestamp.date().isoformat(),
                        "hours": round(seconds / 3600, 2),
                        "from_sender": previous_message.sender,
                        "to_sender": message.sender,
                        "to_role": role_label(message),
                        "excluded_from_average": excluded,
                    }
                )
        previous_pair = (timestamp, message)

    avg_reply_seconds = (
        statistics.fmean(included_reply_intervals_seconds)
        if included_reply_intervals_seconds
        else None
    )
    median_reply_seconds = (
        statistics.median(included_reply_intervals_seconds)
        if included_reply_intervals_seconds
        else None
    )

    return {
        "total_messages": total_messages,
        "role_counts": role_counts,
        "role_char_counts": role_chars,
        "role_avg_lengths": role_avg_lengths,
        "role_questions": role_questions,
        "sender_summaries": sender_summaries,
        "daily_message_counts": [daily_map[key] for key in sorted(daily_map)],
        "conversation_starts": conversation_starts,
        "starts_by_role": starts_by_role,
        "other_start_ratio": other_start_ratio,
        "reply_intervals": reply_interval_points,
        "reply_interval_count": len(reply_intervals_seconds),
        "long_reply_interval_count": long_reply_interval_count,
        "avg_reply_interval_seconds": round(avg_reply_seconds, 2) if avg_reply_seconds is not None else None,
        "median_reply_interval_seconds": round(median_reply_seconds, 2) if median_reply_seconds is not None else None,
        "untimed_message_count": total_messages - len(timed_messages),
        "needs_review_count": sum(1 for message in messages if message.needs_review),
    }


def is_question(text: str) -> bool:
    return any(marker in text for marker in ("?", "？", "吗", "么", "呢", "怎么", "为什么", "比如"))


PROGRESS_MARKERS = ("一起", "见面", "吃饭", "周末", "有空", "约", "过来", "回去", "上车", "等会")
CARE_MARKERS = ("辛苦", "记得", "早点", "怎么啦", "好点", "别太晚", "累", "抱走", "喜欢")
RISK_MARKERS = ("嗯", "哦", "对", "哈哈", "行", "好吧", "算了", "随便")


def clamp_score(value: float) -> int:
    return int(max(0, min(100, round(value))))


def role_messages(messages: List[Message], role: str) -> List[Message]:
    return [message for message in messages if role_label(message) == role]


def find_evidence(messages: List[Message], predicates: Iterable[Any], limit: int = 3) -> List[str]:
    evidence: List[str] = []
    for message in messages:
        if any(predicate(message) for predicate in predicates):
            evidence.append(message.id)
        if len(evidence) >= limit:
            break
    return evidence


def generate_report(messages: List[Message]) -> Dict[str, Any]:
    metrics = compute_metrics(messages)
    total = max(metrics["total_messages"], 1)
    self_count = metrics["role_counts"]["self"]
    other_count = metrics["role_counts"]["other"]
    other_messages = role_messages(messages, "other")
    self_messages = role_messages(messages, "self")

    reciprocity = 100 - abs(self_count - other_count) / total * 100
    other_share = other_count / total
    other_avg = metrics["role_avg_lengths"]["other"]
    self_avg = metrics["role_avg_lengths"]["self"]
    avg_balance = 100 - min(60, abs(other_avg - self_avg) * 2)
    question_balance = 100 - min(80, abs(metrics["role_questions"]["self"] - metrics["role_questions"]["other"]) * 12)

    progress_hits = [message for message in messages if any(marker in message.text for marker in PROGRESS_MARKERS)]
    care_hits = [message for message in other_messages if any(marker in message.text for marker in CARE_MARKERS)]
    short_other = [message for message in other_messages if len(message.text) <= 2 or message.text in RISK_MARKERS]

    interaction_heat = clamp_score(35 + min(total, 160) * 0.25 + metrics["conversation_starts"] * 4)
    response_investment = clamp_score(35 + other_share * 35 + min(other_avg, 50) * 0.8 + len(care_hits) * 3)
    reciprocity_score = clamp_score((reciprocity * 0.65) + (avg_balance * 0.2) + (question_balance * 0.15))
    relationship_progress = clamp_score(20 + min(len(progress_hits), 12) * 6)
    short_ratio = len(short_other) / max(len(other_messages), 1)
    risk = clamp_score(short_ratio * 55 + metrics["long_reply_interval_count"] * 8 + max(0, self_count - other_count) / total * 35)

    trend = "stable"
    ordered = [message for message in messages if role_label(message) in {"self", "other"}]
    if len(ordered) >= 8:
        midpoint = len(ordered) // 2
        first_other = sum(1 for message in ordered[:midpoint] if role_label(message) == "other")
        second_other = sum(1 for message in ordered[midpoint:] if role_label(message) == "other")
        first_total = max(midpoint, 1)
        second_total = max(len(ordered) - midpoint, 1)
        first_ratio = first_other / first_total
        second_ratio = second_other / second_total
        if second_ratio > first_ratio + 0.12 and risk < 55:
            trend = "warming"
        elif second_ratio < first_ratio - 0.12 or risk >= 65:
            trend = "cooling"
        elif self_count > other_count * 1.7:
            trend = "one_sided"
    if metrics["total_messages"] < 12:
        trend = "insufficient_data"

    claims = [
        {
            "title": "对方回应投入",
            "summary": "根据对方消息数量、平均长度、关心/解释类表达估算回应投入。",
            "evidence": find_evidence(other_messages, [lambda m: len(m.text) >= 18, lambda m: any(x in m.text for x in CARE_MARKERS)]),
            "counter_evidence": find_evidence(other_messages, [lambda m: len(m.text) <= 2], limit=2),
            "confidence": "medium" if len(other_messages) >= 5 else "low",
        },
        {
            "title": "互动对等性",
            "summary": "根据双方消息数、字数和提问比例估算互动是否单方面倾斜。",
            "evidence": find_evidence(messages, [lambda m: is_question(m.text), lambda m: len(m.text) >= 20]),
            "counter_evidence": find_evidence(self_messages, [lambda m: len(m.text) >= 25], limit=2) if self_count > other_count * 1.3 else [],
            "confidence": "medium" if total >= 20 else "low",
        },
        {
            "title": "关系推进信号",
            "summary": "识别聊天中的见面、行程、共同安排、未来计划等推进线索。",
            "evidence": [message.id for message in progress_hits[:3]],
            "counter_evidence": find_evidence(messages, [lambda m: "不" in m.text and any(x in m.text for x in ("去", "行", "想"))], limit=2),
            "confidence": "medium" if progress_hits else "low",
        },
        {
            "title": "风险信号",
            "summary": "识别短回复、长期不回应、明显单方输出等可能降低互动质量的信号。",
            "evidence": [message.id for message in short_other[:3]],
            "counter_evidence": find_evidence(other_messages, [lambda m: len(m.text) >= 18], limit=2),
            "confidence": "medium" if total >= 20 else "low",
        },
    ]

    confidence = "high" if total >= 80 and metrics["untimed_message_count"] / total < 0.25 else "medium"
    if total < 25 or metrics["needs_review_count"] / total > 0.25:
        confidence = "low"

    return {
        "trend": trend,
        "confidence": confidence,
        "scores": {
            "interaction_heat": interaction_heat,
            "response_investment": response_investment,
            "reciprocity": reciprocity_score,
            "relationship_progress": relationship_progress,
            "risk": risk,
        },
        "claims": claims,
        "metrics": metrics,
        "disclaimer": "本报告分析聊天中的互动信号，不等同于对方真实心理或关系承诺。",
    }


def export_txt(messages: List[Message]) -> str:
    lines = []
    for message in messages:
        if message.timestamp:
            lines.append(f"[{message.timestamp.replace('T', ' ')}] {message.sender}: {message.text}")
        else:
            lines.append(f"{message.sender}: {message.text}")
    return "\n".join(lines) + "\n"


def export_csv(messages: List[Message]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["id", "timestamp", "sender", "sender_role", "message_type", "text", "source", "confidence", "needs_review"])
    for message in messages:
        writer.writerow([
            message.id,
            message.timestamp or "",
            message.sender,
            message.sender_role,
            message.message_type,
            message.text,
            message.source,
            message.confidence,
            message.needs_review,
        ])
    return stream.getvalue()


def trend_label(value: str) -> str:
    return {
        "warming": "升温",
        "stable": "稳定",
        "cooling": "降温",
        "one_sided": "单方投入",
        "insufficient_data": "数据不足",
    }.get(value, value)


def export_html_report(messages: List[Message], report: Dict[str, Any]) -> str:
    evidence_map = {message.id: message for message in messages}
    score_items = "".join(
        f"<li><strong>{html_lib.escape(key)}</strong>: {value}</li>"
        for key, value in report["scores"].items()
    )
    claim_items = []
    for claim in report["claims"]:
        evidence_html = "".join(
            f"<li>{html_lib.escape(evidence_map[msg_id].sender)}: {html_lib.escape(evidence_map[msg_id].text)}</li>"
            for msg_id in claim["evidence"]
            if msg_id in evidence_map
        )
        counter_html = "".join(
            f"<li>{html_lib.escape(evidence_map[msg_id].sender)}: {html_lib.escape(evidence_map[msg_id].text)}</li>"
            for msg_id in claim["counter_evidence"]
            if msg_id in evidence_map
        )
        claim_items.append(
            f"<section><h3>{html_lib.escape(claim['title'])}</h3>"
            f"<p>{html_lib.escape(claim['summary'])}</p>"
            f"<h4>支持证据</h4><ul>{evidence_html or '<li>暂无</li>'}</ul>"
            f"<h4>反向证据</h4><ul>{counter_html or '<li>暂无</li>'}</ul></section>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>关系互动分析报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif; margin: 40px; line-height: 1.6; color: #18201f; }}
    h1, h2, h3 {{ color: #0f766e; }}
    section {{ border-top: 1px solid #dce2de; padding: 16px 0; }}
  </style>
</head>
<body>
  <h1>关系互动分析报告</h1>
  <p>{html_lib.escape(report['disclaimer'])}</p>
  <h2>总览</h2>
  <p>趋势：<strong>{trend_label(report['trend'])}</strong>；置信度：{html_lib.escape(report['confidence'])}</p>
  <h2>分数</h2>
  <ul>{score_items}</ul>
  <h2>证据分析</h2>
  {''.join(claim_items)}
</body>
</html>"""


def json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def make_conversation(result: ImportResult, filename: str = "") -> Dict[str, Any]:
    conversation_id = uuid.uuid4().hex
    CONVERSATIONS[conversation_id] = {
        "filename": filename,
        "messages": result.messages,
        "errors": result.errors,
        "warnings": result.warnings,
        "source_type": result.source_type,
        "confidence": result.confidence,
        "ocr_blocks": result.ocr_blocks,
    }
    return conversation_payload(conversation_id)


def conversation_payload(conversation_id: str) -> Dict[str, Any]:
    conversation = CONVERSATIONS[conversation_id]
    messages: List[Message] = conversation["messages"]
    return {
        "conversation_id": conversation_id,
        "filename": conversation.get("filename", ""),
        "source_type": conversation.get("source_type", "unknown"),
        "confidence": conversation.get("confidence", 1.0),
        "senders": unique_senders(messages),
        "message_count": len(messages),
        "needs_review_count": sum(1 for message in messages if message.needs_review),
        "errors": conversation.get("errors", []),
        "warnings": conversation.get("warnings", []),
        "ocr_blocks": conversation.get("ocr_blocks", []),
        "messages": [message_to_dict(message) for message in messages],
    }


def register_wechat_accounts(accounts: List[Dict[str, Any]]) -> None:
    for account in accounts:
        account_id = account.get("account_id")
        path = account.get("path")
        if account_id and path:
            WECHAT_ACCOUNTS[account_id] = account


def resolve_wechat_account(account_id: str) -> Optional[Dict[str, Any]]:
    return WECHAT_ACCOUNTS.get(account_id)


class ChatAnalyzerHandler(BaseHTTPRequestHandler):
    server_version = "ChatAnalyzerV2/0.3"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body: str, content_type: str, filename: Optional[str] = None) -> None:
        encoded = body.encode("utf-8-sig" if content_type.startswith("text/csv") else "utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, status: int, code: str, message: str) -> None:
        self.send_json({"error": {"code": code, "message": message}}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self.serve_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            self.serve_file(STATIC_DIR / relative)
            return
        if path == "/api/wechat/detect":
            self.handle_wechat_detect()
            return
        if path.startswith("/api/conversations/"):
            self.handle_conversation_get(path)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未找到请求的资源")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in {"/api/import", "/api/import/files"}:
            self.handle_file_import()
            return
        if path == "/api/import/screenshots":
            self.handle_screenshot_import()
            return
        if path == "/api/wechat/accounts":
            self.handle_wechat_accounts()
            return
        if path == "/api/wechat/sessions":
            self.handle_wechat_sessions()
            return
        if path == "/api/wechat/import":
            self.handle_wechat_import()
            return
        if path.startswith("/api/conversations/") and path.endswith("/role"):
            self.handle_role_update(path)
            return
        if path.startswith("/api/conversations/") and path.endswith("/messages/bulk-update"):
            self.handle_messages_bulk_update(path)
            return
        if path.startswith("/api/conversations/") and path.endswith("/messages/delete"):
            self.handle_messages_delete(path)
            return
        if path.startswith("/api/conversations/") and path.endswith("/messages/merge"):
            self.handle_messages_merge(path)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未找到请求的资源")

    def serve_file(self, file_path: Path) -> None:
        try:
            resolved = file_path.resolve()
            if not str(resolved).startswith(str(STATIC_DIR.resolve())):
                self.send_error_json(HTTPStatus.FORBIDDEN, "forbidden", "禁止访问该路径")
                return
            if not resolved.exists() or not resolved.is_file():
                self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未找到静态文件")
                return
            body = resolved.read_bytes()
            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            if content_type.startswith("text/") or resolved.suffix in {".js", ".css"}:
                content_type += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "file_error", str(exc))

    def get_conversation_from_path(self, path: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], List[str]]:
        parts = path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "conversations":
            return None, None, parts
        conversation_id = parts[2]
        conversation = CONVERSATIONS.get(conversation_id)
        return conversation_id, conversation, parts

    def handle_conversation_get(self, path: str) -> None:
        conversation_id, conversation, parts = self.get_conversation_from_path(path)
        if not conversation_id or not conversation:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return

        action = "/".join(parts[3:])
        messages: List[Message] = conversation["messages"]
        if action == "messages":
            self.send_json(conversation_payload(conversation_id))
            return
        if action == "metrics":
            self.send_json({"conversation_id": conversation_id, "metrics": compute_metrics(messages)})
            return
        if action == "report":
            self.send_json({"conversation_id": conversation_id, "report": generate_report(messages)})
            return
        if action == "export.txt":
            self.send_text(export_txt(messages), "text/plain; charset=utf-8", "chat-standard.txt")
            return
        if action == "export.csv":
            self.send_text(export_csv(messages), "text/csv; charset=utf-8", "chat-standard.csv")
            return
        if action == "export.html":
            self.send_text(export_html_report(messages, generate_report(messages)), "text/html; charset=utf-8", "relationship-report.html")
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未知的会话操作")

    def read_multipart(self) -> Optional[cgi.FieldStorage]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "empty_upload", "没有收到上传内容")
            return None
        if content_length > MAX_UPLOAD_BYTES:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "file_too_large", "上传总量不能超过 50MB")
            return None
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_content_type", "请使用 multipart/form-data 上传")
            return None
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )

    def get_form_files(self, form: cgi.FieldStorage) -> List[cgi.FieldStorage]:
        files: List[cgi.FieldStorage] = []
        for key in ("files", "file", "screenshots"):
            if key not in form:
                continue
            item = form[key]
            if isinstance(item, list):
                files.extend([entry for entry in item if getattr(entry, "filename", "")])
            elif getattr(item, "filename", ""):
                files.append(item)
        return files

    def get_form_value(self, form: cgi.FieldStorage, key: str) -> str:
        if key not in form:
            return ""
        item = form[key]
        if isinstance(item, list):
            item = item[0]
        return normalize_text(getattr(item, "value", ""))

    def handle_file_import(self) -> None:
        form = self.read_multipart()
        if form is None:
            return
        files = self.get_form_files(form)
        if not files:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing_file", "请选择第三方导出文件")
            return
        file_item = files[0]
        filename = os.path.basename(file_item.filename)
        mapping = {
            "sender_field": self.get_form_value(form, "sender_field"),
            "text_field": self.get_form_value(form, "text_field"),
            "time_field": self.get_form_value(form, "time_field"),
        }
        mapping = {key: value for key, value in mapping.items() if value}
        result = import_file(filename, file_item.file.read(), mapping=mapping)
        if not result.messages:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "no_messages", "没有解析出可用消息，请检查格式或字段映射")
            return
        payload = make_conversation(result, filename)
        self.send_json(payload, HTTPStatus.CREATED)

    def handle_screenshot_import(self) -> None:
        form = self.read_multipart()
        if form is None:
            return
        files = self.get_form_files(form)
        if not files:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing_file", "请选择一张或多张聊天截图")
            return
        upload_files = [(os.path.basename(item.filename), item.file.read()) for item in files]
        result = import_screenshots(upload_files)
        if not result.messages:
            reason = result.errors[0]["reason"] if result.errors else "截图未识别出可用消息"
            self.send_error_json(HTTPStatus.BAD_REQUEST, "no_messages", reason)
            return
        payload = make_conversation(result, "screenshots")
        self.send_json(payload, HTTPStatus.CREATED)

    def handle_wechat_detect(self) -> None:
        detection = detect_wechat()
        register_wechat_accounts(detection.get("accounts", []))
        self.send_json(public_detection_payload(detection))

    def handle_wechat_accounts(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return
        root_path = normalize_text(payload.get("root_path"))
        detection = detect_wechat(root_path or None)
        register_wechat_accounts(detection.get("accounts", []))
        self.send_json(public_detection_payload(detection))

    def handle_wechat_sessions(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return

        account = resolve_wechat_account(normalize_text(payload.get("account_id")))
        if not account:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "unknown_wechat_account", "请先检测并选择一个微信账号目录")
            return
        db_key = normalize_text(payload.get("db_key"))
        limit = self.parse_int_payload(payload.get("limit"), 200, 1, 10000)
        try:
            sessions = wechat_collector.list_sessions(account["path"], db_key, limit)
        except wechat_collector.SidecarError as exc:
            self.send_error_json(HTTPStatus(exc.status), exc.code, exc.message)
            return

        self.send_json({
            "account_id": account["account_id"],
            "account_name": account.get("account_name"),
            "sessions": sessions,
            "reader": {"mode": "manual_key_sidecar", "media_supported": False},
        })

    def handle_wechat_import(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return

        account = resolve_wechat_account(normalize_text(payload.get("account_id")))
        if not account:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "unknown_wechat_account", "请先检测并选择一个微信账号目录")
            return
        session_id = normalize_text(payload.get("session_id"))
        if not session_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing_session", "请选择要导入的微信会话")
            return
        db_key = normalize_text(payload.get("db_key"))
        limit = self.parse_int_payload(payload.get("limit"), 5000, 1, 50000)
        try:
            records = wechat_collector.export_messages(account["path"], db_key, session_id, limit)
        except wechat_collector.SidecarError as exc:
            self.send_error_json(HTTPStatus(exc.status), exc.code, exc.message)
            return

        result = import_wechat_records(records)
        if not result.messages:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "no_messages", "微信会话中没有读取到可导入的消息")
            return
        payload = make_conversation(result, f"wechat:{session_id}")
        messages: List[Message] = CONVERSATIONS[payload["conversation_id"]]["messages"]
        self.send_json(
            {
                **payload,
                "metrics": compute_metrics(messages),
                "report": generate_report(messages),
            },
            HTTPStatus.CREATED,
        )

    def parse_int_payload(self, value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def handle_role_update(self, path: str) -> None:
        conversation_id, conversation, parts = self.get_conversation_from_path(path)
        if not conversation_id or not conversation or len(parts) != 4:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return

        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return

        self_sender = normalize_text(payload.get("self_sender"))
        messages: List[Message] = conversation["messages"]
        if self_sender not in unique_senders(messages):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "unknown_sender", "请选择解析结果中存在的发送者")
            return

        assign_self_sender(messages, self_sender)
        self.send_json(
            {
                **conversation_payload(conversation_id),
                "self_sender": self_sender,
                "metrics": compute_metrics(messages),
                "report": generate_report(messages),
            }
        )

    def handle_messages_bulk_update(self, path: str) -> None:
        conversation_id, conversation, _parts = self.get_conversation_from_path(path)
        if not conversation_id or not conversation:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return

        updates = payload.get("messages")
        if not isinstance(updates, list):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_payload", "messages 必须是数组")
            return
        messages: List[Message] = conversation["messages"]
        by_id = {message.id: message for message in messages}
        for item in updates:
            if not isinstance(item, dict) or item.get("id") not in by_id:
                continue
            message = by_id[item["id"]]
            if "sender" in item:
                message.sender = normalize_text(item["sender"]) or message.sender
            if "sender_role" in item and item["sender_role"] in {"self", "other", "unknown"}:
                message.sender_role = item["sender_role"]
            if "timestamp" in item:
                parsed = parse_datetime(item["timestamp"])
                message.timestamp = to_iso(parsed) if parsed else None
            if "text" in item:
                message.text = normalize_text(item["text"])
                message.message_type = infer_message_type(message.text)
            if "message_type" in item and item["message_type"] in MESSAGE_TYPES:
                message.message_type = item["message_type"]
            message.needs_review = bool(item.get("needs_review", False))
        self.send_json({**conversation_payload(conversation_id), "metrics": compute_metrics(messages), "report": generate_report(messages)})

    def handle_messages_delete(self, path: str) -> None:
        conversation_id, conversation, _parts = self.get_conversation_from_path(path)
        if not conversation_id or not conversation:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return
        ids = set(payload.get("ids", []))
        if not ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_payload", "ids 不能为空")
            return
        conversation["messages"] = [message for message in conversation["messages"] if message.id not in ids]
        messages: List[Message] = conversation["messages"]
        self.send_json({**conversation_payload(conversation_id), "metrics": compute_metrics(messages), "report": generate_report(messages)})

    def handle_messages_merge(self, path: str) -> None:
        conversation_id, conversation, _parts = self.get_conversation_from_path(path)
        if not conversation_id or not conversation:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", str(exc))
            return
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) < 2:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_payload", "至少选择两条消息合并")
            return
        messages: List[Message] = conversation["messages"]
        selected = [message for message in messages if message.id in ids]
        if len(selected) < 2:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_payload", "未找到足够消息")
            return
        first = selected[0]
        first.text = "\n".join(message.text for message in selected if message.text)
        first.message_type = infer_message_type(first.text)
        first.needs_review = False
        remove_ids = {message.id for message in selected[1:]}
        conversation["messages"] = [message for message in messages if message.id not in remove_ids]
        messages = conversation["messages"]
        self.send_json({**conversation_payload(conversation_id), "metrics": compute_metrics(messages), "report": generate_report(messages)})

    def read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("请求体不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload


def run() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ChatAnalyzerHandler)
    print(f"聊天关系分析器 V2 已启动：http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
