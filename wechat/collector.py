"""Bridge from the Python app to the native WeChat reader sidecar."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_READER = ROOT_DIR / "native" / "wechat_reader" / "wechat_reader.py"


class SidecarError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def reader_command() -> List[str]:
    return [sys.executable, str(DEFAULT_READER)]


def run_reader(args: List[str], timeout: int = 45) -> Dict[str, Any]:
    command = reader_command() + args
    if not DEFAULT_READER.exists():
        raise SidecarError("native_reader_missing", "未找到微信读取 sidecar。", 501)

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SidecarError("native_reader_timeout", f"微信读取 sidecar 超时：{exc}", 504) from exc
    except OSError as exc:
        raise SidecarError("native_reader_failed", f"微信读取 sidecar 启动失败：{exc}", 500) from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as exc:
        raise SidecarError(
            "native_reader_bad_output",
            "微信读取 sidecar 没有返回有效 JSON。",
            500,
            {"stdout": stdout[:500], "stderr": stderr[:500]},
        ) from exc

    if completed.returncode != 0 or payload.get("success") is False:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "native_reader_failed")
        message = str(error.get("message") or stderr or "微信读取 sidecar 执行失败。")
        status = int(error.get("status") or 500)
        raise SidecarError(code, message, status, error)

    return payload


def list_sessions(account_path: str, db_key: str, limit: int = 200) -> List[Dict[str, Any]]:
    payload = run_reader([
        "list-sessions",
        "--account",
        account_path,
        "--key",
        db_key,
        "--limit",
        str(limit),
    ])
    sessions = payload.get("sessions", [])
    return sessions if isinstance(sessions, list) else []


def export_messages(
    account_path: str,
    db_key: str,
    session_id: str,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    payload = run_reader([
        "export-messages",
        "--account",
        account_path,
        "--key",
        db_key,
        "--session",
        session_id,
        "--limit",
        str(limit),
    ], timeout=90)
    messages = payload.get("messages", [])
    return messages if isinstance(messages, list) else []

