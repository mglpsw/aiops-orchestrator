"""Test fixture only -- not part of the product CLI.

A minimal stand-in for "Process B" (the semantic child) used by
`test_semantic_child_process_boundary_v2.py` to prove the process-boundary
environment-sealing claim against a REAL, unmodified existing owner
(`acquire_diff_v2`).
"""

import sys
from pathlib import Path

_TOOLREPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_TOOLREPO_ROOT))

import os  # noqa: E402

from app.agent_review.controlled_subject_v2 import (  # noqa: E402
    checkout_head_into_subject_v2,
    materialize_controlled_target_subject_v2,
)
from app.agent_review.diff_acquisition_v2 import acquire_diff_v2  # noqa: E402


def main() -> int:
    target_root = Path(sys.argv[1])
    base_sha = sys.argv[2]
    head_sha = sys.argv[3]

    print(f"B_SAW_GIT_DIR={os.environ.get('GIT_DIR')}", file=sys.stderr)
    print(f"B_SAW_PYTHONPATH={os.environ.get('PYTHONPATH')}", file=sys.stderr)

    with materialize_controlled_target_subject_v2(
        target_root, base_sha=base_sha, head_sha=head_sha
    ) as subject:
        checkout_head_into_subject_v2(subject)
        diff = acquire_diff_v2(subject.root, base_sha=base_sha, head_sha=head_sha)
    print(diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
