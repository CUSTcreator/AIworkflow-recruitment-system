from __future__ import annotations

from typing import Any


_SENSITIVE_KEYS = frozenset({
    "authorization", "api_key", "access_token", "refresh_token", "token", "secret",
    "password", "minio_secret_key", "cookie", "set-cookie",
})


def redact(value: Any, *, key: str | None = None) -> Any:
    """Remove secrets and bound verbose values before they leave the process."""
    if key and key.lower().replace("-", "_") in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "...[TRUNCATED]"
    return value
