"""Persistent append-only store for read-only AIOps runs."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.agent_router.schemas import ActionRunResponse, ActionRunResult, RunDetailResponse, RunRecentResponse, RunSummary
from app.agent_router.services.action_runner import redact_sensitive_text
from app.core.config import BASE_DIR, get_settings

_RUN_LOCK = threading.RLock()


class RunStoreError(RuntimeError):
    """Raised when the run store cannot be read or written."""


def resolve_run_store_path() -> Path:
    settings = get_settings()
    path = Path(settings.run_store_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _serialize_run_record(run: ActionRunResponse, *, requested_action_ids: list[str], actor: str) -> str:
    payload: dict[str, Any] = {
        "run_id": run.run_id,
        "target": run.target,
        "approval_id": run.approval_id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "results": [result.model_dump(mode="json") for result in run.results],
        "blocked_steps": [step.model_dump(mode="json") for step in run.blocked_steps],
        "warnings": list(run.warnings),
        "requested_action_ids": list(requested_action_ids),
        "actor": actor,
        "metadata": {"schema_version": "run.v1"},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _parse_record(raw_line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_line)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _record_to_summary(record: dict[str, Any]) -> RunSummary | None:
    try:
        return RunSummary(
            run_id=str(record["run_id"]),
            target=str(record["target"]),
            approval_id=str(record["approval_id"]),
            status=record["status"],
            started_at=str(record["started_at"]),
            finished_at=str(record["finished_at"]),
            requested_action_ids=list(record.get("requested_action_ids") or []),
            result_count=len(record.get("results") or []),
            blocked_count=len(record.get("blocked_steps") or []),
            warning_count=len(record.get("warnings") or []),
        )
    except Exception:
        return None


def _record_to_detail(record: dict[str, Any]) -> RunDetailResponse | None:
    try:
        results = [
            ActionRunResult.model_validate(item)
            for item in list(record.get("results") or [])
            if isinstance(item, dict)
        ]
        blocked_steps = list(record.get("blocked_steps") or [])
        return RunDetailResponse(
            run_id=str(record["run_id"]),
            target=str(record["target"]),
            approval_id=str(record["approval_id"]),
            status=record["status"],
            started_at=str(record["started_at"]),
            finished_at=str(record["finished_at"]),
            requested_action_ids=list(record.get("requested_action_ids") or []),
            results=results,
            blocked_steps=blocked_steps,  # type: ignore[arg-type]
            warnings=list(record.get("warnings") or []),
        )
    except Exception:
        return None


def _load_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if not path.exists():
        return [], warnings

    records: list[dict[str, Any]] = []
    invalid_seen = False
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            record = _parse_record(line)
            if record is None:
                invalid_seen = True
                continue
            records.append(record)

    if invalid_seen:
        warnings.append("One or more invalid run records were ignored.")
    return records, warnings


def _compact_run_store(path: Path, max_records: int) -> None:
    if max_records <= 0:
        return

    records, _ = _load_records(path)
    if not records:
        return

    records = records[-max_records:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    tmp_path.replace(path)


def append_run(
    run: ActionRunResponse,
    *,
    requested_action_ids: list[str],
    actor: str = "authenticated_user",
) -> bool:
    path = resolve_run_store_path()
    payload = _serialize_run_record(run, requested_action_ids=requested_action_ids, actor=actor)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _RUN_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            settings = get_settings()
            if settings.run_store_max_records > 0:
                records, _ = _load_records(path)
                if len(records) > settings.run_store_max_records:
                    _compact_run_store(path, settings.run_store_max_records)
        return True
    except Exception:
        return False


def list_recent_runs(
    *,
    limit: int = 20,
    target: str | None = None,
    status: str | None = None,
) -> tuple[RunRecentResponse, list[str]]:
    path = resolve_run_store_path()
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    with _RUN_LOCK:
        records, warnings = _load_records(path)

    filtered: list[RunSummary] = []
    for record in reversed(records):
        if target is not None and str(record.get("target")) != target:
            continue
        if status is not None and str(record.get("status")) != status:
            continue
        summary = _record_to_summary(record)
        if summary is None:
            warnings.append("One or more invalid run records were ignored.")
            continue
        filtered.append(summary)
        if len(filtered) >= limit:
            break

    return RunRecentResponse(runs=filtered, count=len(filtered), warnings=warnings), warnings


def get_run(run_id: str) -> tuple[RunDetailResponse | None, list[str]]:
    path = resolve_run_store_path()
    with _RUN_LOCK:
        records, warnings = _load_records(path)

    for record in reversed(records):
        if str(record.get("run_id")) != run_id:
            continue
        detail = _record_to_detail(record)
        if detail is None:
            warnings.append("One or more invalid run records were ignored.")
            return None, warnings
        detail.results = [
            ActionRunResult(
                action_id=result.action_id,
                status=result.status,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                output_preview=redact_sensitive_text(result.output_preview),
                truncated=result.truncated,
            )
            for result in detail.results
        ]
        detail.warnings = [redact_sensitive_text(warning) for warning in detail.warnings]
        return detail, warnings

    return None, warnings


def write_run_record(
    run: ActionRunResponse,
    *,
    requested_action_ids: list[str],
    actor: str = "authenticated_user",
) -> bool:
    return append_run(run, requested_action_ids=requested_action_ids, actor=actor)
