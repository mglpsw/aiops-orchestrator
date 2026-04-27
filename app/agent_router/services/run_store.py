"""Persistent append-only store for read-only AIOps runs."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.agent_router.schemas import ActionRunResponse
from app.core.config import BASE_DIR, get_settings

_RUN_LOCK = threading.Lock()


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


def write_run_record(
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
        return True
    except Exception:
        return False
