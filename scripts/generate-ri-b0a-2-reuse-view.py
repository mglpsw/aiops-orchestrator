#!/usr/bin/env python3
"""Regenerate docs/generated/RI_B0A_2_REUSE_REFERENCE.md from
config/ri/ri-b0a-2-reuse-manifest.json (#119.2)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ri_b0a import load_reuse_manifest, render_reuse_view  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "config" / "ri" / "ri-b0a-2-reuse-manifest.json"
VIEW_PATH = REPO_ROOT / "docs" / "generated" / "RI_B0A_2_REUSE_REFERENCE.md"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the committed view differs from a clean render",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    result = load_reuse_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    if not result.ok:
        for error in result.errors:
            print(f"manifest invalid: {error}", file=sys.stderr)
        return 1

    rendered = render_reuse_view(result)

    if args.check:
        existing = VIEW_PATH.read_text(encoding="utf-8") if VIEW_PATH.exists() else None
        if existing != rendered:
            print(f"{VIEW_PATH} is stale -- regenerate with this script (no --check)", file=sys.stderr)
            return 1
        print("generated view is byte-identical to the manifest.")
        return 0

    VIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    VIEW_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {VIEW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
