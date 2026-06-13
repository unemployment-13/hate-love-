#!/usr/bin/env python3
"""Native-reader boundary for WeChat imports.

This script defines the stable CLI/JSON protocol used by the Python web app.
It intentionally does not bundle WeFlow binaries or attempt process-key
extraction. A future Rust/C++ implementation can replace this file while
preserving the same commands and JSON response shapes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat.detector import detect_wechat, find_accounts, public_detection_payload  # noqa: E402


FIXTURE_NAME = "wechat_reader_fixture.json"


def ok(payload: Dict[str, Any]) -> None:
    print(json.dumps({"success": True, **payload}, ensure_ascii=False))


def fail(code: str, message: str, status: int = 500, extra: Optional[Dict[str, Any]] = None) -> int:
    print(json.dumps({
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "status": status,
            **(extra or {}),
        },
    }, ensure_ascii=False))
    return 1


def load_fixture(account: str) -> Optional[Dict[str, Any]]:
    candidates: List[Path] = []
    env_fixture = os.environ.get("CHAT_ANALYZER_WECHAT_FIXTURE")
    if env_fixture:
        candidates.append(Path(env_fixture))
    candidates.append(Path(account) / FIXTURE_NAME)

    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"fixture 读取失败：{exc}") from exc
        if isinstance(payload, dict):
            return payload
    return None


def require_key(value: str) -> Optional[int]:
    if not value.strip():
        return fail("invalid_key", "V2.0 手动密钥模式需要填写数据库密钥。", 400)
    return None


def cmd_detect(args: argparse.Namespace) -> int:
    ok(public_detection_payload(detect_wechat(args.root)))
    return 0


def cmd_list_accounts(args: argparse.Namespace) -> int:
    accounts = find_accounts(args.root)
    ok({"accounts": [{key: value for key, value in account.items() if key != "path"} for account in accounts]})
    return 0


def cmd_list_sessions(args: argparse.Namespace) -> int:
    invalid = require_key(args.key)
    if invalid is not None:
        return invalid
    try:
        fixture = load_fixture(args.account)
    except RuntimeError as exc:
        return fail("fixture_error", str(exc), 500)
    if fixture:
        sessions = fixture.get("sessions", [])
        if not isinstance(sessions, list):
            sessions = []
        ok({"sessions": sessions[: args.limit]})
        return 0
    return fail(
        "native_reader_unavailable",
        "V2 已接通微信读取 sidecar 协议，但真实 WCDB native reader 尚未编译接入；当前不会使用第三方二进制或 WeFlow 代码。",
        501,
    )


def cmd_export_messages(args: argparse.Namespace) -> int:
    invalid = require_key(args.key)
    if invalid is not None:
        return invalid
    try:
        fixture = load_fixture(args.account)
    except RuntimeError as exc:
        return fail("fixture_error", str(exc), 500)
    if fixture:
        message_map = fixture.get("messages", {})
        if isinstance(message_map, dict):
            messages = message_map.get(args.session, [])
        else:
            messages = []
        if not isinstance(messages, list):
            messages = []
        ok({"messages": messages[: args.limit]})
        return 0
    return fail(
        "native_reader_unavailable",
        "V2 已接通微信读取 sidecar 协议，但真实 WCDB native reader 尚未编译接入；当前不会使用第三方二进制或 WeFlow 代码。",
        501,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WeChat local reader sidecar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect")
    detect.add_argument("--root", default=None)
    detect.set_defaults(func=cmd_detect)

    accounts = subparsers.add_parser("list-accounts")
    accounts.add_argument("--root", required=True)
    accounts.set_defaults(func=cmd_list_accounts)

    sessions = subparsers.add_parser("list-sessions")
    sessions.add_argument("--account", required=True)
    sessions.add_argument("--key", required=True)
    sessions.add_argument("--limit", type=int, default=200)
    sessions.set_defaults(func=cmd_list_sessions)

    messages = subparsers.add_parser("export-messages")
    messages.add_argument("--account", required=True)
    messages.add_argument("--key", required=True)
    messages.add_argument("--session", required=True)
    messages.add_argument("--limit", type=int, default=5000)
    messages.set_defaults(func=cmd_export_messages)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
