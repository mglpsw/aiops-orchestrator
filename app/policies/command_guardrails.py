"""Shared command guardrails for AIOps execution and catalog validation.

This module centralizes the blocking logic for destructive Docker commands and
shell-wrapper bypass attempts so the policy engine, catalog loader, and legacy
executors all make the same decision.
"""

from __future__ import annotations

import re

# Explicitly blocked wrapper patterns. These are denied even before looking at
# the underlying command because they are commonly used to smuggle dangerous
# shell execution through a policy gate.
BLOCKED_WRAPPER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(?:^|[\s;&|])(?:sudo|env|nohup)\b"),
    re.compile(r"(?i)\b(?:sh|bash)\s+-[lc]\b"),
)

# Docker commands that are safe to expose as read-only diagnostics.
SAFE_DOCKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bdocker(?:-compose|\s+compose)\b(?:\s+-\S+(?:\s+\S+)*)*\s+config\s+--quiet\b"),
    re.compile(r"(?i)\bdocker(?:-compose|\s+compose)\b(?:\s+-\S+(?:\s+\S+)*)*\s+ps\b"),
    re.compile(r"(?i)\bdocker(?:-compose|\s+compose)\b(?:\s+-\S+(?:\s+\S+)*)*\s+logs\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+version\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+info\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+ps\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+inspect\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+logs\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+events\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+stats\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+top\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+port\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+images\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+volume\s+ls\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+network\s+ls\b"),
)

# Destructive Docker commands that must never run automatically.
BLOCKED_DOCKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bdocker(?:-compose|\s+compose)\b(?:\s+\S+)*\s+(down|stop|restart|rm)\b"),
    re.compile(r"(?i)\bdocker\b(?:\s+\S+)*\s+(stop|kill|rm|restart|update)\b"),
    re.compile(r"(?i)\bdocker\s+system\s+prune\b"),
    re.compile(r"(?i)\bdocker\s+container\s+prune\b"),
    re.compile(r"(?i)\bdocker\s+network\s+prune\b"),
    re.compile(r"(?i)\bdocker\s+volume\s+prune\b"),
)


def find_blocked_command_reason(command: str) -> str | None:
    """Return the blocking reason for *command*, or None if it is not blocked."""
    text = command.strip()
    if not text:
        return "empty command"

    for pattern in BLOCKED_WRAPPER_PATTERNS:
        if pattern.search(text):
            return f"blocked shell wrapper pattern '{pattern.pattern}'"

    for pattern in BLOCKED_DOCKER_PATTERNS:
        if pattern.search(text):
            return f"blocked Docker pattern '{pattern.pattern}'"

    return None


def is_safe_command(command: str) -> bool:
    """Return True when *command* is a known read-only diagnostic command."""
    text = command.strip()
    return any(pattern.search(text) for pattern in SAFE_DOCKER_PATTERNS)
