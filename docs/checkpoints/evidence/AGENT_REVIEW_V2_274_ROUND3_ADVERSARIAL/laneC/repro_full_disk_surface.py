from __future__ import annotations

import errno
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
from app.agent_review.reference_source_v2 import resolve_reference_source_v2


repo = Path("/tmp/ar274-review-r3/laneC/q10/config-semantics")
base = subprocess.run(
    ["git", "rev-parse", "HEAD^"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
enospc = OSError(errno.ENOSPC, "No space left on device")

with patch("app.agent_review.diff_acquisition_v2.tempfile.mkdtemp", side_effect=enospc):
    try:
        acquire_diff_v2(repo, base_sha=base, head_sha=head)
    except BaseException as exc:
        print(f"DIFF_EXCEPTION={type(exc).__name__}:{exc}")

with patch("app.agent_review.reference_source_v2.tempfile.mkdtemp", side_effect=enospc):
    try:
        with resolve_reference_source_v2(repo_root=repo, head_sha=head, profile=None):  # type: ignore[arg-type]
            pass
    except BaseException as exc:
        print(f"REFERENCE_EXCEPTION={type(exc).__name__}:{exc}")
