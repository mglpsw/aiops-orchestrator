"""Utility to mask secrets in strings before logging or storing."""

from __future__ import annotations

import re


_PATTERNS = [
    # API keys
    (re.compile(r"(sk-[a-zA-Z0-9]{20,})"), r"sk-***MASKED***"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?([^'\"\s]+)", re.IGNORECASE), r"\1***MASKED***"),
    # Tokens
    (re.compile(r"(token\s*[:=]\s*)['\"]?([^'\"\s]+)", re.IGNORECASE), r"\1***MASKED***"),
    # Passwords
    (re.compile(r"(password\s*[:=]\s*)['\"]?([^'\"\s]+)", re.IGNORECASE), r"\1***MASKED***"),
    # Bearer tokens
    (re.compile(r"(Bearer\s+)([A-Za-z0-9._-]+)", re.IGNORECASE), r"\1***MASKED***"),
]


def mask_secrets(text: str) -> str:
    """Replace known secret patterns with masked values."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def truncate(text: str, max_len: int = 4096) -> str:
    """Truncate text with indicator."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [truncated, {len(text)} total chars]"
