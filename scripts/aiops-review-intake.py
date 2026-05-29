#!/usr/bin/env python3
"""Generate offline AgentReview intake and redaction report JSON."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

