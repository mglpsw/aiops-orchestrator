from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

SUBJECT = Path("/opt/agent-tools/ar-200d-successor")
sys.path.insert(0, str(SUBJECT))

from app.agent_review.diff_acquisition_v2 import (  # noqa: E402
    DiffAcquisitionError,
    _attribute_bound_diff_worktree_v2,
    acquire_diff_v2,
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def state(repo: Path, tmpdir: Path) -> dict[str, object]:
    common_result = git(repo, "rev-parse", "--git-common-dir")
    common = Path(common_result.stdout.strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    registrations = common / "worktrees"
    return {
        "holder_paths": sorted(str(path) for path in tmpdir.glob("agent-review-diff-attr-source-v2-*")),
        "registration_names": sorted(path.name for path in registrations.iterdir())
        if registrations.is_dir()
        else [],
        "worktree_list_rc": git(repo, "worktree", "list", "--porcelain").returncode,
        "worktree_list": git(repo, "worktree", "list", "--porcelain").stdout,
    }


def main() -> int:
    scenario, repo_text, base, head, tmpdir_text, *extra = sys.argv[1:]
    repo = Path(repo_text)
    tmpdir = Path(tmpdir_text)
    before = state(repo, tmpdir)
    event: dict[str, object] = {"scenario": scenario, "before": before}

    try:
        if scenario == "normal":
            with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
                event["inside_exists"] = worktree.is_dir()
                event["inside_state"] = state(repo, tmpdir)
        elif scenario == "body_exception":
            try:
                with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
                    event["inside_exists"] = worktree.is_dir()
                    raise RuntimeError("q5 deliberate body exception")
            except RuntimeError as exc:
                event["caught"] = repr(exc)
        elif scenario == "body_child_sigkill":
            with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
                child = subprocess.run(
                    ["/bin/sh", "-c", "kill -KILL $$"],
                    cwd=worktree,
                    capture_output=True,
                    check=False,
                )
                event["child_returncode"] = child.returncode
        elif scenario in {
            "remove_child_sigkill",
            "remove_enospc",
            "remove_real_enospc",
            "add_child_sigkill",
            "diff_child_sigkill",
            "full_disk_checkout",
        }:
            try:
                diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
                event["acquire_result"] = "success"
                event["diff_has_added_line"] = "+second" in diff
            except BaseException as exc:
                event["acquire_result"] = "raised"
                event["exception_type"] = type(exc).__name__
                event["exception"] = str(exc)
                if isinstance(exc, DiffAcquisitionError):
                    event["reason_code"] = exc.reason_code
        elif scenario == "parent_hold":
            ready = Path(extra[0])
            with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
                ready.write_text(
                    json.dumps({"pid": os.getpid(), "worktree": str(worktree)}),
                    encoding="utf-8",
                )
                while True:
                    time.sleep(1)
        else:
            raise ValueError(scenario)
    finally:
        if scenario != "parent_hold":
            event["after"] = state(repo, tmpdir)
            print(json.dumps(event, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
