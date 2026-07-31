from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate-agent-review-profile-v1-v2.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_v1_profile(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "target_repo": "mglpsw/aiops-orchestrator",
                "artifacts": [
                    {"name": "full-diff", "path": "artifacts/full.diff", "kind": "diff", "required": True}
                ],
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )


def test_migration_cli_writes_a_report_with_pending_decisions(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile-v1.json"
    _write_v1_profile(profile_path)
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    output_path = tmp_path / "out" / "report.json"

    result = _run(
        [
            "--profile-v1",
            str(profile_path),
            "--repo",
            "mglpsw/aiops-orchestrator",
            "--default-branch",
            "master",
            "--repo-root",
            str(repo_root),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    assert doc["candidate_is_directly_usable"] is False
    assert doc["pending_decisions"]
    assert doc["candidate"]["policies"]["network_policy"] == "forbidden"


def test_migration_cli_rejects_an_output_path_inside_repo_root(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile-v1.json"
    _write_v1_profile(profile_path)
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    output_path = repo_root / "report.json"

    result = _run(
        [
            "--profile-v1",
            str(profile_path),
            "--repo",
            "mglpsw/aiops-orchestrator",
            "--default-branch",
            "master",
            "--repo-root",
            str(repo_root),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()


def test_migration_cli_rejects_a_non_mapping_profile_document(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile-v1.json"
    profile_path.write_text("[1, 2, 3]", encoding="utf-8")
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir()
    output_path = tmp_path / "out" / "report.json"

    result = _run(
        [
            "--profile-v1",
            str(profile_path),
            "--repo",
            "mglpsw/aiops-orchestrator",
            "--default-branch",
            "master",
            "--repo-root",
            str(repo_root),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()
