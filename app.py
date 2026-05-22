#!/usr/bin/env python3
"""V0 local chat relationship analyzer.

This module intentionally uses only the Python standard library so the
prototype can run in a clean local environment with `python3 app.py`.
"""

from __future__ import annotations

import cgi
import csv
import io
import json
import mimetypes
import os
import re
import statistics
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
NEW_CONVERSATION_GAP = timedelta(hours=6)
REPLY_INTERVAL_CAP = timedelta(days=7)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

CONVERSATIONS: Dict[str, Dict[str, Any]] = {}


@dataclass
class Message:
    id: str
    sender: str
    sender_role: str
    timestamp: Optional[str]
    text: str
    message_type: str
    source: str


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

TIME_FIELDS = ("timestamp", "time", "date")
SENDER_FIELDS = ("sender", "name", "from")
TEXT_FIELDS = ("text", "message", "content")
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
    text = normalize_text(value)
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def is_system_message(sender: str, text: str) -> bool:
    value = f"{sender} {text}".strip().lower()
    return any(marker in value for marker in SYSTEM_MARKERS)


def make_message(
    index: int,
    sender: str,
    text: str,
    source: str,
    timestamp: Optional[datetime] = None,
) -> Message:
    return Message(
        id=f"m_{index:06d}",
        sender=sender,
        sender_role="unknown",
        timestamp=to_iso(timestamp),
        text=text,
        message_type="text",
        source=source,
    )


def parse_txt(content: str) -> Tuple[List[Message], List[Dict[str, Any]]]:
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

        messages.append(make_message(len(messages) + 1, sender, text, "txt", timestamp))

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


def parse_csv_content(content: str) -> Tuple[List[Message], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    messages: List[Message] = []
    stream = io.StringIO(content)

    try:
        reader = csv.DictReader(stream, dialect=sniff_csv_dialect(content))
    except csv.Error as exc:
        return [], [{"line": 1, "raw": "", "reason": f"CSV 解析失败：{exc}"}]

    if not reader.fieldnames:
        return [], [{"line": 1, "raw": "", "reason": "CSV 缺少表头"}]

    sender_field = find_field(reader.fieldnames, SENDER_FIELDS)
    text_field = find_field(reader.fieldnames, TEXT_FIELDS)
    time_field = find_field(reader.fieldnames, TIME_FIELDS)

    if not sender_field or not text_field:
        missing = []
        if not sender_field:
            missing.append("sender/name/from")
        if not text_field:
            missing.append("text/message/content")
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

        messages.append(make_message(len(messages) + 1, sender, text, "csv", timestamp))

    return messages, errors


def parse_upload(filename: str, raw: bytes) -> Tuple[List[Message], List[Dict[str, Any]]]:
    content = decode_upload(raw)
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv":
        return parse_csv_content(content)
    if suffix == ".txt":
        return parse_txt(content)
    return [], [{"line": 0, "raw": filename, "reason": "仅支持 .txt 和 .csv 文件"}]


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


def compute_metrics(messages: List[Message]) -> Dict[str, Any]:
    total_messages = len(messages)
    role_counts = {"self": 0, "other": 0, "unknown": 0}
    role_chars = {"self": 0, "other": 0, "unknown": 0}
    sender_counts: Dict[str, int] = {}
    sender_chars: Dict[str, int] = {}

    for message in messages:
        role = role_label(message)
        length = len(message.text)
        role_counts[role] += 1
        role_chars[role] += length
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

    timed_messages = [
        (parse_message_time(message), message)
        for message in messages
        if parse_message_time(message) is not None
    ]
    timed_messages.sort(key=lambda item: item[0])

    daily_map: Dict[str, Dict[str, Any]] = {}
    for timestamp, message in timed_messages:
        assert timestamp is not None
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
        assert timestamp is not None
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
        assert timestamp is not None
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
    }


def json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class ChatAnalyzerHandler(BaseHTTPRequestHandler):
    server_version = "ChatAnalyzerV0/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        if path.startswith("/api/conversations/"):
            self.handle_conversation_get(path)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未找到请求的资源")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/import":
            self.handle_import()
            return
        if path.startswith("/api/conversations/") and path.endswith("/role"):
            self.handle_role_update(path)
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

    def handle_conversation_get(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "conversations":
            self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "无效的会话接口")
            return

        conversation_id = parts[2]
        action = parts[3]
        conversation = CONVERSATIONS.get(conversation_id)
        if not conversation:
            self.send_error_json(HTTPStatus.NOT_FOUND, "conversation_not_found", "会话不存在或服务已重启")
            return

        messages: List[Message] = conversation["messages"]
        if action == "messages":
            self.send_json(
                {
                    "conversation_id": conversation_id,
                    "messages": [message_to_dict(message) for message in messages],
                    "errors": conversation["errors"],
                    "senders": unique_senders(messages),
                }
            )
            return
        if action == "metrics":
            self.send_json({"conversation_id": conversation_id, "metrics": compute_metrics(messages)})
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "未知的会话操作")

    def handle_import(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "empty_upload", "没有收到上传内容")
            return
        if content_length > MAX_UPLOAD_BYTES:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "file_too_large", "文件不能超过 10MB")
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_content_type", "请使用 multipart/form-data 上传文件")
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        file_item = form["file"] if "file" in form else None
        if file_item is None or not getattr(file_item, "filename", ""):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing_file", "请选择一个聊天记录文件")
            return

        raw = file_item.file.read()
        filename = os.path.basename(file_item.filename)
        messages, errors = parse_upload(filename, raw)

        if not messages:
            self.send_error_json(
                HTTPStatus.BAD_REQUEST,
                "no_messages",
                "没有解析出可用消息，请检查文件格式或内容",
            )
            return

        conversation_id = uuid.uuid4().hex
        CONVERSATIONS[conversation_id] = {
            "filename": filename,
            "messages": messages,
            "errors": errors,
        }
        self.send_json(
            {
                "conversation_id": conversation_id,
                "filename": filename,
                "senders": unique_senders(messages),
                "message_count": len(messages),
                "errors": errors,
                "messages": [message_to_dict(message) for message in messages],
            },
            HTTPStatus.CREATED,
        )

    def handle_role_update(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "conversations" or parts[3] != "role":
            self.send_error_json(HTTPStatus.NOT_FOUND, "not_found", "无效的身份设置接口")
            return

        conversation_id = parts[2]
        conversation = CONVERSATIONS.get(conversation_id)
        if not conversation:
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
                "conversation_id": conversation_id,
                "self_sender": self_sender,
                "senders": unique_senders(messages),
                "messages": [message_to_dict(message) for message in messages],
                "metrics": compute_metrics(messages),
            }
        )

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
    print(f"聊天关系分析器 V0 已启动：http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
