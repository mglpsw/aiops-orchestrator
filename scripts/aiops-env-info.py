#!/usr/bin/env python3
"""Emit normalized AIOps environment context as JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.environment_context import build_environment_context  # noqa: E402


def main() -> int:
    context = build_environment_context(os.environ, source="aiops-env-info")
    json.dump(context, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - unexpected programming errors should be visible.
        json.dump(
            {
                "schema_version": 1,
                "source": "aiops-env-info",
                "environment": "unknown",
                "node_role": "unknown",
                "repo_mode": "unknown",
                "production_runtime": False,
                "agent_review_tooling_allowed": False,
                "limitations": ["unexpected_error"],
                "error_class": exc.__class__.__name__,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        raise SystemExit(1)
