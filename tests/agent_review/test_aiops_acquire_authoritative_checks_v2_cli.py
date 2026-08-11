"""Host-owned CI acquirer (`#201-C0`, C0-6).

Driven entirely from recorded GitHub payloads. The acquirer's real-API path is
deliberately not a merge gate for C0 -- see the PR description -- so these tests
prove the mapping, the local-git parentage observation, and the fail-closed
behaviour, without a token or a network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-acquire-authoritative-checks-v2.py"

REPO = "mglpsw/aiops-orchestrator"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def merge_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A real repository with a real merge commit, so parentage is observed
    rather than asserted."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "feature")
    head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "synthetic merge")
    merge = _git(repo, "rev-parse", "HEAD")
    return repo, base, head, merge


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "check_runs": [
            {
                "id": 100,
                "name": "Validate repository",
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "check_suite": {"id": 55},
            }
        ],
        "workflow_runs": [
            {
                "id": 900,
                "check_suite_id": 55,
                "path": ".github/workflows/ci.yml",
                "event": "pull_request",
                "head_branch": "feature",
                "run_attempt": 2,
                "pull_requests": [{"number": 7}],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _acquire(tmp_path: Path, merge_repo, payload: dict) -> subprocess.CompletedProcess[str]:
    repo, _base, head, merge = merge_repo
    observations = tmp_path / "payload.json"
    observations.write_text(json.dumps(payload), encoding="utf-8")
    return _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", merge,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(tmp_path / "snapshot.json"),
        ]
    )


def test_acquires_a_parseable_snapshot(tmp_path: Path, merge_repo) -> None:
    repo, base, head, merge = merge_repo
    result = _acquire(tmp_path, merge_repo, _payload())
    assert result.returncode == 0, result.stderr

    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.acquisition.repository == REPO
    assert snapshot.acquisition.head_sha == head
    assert snapshot.tested_merge_sha == merge
    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].run_attempt == 2


def test_parentage_is_observed_from_real_git_not_from_the_api(tmp_path: Path, merge_repo) -> None:
    """The API reported nothing about parentage. Asking the same service that
    reported the check to also vouch for the tree would be circular."""

    repo, base, head, merge = merge_repo
    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert list(snapshot.tested_merge_parents) == [base, head]


def test_acquisition_identity_is_recorded(tmp_path: Path, merge_repo) -> None:
    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.acquisition.acquired_by == "aiops-acquire-authoritative-checks-v2"
    assert snapshot.acquisition.api_host == "api.github.com"


def test_pull_request_runs_report_a_pull_ref_not_a_base_ref(tmp_path: Path, merge_repo) -> None:
    """The KNOWN LIMITATION, pinned as a test so it cannot be quietly
    "fixed" by asserting a base-owned origin GitHub never reported."""

    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].workflow_ref == "refs/pull/7/merge"


def test_branch_runs_report_a_branch_ref(tmp_path: Path, merge_repo) -> None:
    payload = _payload()
    payload["workflow_runs"][0].update({"event": "push", "pull_requests": []})
    _acquire(tmp_path, merge_repo, payload)
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].workflow_ref == "refs/heads/feature"


def test_a_check_run_with_no_matching_workflow_run_is_dropped(tmp_path: Path, merge_repo) -> None:
    """Dropped rather than recorded with invented workflow fields. Safe:
    a required check that ends up absent fails closed downstream."""

    payload = _payload()
    payload["check_runs"].append(
        {
            "id": 101,
            "name": "orphan",
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
            "check_suite": {"id": 999},
        }
    )
    _acquire(tmp_path, merge_repo, payload)
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert [obs.check_run_name for obs in snapshot.observations] == ["Validate repository"]


def test_a_non_merge_tested_commit_is_refused(tmp_path: Path, merge_repo) -> None:
    repo, base, head, _merge = merge_repo
    observations = tmp_path / "payload.json"
    observations.write_text(json.dumps(_payload()), encoding="utf-8")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")

    result = _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", root,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(tmp_path / "snapshot.json"),
        ]
    )

    assert result.returncode != 0
    assert "git_observation_failed" in result.stderr


def test_output_is_canonical_and_reproducible(tmp_path: Path, merge_repo) -> None:
    _acquire(tmp_path, merge_repo, _payload())
    first = (tmp_path / "snapshot.json").read_bytes()
    _acquire(tmp_path, merge_repo, _payload())
    assert (tmp_path / "snapshot.json").read_bytes() == first


def test_an_unparseable_snapshot_is_never_written(tmp_path: Path, merge_repo) -> None:
    """Writing something the offline parser would reject just moves the
    failure downstream."""

    payload = _payload()
    payload["check_runs"][0]["conclusion"] = "exploded"
    result = _acquire(tmp_path, merge_repo, payload)

    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()
