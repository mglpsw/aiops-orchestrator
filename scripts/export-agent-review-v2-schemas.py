#!/usr/bin/env python3
"""Regenerate the committed AgentReview v2 JSON Schemas."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.schema_export_v2 import (  # noqa: E402
    render_v2_json_schema_text,
    render_v2_json_schemas,
)


def main() -> int:
    output_dir = REPO_ROOT / "schemas" / "agent-review" / "v2"
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, schema in render_v2_json_schemas().items():
        (output_dir / filename).write_text(render_v2_json_schema_text(schema), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
