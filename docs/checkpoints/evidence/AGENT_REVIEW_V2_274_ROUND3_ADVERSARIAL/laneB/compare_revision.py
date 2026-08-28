from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.agent_review import toolrepo_identity_v2 as identity
from app.agent_review.diff_acquisition_v2 import (
    acquire_authoritative_diff_v2,
    acquire_diff_v2,
    acquire_raw_diff_v2,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    unified = acquire_diff_v2(args.repo, base_sha=args.base, head_sha=args.head)
    raw = acquire_raw_diff_v2(args.repo, base_sha=args.base, head_sha=args.head)
    authoritative = acquire_authoritative_diff_v2(
        args.repo, base_sha=args.base, head_sha=args.head
    )

    # Exercise the exact same clean fixture and declared SHA under both
    # implementations. The package location is the authority's only root
    # discovery input, so bind it to the fixture instead of the archived
    # implementation checkout.
    identity._agent_review_package_v2.__file__ = str(
        args.repo / "app" / "agent_review" / "__init__.py"
    )
    observed = identity.establish_toolrepo_source_identity_v2(
        declared_toolrepo_sha=args.head,
        executing_script=args.repo / "scripts" / "aiops-review-run-v2.py",
    )

    payload = {
        "unified": unified,
        "unified_sha256": hashlib.sha256(unified.encode()).hexdigest(),
        "raw_hex": raw.encode().hex(),
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "authoritative": [
            {
                "old_path": item.old_path,
                "new_path": item.new_path,
                "change_type": item.change_type,
                "is_binary": item.is_binary,
                "is_submodule": item.is_submodule,
                "similarity_index": item.similarity_index,
                "old_no_newline_at_eof": item.old_no_newline_at_eof,
                "new_no_newline_at_eof": item.new_no_newline_at_eof,
                "truncated": item.truncated,
                "hunks": [hunk.__dict__ for hunk in item.hunks],
            }
            for item in authoritative
        ],
        "identity_sha": observed.toolrepo_sha,
        "identity_root": str(observed.toolrepo_root),
    }
    args.output.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
