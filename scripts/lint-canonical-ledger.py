#!/usr/bin/env python3
"""Structural invariant linter for the canonical backlog/CAEM-reuse ledger.

Read-only, offline, deterministic. This does NOT verify whether any claim in
the ledger is currently TRUE against the forge -- that is a materially
different question (see `--audit-live` below) and CI must not depend on live
network state for a deterministic gate (CAEM P020: gates proportional to
impact; a syntactic-invariant check is cheap and should run on every commit,
a live-forge cross-check is expensive/flaky and must not block CI).

What this DOES enforce, mechanically, on every commit: the CLASS of defect
that repeatedly recurred in `docs/checkpoints/
AGENT_REVIEW_CANONICAL_BACKLOG_AND_CAEM_REUSE_LEDGER.md` across PR #318's
three correction rounds and PR #325's own commit history (see #324). Per
CAEM P022 ("subject-identity change stales evidence by default"), the ledger
must never assert a literal current-subject fact (a head SHA, a Codex
verdict, an exact witness count) as though it stayed true across a rebase --
it may only assert it as an explicitly HISTORICAL, epoch-bound record, or
defer it entirely to the owning PR/issue's own live state.

Rules (each independently mutation-tested by
tests/test_canonical_ledger_lint.py -- see that file for the RED fixture
that proves each rule is load-bearing, not merely present):

  R1 DUPLICATE_LIFECYCLE_CLAIM
     The same issue number appears in more than one YAML block in this
     file (as a top-level `issue_N:` mapping key, a compact `issue_N:
     {...}` entry, or a list entry `- issue: N`), AND more than one of
     those occurrences carries a field from LIFECYCLE_CLAIM_FIELD_NAMES
     (disposition, qualification_status, blocking, next_gate,
     current_status, implementation_status, codex_status, final_head,
     ci_status). Appearing in more than one block is legitimate on its
     own -- Section 5's per-slice CAEM-reuse-evidence row is a genuinely
     distinct axis from Section 1's roadmap row for the same issue, and
     duplicating the ISSUE NUMBER as a cross-reference key is fine.
     What is never legitimate is two independently-editable copies of
     the SAME lifecycle fact (e.g. Section 1's `disposition` and
     Section 5's `current_status` both separately asserting whether the
     issue is done), because nothing then keeps them in sync -- this is
     exactly the #312 defect PR #318's own correction missed on its
     first pass.

  R2 STALE_LINE_LOCATOR
     A backtick-quoted `:NNN`-shaped site locator (the convention this repo
     uses for "line N of file X") appears anywhere in the document. A line
     number drifts on the next edit; a stable semantic site name does not.

  R3 ORPHANED_CURRENT_SHA
     A full 40-hex object id appears as the value of a CURRENT-axis field
     (`final_head`, `codex_status`, `ci_status`, `next_gate`,
     `qualification_status`, `current_status`) without that line also
     containing the literal marker `HISTORICAL` (case-insensitive). A
     current-axis field must never pin a literal subject identity; it must
     either be forge-derived (no literal SHA) or explicitly re-labelled as
     historical evidence bound to a stated epoch.

  R4 UNQUALIFIED_EXACT_COUNT_CLAIM
     A phrase asserting an exact witness/test count as a standing fact
     ("kills exactly N witnesses", "N new tests total", "N witnesses",
     written as an absolute rather than deferred to the owning PR's own
     qualification comment). Exact counts are observations of a specific
     run (CAEM P021: evidence bound to identity/environment), not durable
     ledger content.

  R5 INTERNAL_LIFECYCLE_CONTRADICTION
     Within one issue block, `implementation_status` asserts completion
     (a value in COMPLETION_VALUES) while `disposition` or `current_status`
     asserts the work has not started (a value in NOT_STARTED_VALUES), or
     vice versa. Two axis-A/axis-D fields in the same block cannot
     simultaneously claim "done" and "not started" -- if they do, at least
     one is stale.

Usage:
    lint-canonical-ledger.py [--check] [PATH]
    lint-canonical-ledger.py --audit-live [PATH]   # advisory only, see below

`--check` (the CI-gated mode): run R1-R5, offline, deterministic. Exit
non-zero iff any violation is found. This is the only mode wired into
`scripts/ci_validate.sh`.

`--audit-live` (NOT CI-gated, informational only): for every issue number
referenced in the ledger, calls `gh issue view <N> --json state,stateReason`
and reports drift between the ledger's own axis-D claim (`disposition`,
`current_status`) and the live forge state (e.g. "ledger says ACTIVE_PARALLEL,
forge says CLOSED/COMPLETED"). This is exactly the class of drift that made
#312's and #313's ledger rows silently false after their owning PRs merged.
It is deliberately advisory and never gates CI: forge state changes
independently of any commit to this repository, so gating a deterministic
CI run on it would make merges non-reproducible based on external state
(CAEM P020 again -- this is disproportionate for a syntactic gate). Requires
`gh` on PATH and network access; reports GATE_UNAVAILABLE per-issue rather
than failing if either is absent (CAEM P024: never fabricate proof).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_CANONICAL_BACKLOG_AND_CAEM_REUSE_LEDGER.md"

# Axis-C ("current qualification"/"current lifecycle") field names. A field
# with one of these keys must never carry a literal, un-labelled subject SHA
# -- see R3's docstring above.
CURRENT_AXIS_FIELD_NAMES = frozenset(
    {
        "final_head",
        "codex_status",
        "ci_status",
        "next_gate",
        "qualification_status",
        "current_status",
    }
)

COMPLETION_VALUES = frozenset({"complete", "completed", "implemented", "integrated_completed"})
NOT_STARTED_VALUES = frozenset({"not_started", "n/a", "open_not_started"})

# Fields that assert a lifecycle/qualification FACT about an issue, as
# opposed to purely evidentiary/provenance fields (caem_predecessor,
# predecessor_truth_maker, agentreview_domain_delta, tests_ported,
# tests_rederived, authority_effect_in_aiops, qualification_transferred,
# title, track, canonical_property, ...) which may legitimately be
# repeated or extended across more than one block without creating a
# second authority for the same fact.
LIFECYCLE_CLAIM_FIELD_NAMES = frozenset(
    {
        "disposition",
        "qualification_status",
        "blocking",
        "next_gate",
        "current_status",
        "implementation_status",
        "codex_status",
        "final_head",
        "ci_status",
    }
)

FULL_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
STALE_LOCATOR_RE = re.compile(r"`:\d{2,5}`")
# "kills exactly N witnesses", "N new tests total", "N witnesses" as a bare
# absolute -- deliberately narrow (matched against the real historical
# phrasing) rather than a blanket ban on the word "witness", which appears
# legitimately in qualitative sentences this architecture explicitly wants
# to keep ("has a discriminating witness").
EXACT_COUNT_RE = re.compile(
    r"\bkills? exactly\s+\d+\b"
    r"|\b\d+\s+new\s+tests?\s+total\b"
    r"|\b(?:ten|nine|eight|seven|six|five|four|three|two|one)\s+witnesses\b",
    re.IGNORECASE,
)

YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)


@dataclass
class Finding:
    rule: str
    message: str
    line: int | None = None

    def render(self) -> str:
        loc = f":{self.line}" if self.line is not None else ""
        return f"{self.rule}{loc}: {self.message}"


@dataclass
class LintResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, rule: str, message: str, line: int | None = None) -> None:
        self.findings.append(Finding(rule, message, line))


def _extract_yaml_blocks(text: str) -> list[tuple[int, dict[str, Any] | list[Any]]]:
    """Every fenced ```yaml block in the document, with its starting line number."""
    blocks: list[tuple[int, dict[str, Any] | list[Any]]] = []
    for match in YAML_BLOCK_RE.finditer(text):
        start_line = text.count("\n", 0, match.start()) + 2  # first line inside the fence
        loaded = yaml.safe_load(match.group(1))
        if loaded is not None:
            blocks.append((start_line, loaded))
    return blocks


def _iter_issue_occurrences(blocks: list[tuple[int, dict[str, Any] | list[Any]]]) -> list[tuple[int, dict[str, Any]]]:
    """Every (issue_number, block_dict) occurrence, from either shape.

    Shape A: a top-level mapping key `issue_312: {...}` or compact
    `issue_213: {...}`.
    Shape B: a list entry `- issue: 312\\n  ...` inside any top-level list
    (e.g. the Section 5 `agentreview_property:` reuse ledger).
    """
    occurrences: list[tuple[int, dict[str, Any]]] = []
    for _start_line, doc in blocks:
        if isinstance(doc, dict):
            for key, value in doc.items():
                if isinstance(value, dict) and re.fullmatch(r"issue_(\d+)", str(key)):
                    num = int(re.fullmatch(r"issue_(\d+)", str(key)).group(1))  # type: ignore[union-attr]
                    occurrences.append((num, value))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "issue" in item:
                            raw = item["issue"]
                            if isinstance(raw, int):
                                occurrences.append((raw, item))
        elif isinstance(doc, list):
            for item in doc:
                if isinstance(item, dict) and "issue" in item and isinstance(item["issue"], int):
                    occurrences.append((item["issue"], item))
    return occurrences


def _rule_duplicate_lifecycle_claim(occurrences: list[tuple[int, dict[str, Any]]], result: LintResult) -> None:
    lifecycle_occurrence_count: dict[int, int] = {}
    fields_seen: dict[int, set[str]] = {}
    for num, block in occurrences:
        claimed = set(block.keys()) & LIFECYCLE_CLAIM_FIELD_NAMES
        if claimed:
            lifecycle_occurrence_count[num] = lifecycle_occurrence_count.get(num, 0) + 1
            fields_seen.setdefault(num, set()).update(claimed)
    for num, count in sorted(lifecycle_occurrence_count.items()):
        if count > 1:
            result.add(
                "R1_DUPLICATE_LIFECYCLE_CLAIM",
                f"issue #{num} has lifecycle-claim fields ({sorted(fields_seen[num])}) "
                f"independently asserted in {count} separate ledger blocks -- nothing "
                "keeps them in sync; consolidate to one location or make every location "
                "beyond the first a forge-derived pointer, never a second independent "
                "value. See #324's finding on issue #312.",
            )


def _rule_stale_line_locator(text: str, result: LintResult) -> None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        if STALE_LOCATOR_RE.search(line):
            result.add(
                "R2_STALE_LINE_LOCATOR",
                f"backtick-quoted `:N` line locator found -- replace with a stable "
                f"semantic site name: {line.strip()[:140]!r}",
                line=lineno,
            )


def _rule_orphaned_current_sha(blocks: list[tuple[int, dict[str, Any]]], text: str, result: LintResult) -> None:
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", stripped)
        if not match:
            continue
        field_name, value = match.groups()
        if field_name not in CURRENT_AXIS_FIELD_NAMES:
            continue
        if not FULL_SHA_RE.search(value):
            continue
        if "historical" in line.lower():
            continue
        result.add(
            "R3_ORPHANED_CURRENT_SHA",
            f"current-axis field `{field_name}` carries a literal 40-hex subject id "
            f"with no HISTORICAL label -- pin nothing here, or label it explicitly "
            f"historical and epoch-bound: {stripped[:160]!r}",
            line=lineno,
        )


def _rule_unqualified_exact_count(text: str, result: LintResult) -> None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        if EXACT_COUNT_RE.search(line):
            result.add(
                "R4_UNQUALIFIED_EXACT_COUNT_CLAIM",
                "exact witness/test count asserted as a standing ledger fact -- "
                "defer the number to the owning PR's own exact-head qualification "
                f"comment; state only that a discriminating witness exists: {line.strip()[:160]!r}",
                line=lineno,
            )


def _rule_internal_lifecycle_contradiction(occurrences: list[tuple[int, dict[str, Any]]], result: LintResult) -> None:
    for num, block in occurrences:
        impl = str(block.get("implementation_status", "")).strip().lower()
        disp = str(block.get("disposition", "")).strip().lower()
        cur = str(block.get("current_status", "")).strip().lower()
        impl_done = impl in COMPLETION_VALUES
        other_not_started = disp in NOT_STARTED_VALUES or cur in NOT_STARTED_VALUES
        impl_not_started = impl in NOT_STARTED_VALUES
        other_done = disp in COMPLETION_VALUES or cur in COMPLETION_VALUES
        if impl_done and other_not_started:
            result.add(
                "R5_INTERNAL_LIFECYCLE_CONTRADICTION",
                f"issue #{num}: implementation_status={impl!r} claims completion while "
                f"disposition/current_status claims not-started (disposition={disp!r}, "
                f"current_status={cur!r})",
            )
        elif impl_not_started and other_done:
            result.add(
                "R5_INTERNAL_LIFECYCLE_CONTRADICTION",
                f"issue #{num}: implementation_status={impl!r} claims not-started while "
                f"disposition/current_status claims completion (disposition={disp!r}, "
                f"current_status={cur!r})",
            )


def lint_text(text: str) -> LintResult:
    result = LintResult()
    blocks = _extract_yaml_blocks(text)
    occurrences = _iter_issue_occurrences(blocks)
    _rule_duplicate_lifecycle_claim(occurrences, result)
    _rule_stale_line_locator(text, result)
    _rule_orphaned_current_sha(blocks, text, result)
    _rule_unqualified_exact_count(text, result)
    _rule_internal_lifecycle_contradiction(occurrences, result)
    return result


def audit_live(text: str) -> list[str]:
    """Best-effort, advisory-only live-forge drift report. Never gates CI.

    Returns human-readable report lines. Uses `gh issue view <N> --json
    state,stateReason`; if `gh` is unavailable or the call fails, records
    GATE_UNAVAILABLE for that issue rather than inferring anything
    (CAEM P024).
    """
    lines: list[str] = []
    if shutil.which("gh") is None:
        return ["GATE_UNAVAILABLE: `gh` not found on PATH -- cannot audit live forge state."]

    blocks = _extract_yaml_blocks(text)
    occurrences = _iter_issue_occurrences(blocks)
    seen: set[int] = set()
    for num, block in occurrences:
        if num in seen:
            continue
        seen.add(num)
        disp = str(block.get("disposition", block.get("current_status", ""))).strip()
        try:
            proc = subprocess.run(
                ["gh", "issue", "view", str(num), "--json", "state,stateReason"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f"issue #{num}: GATE_UNAVAILABLE ({exc})")
            continue
        if proc.returncode != 0:
            lines.append(f"issue #{num}: GATE_UNAVAILABLE ({proc.stderr.strip()[:200]})")
            continue
        try:
            live = json.loads(proc.stdout)
        except json.JSONDecodeError:
            lines.append(f"issue #{num}: GATE_UNAVAILABLE (unparseable gh response)")
            continue
        live_state = live.get("state", "")
        live_reason = live.get("stateReason", "") or ""
        looks_active = disp and not any(
            token in disp.upper() for token in ("CLOSED", "COMPLETED", "MERGED", "INTEGRATED")
        )
        if live_state == "CLOSED" and looks_active:
            lines.append(
                f"DRIFT issue #{num}: ledger claims {disp!r}, live forge state is "
                f"CLOSED/{live_reason} -- ledger row is stale."
            )
        else:
            lines.append(f"issue #{num}: ledger={disp!r} live={live_state}/{live_reason} (no drift detected)")
    return lines


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--check", action="store_true", help="CI-gated mode: R1-R5, offline, deterministic")
    parser.add_argument(
        "--audit-live",
        action="store_true",
        help="advisory-only: cross-check disposition claims against live `gh issue view` state; never gates CI",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2
    text = path.read_text()

    if args.audit_live:
        for line in audit_live(text):
            print(line)
        return 0  # advisory only; never fails the invocation

    result = lint_text(text)
    if result.ok:
        print(f"canonical ledger structural invariants: clean ({path})")
        return 0
    for finding in result.findings:
        print(finding.render(), file=sys.stderr)
    print(f"canonical ledger structural invariants: {len(result.findings)} violation(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
