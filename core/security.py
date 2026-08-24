"""Local API security helpers.

The dashboard intentionally has no account system, so the primary browser
boundary is a strict Origin allowlist.  Browser-extension ingestion is a
separate, write-only capability protected by its own token.
"""

from __future__ import annotations

import copy
import hmac
import os
from collections.abc import Iterable
from typing import Any


REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {
    "api_key",
    "bot_token",
    "chat_id",
    "client_secret",
    "ingest_token",
    "password",
    "secret",
    "token",
}


def is_sensitive_key(key: str) -> bool:
    """只遮蔽實際 secret 欄位；`token_env` / `api_key_env` 仍可安全顯示。"""
    normalized = key.lower().strip()
    if normalized.endswith("_env"):
        return False
    sensitive_suffixes = ("_secret", "_token", "_api_key", "_password")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(sensitive_suffixes)


def redact_config(value: Any, key: str = "") -> Any:
    """回傳可供 Web UI 使用、但不含明文 secret 的深拷貝。"""
    if key and is_sensitive_key(key):
        return REDACTED if value not in (None, "") else ""
    if isinstance(value, dict):
        return {k: redact_config(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return copy.deepcopy(value)


def merge_redacted_config(existing: Any, incoming: Any, key: str = "") -> Any:
    """Web UI 回傳遮蔽值時保留原 secret，避免把 token 覆寫成星號。"""
    if key and is_sensitive_key(key) and incoming == REDACTED:
        return copy.deepcopy(existing)
    if isinstance(incoming, dict):
        base = existing if isinstance(existing, dict) else {}
        merged = {k: copy.deepcopy(v) for k, v in base.items()}
        for child_key, child_value in incoming.items():
            merged[child_key] = merge_redacted_config(
                base.get(child_key), child_value, str(child_key)
            )
        return merged
    if isinstance(incoming, list):
        return copy.deepcopy(incoming)
    return copy.deepcopy(incoming)


def configured_allowed_origins(cfg: Any) -> list[str]:
    """取得精確 Origin allowlist；永遠拒絕 wildcard。"""
    configured = cfg.get("security.allowed_origins", None)
    if configured is None:
        port = int(cfg.get("server.port", 8765))
        configured = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    origins = []
    for origin in configured if isinstance(configured, Iterable) and not isinstance(configured, str) else []:
        value = str(origin).strip().rstrip("/")
        if value and value != "*" and value not in origins:
            origins.append(value)
    return origins


def origin_is_allowed(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    """無 Origin 的 local CLI/native client 由 loopback check 管理。"""
    if not origin:
        return True
    normalized = origin.strip().rstrip("/")
    return normalized in set(allowed_origins)


def is_extension_origin(origin: str | None) -> bool:
    return bool(origin and origin.lower().startswith("chrome-extension://"))


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.split("%", 1)[0].lower()
    return normalized in {"127.0.0.1", "::1", "localhost", "testclient"}


def get_extension_ingest_token(cfg: Any) -> str:
    env_name = cfg.get(
        "security.browser_extension_ingest_token_env",
        "OMNICONTEXT_INGEST_TOKEN",
    )
    return str(
        os.environ.get(str(env_name), "")
        or cfg.get("security.browser_extension_ingest_token", "")
        or ""
    )


def extension_ingest_authorized(provided_token: str | None, cfg: Any) -> bool:
    expected = get_extension_ingest_token(cfg)
    if not expected or not provided_token:
        return False
    return hmac.compare_digest(str(provided_token), expected)
