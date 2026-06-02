#!/usr/bin/env python3
"""Emit a read-only AIOps runtime backup/rollback planning manifest.

This is a *planning* manifest, not a backup validator. It declares which
persistent runtime stores must be covered by backup/rollback before a CT102
transition window, records which are present/absent at baseline, and lists
limitations. It never asserts that a backup exists or is reliable; that requires
real post-execution evidence captured during the controlled window.

Boundaries (read-only): no subprocess, no docker, no systemctl, no network, no
`.env` content read, no store-content read, no write inside the repo root, no raw
absolute path in the output, no secret output, no backup/rollback execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class SafeError(Exception):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a read-only AIOps runtime backup/rollback planning manifest.",
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--container-data-path", default="/app/data/aiops.db")
    parser.add_argument("--docker-volume-name", default="aiops-data")
    parser.add_argument("--expected-baseline-commit", default="")
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        output = Path(args.output).resolve()
        _validate_output_path(repo_root, output)

        payload = build_manifest(
            repo_root,
            container_data_path=args.container_data_path,
            docker_volume_name=args.docker_volume_name,
            expected_baseline_commit=args.expected_baseline_commit,
        )
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except SafeError as exc:
        _emit_error(exc.error_class, exc.message)
        return 1


def build_manifest(
    repo_root: Path,
    *,
    container_data_path: str,
    docker_volume_name: str,
    expected_baseline_commit: str,
) -> dict[str, Any]:
    limitations: list[str] = []
    git = _read_git(repo_root, limitations)
    baseline_commit = git["head_sha"]

    if expected_baseline_commit:
        if not baseline_commit:
            limitations.append("expected_baseline_commit_unverifiable")
        elif baseline_commit != expected_baseline_commit:
            limitations.append("expected_baseline_commit_mismatch")

    presence = _read_path_presence(repo_root)
    stores = _build_stores(presence, container_data_path, docker_volume_name)

    docker_volume_hints = [
        {
            "id": docker_volume_name,
            "container_path": container_data_path,
            "backup_required": True,
            "rollback_required": True,
        }
    ]

    minimum_backup_complete = _minimum_backup_complete(
        presence,
        container_data_path=container_data_path,
        docker_volume_name=docker_volume_name,
        limitations=limitations,
    )

    return {
        "schema_version": 1,
        "source": "aiops-runtime-backup-manifest",
        "repo_root": repo_root.name,
        "baseline_commit": baseline_commit,
        "expected_baseline_commit": expected_baseline_commit,
        "stores": stores,
        "docker_volume_hints": docker_volume_hints,
        "minimum_backup_complete": minimum_backup_complete,
        "limitations": limitations,
    }


def _validate_output_path(repo_root: Path, output: Path) -> None:
    if output == repo_root or repo_root in output.parents:
        raise SafeError("repo_output_blocked", "Output must be outside --repo-root.")


def _read_path_presence(repo_root: Path) -> dict[str, bool]:
    return {
        "env": (repo_root / ".env").exists(),
        "config": (repo_root / "config").exists(),
        "audit": (repo_root / "var" / "audit").exists(),
        "approvals": (repo_root / "var" / "approvals").exists(),
        "runs": (repo_root / "var" / "runs").exists(),
        "data": (repo_root / "data").exists(),
        "docker_compose": (repo_root / "deploy" / "docker-compose.yml").exists(),
    }


def _build_stores(
    presence: dict[str, bool],
    container_data_path: str,
    docker_volume_name: str,
) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []

    stores.append(
        _store(
            "config",
            target="config",
            kind="dir",
            exists=presence["config"],
            note="runtime configuration and action catalog; must be preserved",
        )
    )
    stores.append(
        _store(
            "env",
            target=".env",
            kind="file",
            exists=presence["env"],
            note="secrets file; presence only, never read; restore as a unit without exposing secrets",
        )
    )
    stores.append(
        _store(
            "audit",
            target="var/audit",
            kind="dir",
            exists=presence["audit"],
            note="audit JSONL store; present at baseline; must be preserved",
        )
    )

    # On-demand JSONL stores: absence at baseline is a limitation, not an error.
    stores.append(_on_demand_store("approvals", "var/approvals", presence["approvals"]))
    stores.append(_on_demand_store("runs", "var/runs", presence["runs"]))

    # Database: lives in a Docker volume; host data dir may be absent by design.
    if presence["data"]:
        stores.append(
            _store(
                "data",
                target="data",
                kind="dir",
                exists=True,
                note="host data directory present; preserve database content",
            )
        )
    else:
        stores.append(
            {
                "id": "data",
                "target": "data",
                "kind": "container_path",
                "exists": False,
                "backup_required": True,
                "rollback_required": True,
                "baseline_state": "missing",
                "note": (
                    f"host data dir absent; database lives in docker volume "
                    f"'{docker_volume_name}' at '{container_data_path}'; "
                    f"preserve the volume or an equivalent DB file"
                ),
            }
        )

    stores.append(
        _store(
            "docker_compose",
            target="deploy/docker-compose.yml",
            kind="file",
            exists=presence["docker_compose"],
            note="deploy definition; required for restore context",
        )
    )

    return stores


def _store(store_id: str, *, target: str, kind: str, exists: bool, note: str) -> dict[str, Any]:
    return {
        "id": store_id,
        "target": target,
        "kind": kind,
        "exists": exists,
        "backup_required": True,
        "rollback_required": True,
        "baseline_state": "present" if exists else "missing",
        "note": note,
    }


def _on_demand_store(store_id: str, target: str, exists: bool) -> dict[str, Any]:
    if exists:
        return _store(
            store_id,
            target=target,
            kind="dir",
            exists=True,
            note="JSONL store present at baseline; preserve content",
        )
    return {
        "id": store_id,
        "target": target,
        "kind": "missing_baseline",
        "exists": False,
        "backup_required": True,
        "rollback_required": True,
        "baseline_state": "missing",
        "note": "missing at baseline; preserve absence or restore if created during transition",
    }


def _minimum_backup_complete(
    presence: dict[str, bool],
    *,
    container_data_path: str,
    docker_volume_name: str,
    limitations: list[str],
) -> bool:
    complete = True

    if not presence["config"]:
        complete = False
        limitations.append("config_missing")
    if not presence["docker_compose"]:
        complete = False
        limitations.append("docker_compose_missing")
    if not presence["audit"]:
        complete = False
        limitations.append("audit_missing")

    data_documented = bool(container_data_path) or bool(docker_volume_name)
    if not presence["data"]:
        if data_documented:
            # Expected for the containerized deploy: not fatal, just recorded.
            limitations.append("data_host_dir_missing_using_docker_volume")
        else:
            complete = False
            limitations.append("data_host_dir_missing_undocumented")

    if not presence["approvals"]:
        complete = False
        limitations.append("approvals_missing_baseline")
    if not presence["runs"]:
        complete = False
        limitations.append("runs_missing_baseline")

    return complete


def _read_git(repo_root: Path, limitations: list[str]) -> dict[str, str]:
    head_path = repo_root / ".git" / "HEAD"
    head_ref = ""
    head_sha = ""

    try:
        head_value = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        limitations.append("git_head_unavailable")
        return {"head_ref": "", "head_sha": "", "status": "unknown"}

    if head_value.startswith("ref: "):
        head_ref = head_value.removeprefix("ref: ").strip()
        ref_path = repo_root / ".git" / head_ref
        try:
            head_sha = ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            head_sha = _read_packed_ref(repo_root, head_ref)
            if not head_sha:
                limitations.append("git_ref_unavailable")
    else:
        head_sha = head_value
        head_ref = "detached"

    status = "observed" if head_ref or head_sha else "unknown"
    return {"head_ref": head_ref, "head_sha": head_sha, "status": status}


def _read_packed_ref(repo_root: Path, head_ref: str) -> str:
    packed_refs = repo_root / ".git" / "packed-refs"
    try:
        for line in packed_refs.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == head_ref:
                return parts[0].strip()
    except OSError:
        return ""
    return ""


def _emit_error(error_class: str, message: str) -> None:
    json.dump({"error_class": error_class, "message": message}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
