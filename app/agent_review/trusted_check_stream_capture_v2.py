"""Bounded, concurrent capture of a subject's stdout/stderr (#201-B3).

Stdlib-only for the same reason as ``trusted_check_namespace_kernel_v2.py``:
imported both by ``isolated_executor_v2.py`` and by
``trusted_check_broker_v2.py`` (the latter invoked under ``python -I``).

Two independent properties, neither optional:

- BOUNDED in bytes: the subject controls how much it writes; the host
  controls how much it keeps. The digest still covers the FULL stream, so
  bounding the excerpt costs completeness of the evidence, never its
  integrity;
- CONCURRENT: draining stdout and stderr in sequence deadlocks the moment a
  subject fills the OTHER pipe's buffer -- a classic, and the drain must
  start as soon as the process exists, not after it exits, or a subject that
  writes more than a pipe buffer holds blocks in the kernel until someone
  reads, and a host that waits first and reads afterwards reports that
  self-inflicted deadlock as a TIMEOUT.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import Thread

# Per stream.
OUTPUT_BUDGET_BYTES_V2 = 64 * 1024

# Bounded join when finishing a drain -- a descendant that escaped and still
# holds a pipe open must not stall the verdict.
DRAIN_JOIN_GRACE_SECONDS_V2 = 5.0


@dataclass(frozen=True)
class CapturedStreamV2:
    """``excerpt`` is what the host keeps; ``total_bytes``/``sha256``
    describe what the subject actually produced. Truncation is therefore an
    explicit, verifiable fact rather than a silent loss."""

    excerpt: bytes
    total_bytes: int
    sha256: str
    truncated: bool


EMPTY_CAPTURE_V2 = CapturedStreamV2(b"", 0, hashlib.sha256().hexdigest(), False)


def capture_stream_v2(stream, *, budget: int = OUTPUT_BUDGET_BYTES_V2) -> CapturedStreamV2:
    digest = hashlib.sha256()
    kept = bytearray()
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        if len(kept) < budget:
            kept.extend(chunk[: budget - len(kept)])
    return CapturedStreamV2(
        excerpt=bytes(kept), total_bytes=total, sha256=digest.hexdigest(), truncated=total > budget
    )


def start_drain_v2(process) -> tuple[dict, list]:
    """Start draining stdout and stderr IMMEDIATELY, each on its own thread
    and under its own byte budget. Must be started as soon as the process
    exists -- see the module docstring for why waiting deadlocks."""

    captured: dict[str, CapturedStreamV2] = {}

    def _read(name: str, stream) -> None:
        try:
            captured[name] = capture_stream_v2(stream)
        except (OSError, ValueError):  # pragma: no cover - defensive
            captured[name] = EMPTY_CAPTURE_V2

    threads = [
        Thread(target=_read, args=("stdout", process.stdout), daemon=True),
        Thread(target=_read, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    return captured, threads


def finish_drain_v2(captured: dict, threads: list) -> tuple[CapturedStreamV2, CapturedStreamV2]:
    for thread in threads:
        thread.join(timeout=DRAIN_JOIN_GRACE_SECONDS_V2)
    return captured.get("stdout", EMPTY_CAPTURE_V2), captured.get("stderr", EMPTY_CAPTURE_V2)
