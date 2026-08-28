"""Reproduces: `establish_toolrepo_source_identity_v2` trusts an empty
`git diff --name-only HEAD -- <bounded paths>` as proof the worktree
matches the declared commit. `git update-index --assume-unchanged` makes
Git itself omit a modified tracked file from that diff, so the identity
authority PASSES while the file on disk is materially different from what
is committed at the declared SHA.

This is run against a disposable CLONE of the toolrepo checked out at the
exact head under test, never against the toolrepo's own working checkout
-- tampering with this repository's own tree to prove the point would
itself be the kind of action this round is forbidden from taking.

Usage:
    /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \\
        04_assume_unchanged_defeats_identity.py \\
        --source /opt/agent-tools/ar-200d-successor \\
        --sha c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def sh(cmd: str, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, shell=True, check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="assume-unchanged-"))
    clone = scratch / "tr"
    subprocess.run(
        ["git", "clone", "--quiet", str(args.source), str(clone)], check=True
    )
    subprocess.run(
        ["git", "checkout", "--quiet", args.sha], cwd=clone, check=True
    )

    sys.path.insert(0, str(clone))
    from app.agent_review.toolrepo_identity_v2 import (  # noqa: E402
        establish_toolrepo_source_identity_v2,
    )

    target = clone / "app" / "agent_review" / "toolrepo_identity_v2.py"
    with target.open("a") as f:
        f.write("\n# TAMPERED\n")
    sh(f"git update-index --assume-unchanged {target.relative_to(clone)}", clone)

    diff_output = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "app"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    print(f"git diff --name-only HEAD -- app: [{diff_output.strip()}]")

    try:
        identity = establish_toolrepo_source_identity_v2(declared_toolrepo_sha=args.sha)
        print(f"IDENTITY RESULT: PASSED -> {identity}")
        passed = True
    except Exception as exc:  # noqa: BLE001 -- report whatever it raises
        print(f"IDENTITY RESULT: refused -> {type(exc).__name__}: {exc}")
        passed = False

    on_disk_tail = target.read_text().splitlines()[-1]
    print(f"actual on-disk last line: {on_disk_tail!r}")

    if passed and on_disk_tail.strip() == "# TAMPERED":
        print("CONFIRMED: identity passed while bounded tracked source was tampered")
        return 0
    print("not reproduced")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
