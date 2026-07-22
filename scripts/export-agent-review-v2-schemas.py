#!/usr/bin/env python3
"""Regenerate the committed AgentReview v2 JSON Schemas."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.schema_export_v2 import (  # noqa: E402
    render_v2_json_schema_text,
    render_v2_json_schemas,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when committed schemas differ from a clean render",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = REPO_ROOT / "schemas" / "agent-review" / "v2"
    rendered = {
        filename: render_v2_json_schema_text(schema)
        for filename, schema in render_v2_json_schemas().items()
    }
    if args.check:
        stale: list[str] = []
        for filename, expected in rendered.items():
            path = output_dir / filename
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(filename)
        unexpected = (
            sorted(path.name for path in output_dir.glob("*.schema.json") if path.name not in rendered)
            if output_dir.is_dir()
            else []
        )
        if stale or unexpected:
            for filename in stale:
                print(f"stale or missing schema: {filename}", file=sys.stderr)
            for filename in unexpected:
                print(f"unexpected schema: {filename}", file=sys.stderr)
            return 1
        print("AgentReview v2 schemas are byte-identical.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in rendered.items():
        (output_dir / filename).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
