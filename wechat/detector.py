"""Detect local WeChat account directories.

This module intentionally only inspects filesystem structure. It does not
attempt to decrypt or open WeChat databases.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ACCOUNT_MARKERS = (
    ("db_storage",),
    ("FileStorage", "Image"),
    ("FileStorage", "Image2"),
)

IGNORED_DIR_PREFIXES = ("all", "applet", "backup", "wmpf", "crash", "log")


def redact_path(path: Path | str) -> str:
    resolved = str(Path(path).expanduser())
    home = str(Path.home())
    if resolved == home:
        return "~"
    if resolved.startswith(home + os.sep):
        return "~" + resolved[len(home):]
    parts = Path(resolved).parts
    if len(parts) <= 4:
        return resolved
    return str(Path(parts[0], parts[1], "...", *parts[-3:]))


def account_id_for_path(path: Path | str) -> str:
    digest = hashlib.sha1(str(Path(path).expanduser()).encode("utf-8")).hexdigest()[:16]
    return f"wxacct_{digest}"


def candidate_roots(home: Optional[Path] = None, platform: Optional[str] = None) -> List[Path]:
    home = home or Path.home()
    platform = platform or sys.platform
    roots: List[Path] = []

    if platform == "darwin":
        app_support_base = (
            home
            / "Library"
            / "Containers"
            / "com.tencent.xinWeChat"
            / "Data"
            / "Library"
            / "Application Support"
            / "com.tencent.xinWeChat"
        )
        if app_support_base.exists():
            try:
                for entry in app_support_base.iterdir():
                    if entry.is_dir() and _looks_like_wechat_version_dir(entry.name):
                        roots.append(entry)
            except OSError:
                pass
        roots.append(
            home
            / "Library"
            / "Containers"
            / "com.tencent.xinWeChat"
            / "Data"
            / "Documents"
            / "xwechat_files"
        )
    elif platform.startswith("win"):
        roots.append(home / "Documents" / "xwechat_files")
    else:
        roots.extend([
            home / "Documents" / "xwechat_files",
            home / ".local" / "share" / "xwechat_files",
        ])

    deduped: List[Path] = []
    seen = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _looks_like_wechat_version_dir(name: str) -> bool:
    return bool(name) and name[0].isdigit() and any(char.isdigit() for char in name)


def is_account_dir(path: Path | str) -> bool:
    base = Path(path)
    return any((base.joinpath(*marker)).exists() for marker in ACCOUNT_MARKERS)


def is_potential_account_name(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(IGNORED_DIR_PREFIXES):
        return False
    return True


def find_accounts(root: Path | str) -> List[Dict[str, Any]]:
    root_path = Path(root).expanduser()
    accounts: List[Dict[str, Any]] = []
    if not root_path.exists():
        return accounts

    if is_account_dir(root_path):
        return [_account_payload(root_path, root_path.name)]

    try:
        entries = sorted(root_path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return accounts

    for entry in entries:
        if not entry.is_dir() or not is_potential_account_name(entry.name):
            continue
        if is_account_dir(entry):
            accounts.append(_account_payload(entry, entry.name))
    return accounts


def detect_wechat(root_override: Optional[str] = None) -> Dict[str, Any]:
    roots = [Path(root_override).expanduser()] if root_override else candidate_roots()
    accounts: List[Dict[str, Any]] = []
    checked_roots: List[Dict[str, Any]] = []

    for root in roots:
        exists = root.exists()
        checked_roots.append({
            "path": str(root),
            "redacted_path": redact_path(root),
            "exists": exists,
        })
        if exists:
            accounts.extend(find_accounts(root))

    deduped: Dict[str, Dict[str, Any]] = {}
    for account in accounts:
        deduped[account["account_id"]] = account

    warnings = []
    if not deduped:
        warnings.append({
            "code": "wechat_accounts_not_found",
            "message": "未检测到包含 db_storage 或 FileStorage 的微信账号目录。",
        })

    return {
        "success": bool(deduped),
        "platform": sys.platform,
        "roots": checked_roots,
        "accounts": list(deduped.values()),
        "warnings": warnings,
    }


def public_detection_payload(detection: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(detection)
    public["accounts"] = [public_account_payload(account) for account in detection.get("accounts", [])]
    return public


def public_account_payload(account: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in account.items()
        if key not in {"path"}
    }


def _account_payload(path: Path, display_name: str) -> Dict[str, Any]:
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        modified_at = 0
    return {
        "account_id": account_id_for_path(path),
        "account_name": display_name,
        "display_name": display_name,
        "path": str(path),
        "redacted_path": redact_path(path),
        "has_db_storage": (path / "db_storage").exists(),
        "has_file_storage": (path / "FileStorage").exists(),
        "last_modified": int(modified_at),
    }

