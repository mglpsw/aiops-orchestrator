#!/usr/bin/env python3
"""#200-G2 real-source differential oracle.

A hand-written corpus alone is not sufficient evidence for the safe review
material disposition engine -- this is exactly how the #277 lineage passed
internally and failed externally twice (see
docs/checkpoints/AGENT_REVIEW_V2_200G2_SAFE_REVIEW_MATERIAL.md). This script
runs the engine across this repository's own `app/`, `scripts/`, and
`tests/` Python source trees (not just `app/` -- #277's own regression test
was found mis-scoped for missing `scripts/`/`tests/`) and reports, per
file and in aggregate:

    files_scanned, lines_examined, changed_lines, parseability_regressions

plus the full list of changed-line diffs so each one can be classified by
hand as expected (a real fixture/example secret string, most of them inside
redaction.py's/tests' own documentation of secret shapes) or unexpected (a
false-positive touching ordinary code) -- recorded in the checkpoint with
exact counts, per the mission.

Read-only: writes nothing back to the source tree. Not wired into CI; this
is evidence-gathering for the PR, not a regression gate.
"""

from __future__ import annotations

import ast
import difflib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.redaction import RedactionState, redact_text  # noqa: E402

SCAN_ROOTS = ("app", "scripts", "tests")
EXCLUDE_DIR_NAMES = {"__pycache__", ".git", "node_modules", ".venv"}


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


def parses(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def main() -> int:
    files = iter_python_files()
    total_lines = 0
    total_changed_lines = 0
    parseability_regressions: list[dict] = []
    changed_line_records: list[dict] = []

    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        state = RedactionState()
        transformed = redact_text(original, state)

        original_lines = original.split("\n")
        transformed_lines = transformed.split("\n")
        total_lines += len(original_lines)

        if transformed != original:
            # An LCS-based opcode diff (not positional index comparison):
            # a multi-line match that collapses N source lines into 1 (the
            # private-key block is exactly this shape) must not be counted
            # as "every following line changed" just because everything
            # after it shifted by an offset -- that inflates the count by
            # orders of magnitude and hides the real signal (see checkpoint
            # for the raw index-based count this replaced, kept there as a
            # named methodology note).
            matcher = difflib.SequenceMatcher(a=original_lines, b=transformed_lines, autojunk=False)
            for tag, a_lo, a_hi, b_lo, b_hi in matcher.get_opcodes():
                if tag == "equal":
                    continue
                before_block = "\n".join(original_lines[a_lo:a_hi])
                after_block = "\n".join(transformed_lines[b_lo:b_hi])
                block_len = max(a_hi - a_lo, b_hi - b_lo, 1)
                total_changed_lines += block_len
                changed_line_records.append(
                    {
                        "file": str(path.relative_to(REPO_ROOT)),
                        "line_no": a_lo + 1,
                        "op": tag,
                        "before": before_block[:400],
                        "after": after_block[:400],
                    }
                )

            original_parses = parses(original)
            transformed_parses = parses(transformed)
            if original_parses and not transformed_parses:
                parseability_regressions.append(
                    {
                        "file": str(path.relative_to(REPO_ROOT)),
                    }
                )

    report = {
        "files_scanned": len(files),
        "lines_examined": total_lines,
        "changed_lines": total_changed_lines,
        "files_with_changes": len({r["file"] for r in changed_line_records}),
        "parseability_regressions": parseability_regressions,
        "changed_line_records": changed_line_records,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
