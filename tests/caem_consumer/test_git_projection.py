"""Regression tests for `_verify_git_projection`'s `git_root` HEAD binding.

Codex review round 4 (confirmation pass on PR #173) found: the function only
read blobs from the pinned `carrier_sha` commit object directly (`git
cat-file blob <carrier_sha>:<path>`), and never checked that `git_root`'s
*working tree* was actually checked out at that commit — despite the
function's own docstring stating `git_root` "must be a real Git working tree
checked out at `pin.carrier_sha`". Confirmed as a real gap before this fix:
zero tests existed for `_verify_git_projection` at all, and a manual repro
(temp repo committed at a "carrier" commit, then advanced to a later commit
that still contains the carrier as an ancestor) returned `errors == []` from
the unfixed function, i.e. verified success despite the checkout having
drifted away from the pinned identity.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.caem_consumer.f0 import (
    ReasonCode,
    _canonical_json_digest,
    _git_blob_oid,
    _verify_git_projection,
)
from tests.caem_consumer.conftest import build_fixture_interface, build_fixture_pin


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, check=True, text=True
    ).stdout.strip()


def _init_carrier_commit(root: Path, built: dict[str, Any]) -> str:
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "test@example.invalid", cwd=root)
    _git("config", "user.name", "test", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "carrier", cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


def _projection_digest(root: Path, commit: str, built: dict[str, Any]) -> str:
    projection = []
    for entry in sorted(built["artifact_files"], key=lambda f: f["path"]):
        rel = entry["path"]
        result = subprocess.run(
            ["git", "cat-file", "blob", f"{commit}:{rel}"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        projection.append({"path": rel, "git_blob_oid": _git_blob_oid(result.stdout)})
    return _canonical_json_digest(projection)


def test_git_root_checked_out_at_carrier_verifies(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    carrier = _init_carrier_commit(root, built)
    digest = _projection_digest(root, carrier, built)
    pin = build_fixture_pin(built, carrier_sha=carrier, artifact_git_projection_digest=digest)

    errors = _verify_git_projection(root, pin, built["manifest"])
    assert errors == []


def test_git_root_head_drifted_past_carrier_is_rejected(tmp_path: Path) -> None:
    """The carrier commit remains reachable (it's HEAD~1), so blob reads by
    commit-ish alone would still succeed -- the fix must catch this via HEAD,
    not via blob readability."""
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    carrier = _init_carrier_commit(root, built)
    digest = _projection_digest(root, carrier, built)

    (root / "drift.txt").write_text("advanced past the carrier\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "drift", cwd=root)
    drifted_head = _git("rev-parse", "HEAD", cwd=root)
    assert drifted_head != carrier

    pin = build_fixture_pin(built, carrier_sha=carrier, artifact_git_projection_digest=digest)
    errors = _verify_git_projection(root, pin, built["manifest"])

    assert len(errors) == 1
    assert errors[0].reason_code == ReasonCode.SOURCE_IDENTITY_MISMATCH
    assert carrier in errors[0].message
    assert drifted_head in errors[0].message


def test_git_root_not_a_git_repo_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    pin = build_fixture_pin(built, carrier_sha="0" * 40)

    errors = _verify_git_projection(root, pin, built["manifest"])  # must not raise

    assert len(errors) == 1
    assert errors[0].reason_code == ReasonCode.SOURCE_IDENTITY_MISMATCH
