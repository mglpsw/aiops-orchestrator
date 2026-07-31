#!/usr/bin/env python3
"""Explicit, human-run tool: migrate a v1 target profile to a v2 candidate.

Never invoked from the review pipeline. Never overwrites the canonical v2
profile. Writes a JSON report (candidate + pending human decisions) to
``--output``, which must not live inside ``--repo-root``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.profile_migration_v1_v2 import migrate_profile_v1_to_v2  # noqa: E402
from app.agent_review.schemas import TargetProfile  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a v1 AgentReview target profile to a v2 candidate + report."
    )
    parser.add_argument("--profile-v1", required=True, help="Path to the v1 repo-profile.yaml/json")
    parser.add_argument("--repo", required=True, help="Trusted repo identity, owner/name")
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--repo-root", required=True, help="Trusted base/default checkout root")
    parser.add_argument("--output", required=True, help="Report output path (must not be inside --repo-root)")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    output_path = Path(args.output).resolve()
    try:
        output_path.relative_to(repo_root)
    except ValueError:
        pass
    else:
        print("Blocked: --output must not be inside --repo-root.", file=sys.stderr)
        return 2

    profile_v1_path = Path(args.profile_v1)
    try:
        raw_text = profile_v1_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Blocked: cannot read --profile-v1 ({exc.__class__.__name__}).", file=sys.stderr)
        return 2

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        import yaml

        raw = yaml.safe_load(raw_text)

    if not isinstance(raw, dict):
        print("Blocked: --profile-v1 did not parse into an object.", file=sys.stderr)
        return 2

    profile_v1 = TargetProfile.model_validate(raw)
    report = migrate_profile_v1_to_v2(
        profile_v1, repo=args.repo, default_branch=args.default_branch
    )

    output_doc = {
        "source_schema_version": report.source_schema_version,
        "candidate": report.candidate,
        "candidate_is_directly_usable": report.candidate_is_directly_usable,
        "pending_decisions": [asdict(decision) for decision in report.pending_decisions],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Migration report written to {output_path}")
    print(f"Pending human decisions: {len(report.pending_decisions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
