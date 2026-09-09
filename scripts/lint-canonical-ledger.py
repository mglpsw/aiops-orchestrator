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

Round-2 revision (post native-Codex + adversarial-lane review of the first
cut): the first version of this linter was itself an instance of exactly the
class it exists to prevent -- confident, green, and silently blind to
realistic ledger content. Every fix below closes a REPRODUCED false
negative, not a hypothetical one; see the regression corpus for the fixture
that reproduces each one against the pre-fix rule.

Rules (each independently mutation-tested by
tests/test_canonical_ledger_lint.py):

  R1 DUPLICATE_LIFECYCLE_CLAIM
     The same issue number's lifecycle facts are asserted in more than one
     place, via either of two independently-reproduced mechanisms:
       (a) cross-block: the number appears in 2+ separate YAML blocks, and
           2+ of those occurrences each carry AT LEAST ONE field from
           LIFECYCLE_CLAIM_FIELD_NAMES with a non-sentinel value -- not
           necessarily the SAME field name in both. Deliberately broader
           than "the identical field duplicated": two blocks asserting
           DIFFERENT current-state fields (Section 1's `disposition` and
           Section 5's `blocking`, say) are still two independently-
           editable statements about the same issue's current lifecycle
           that nothing keeps in sync, which is the actual property this
           rule protects. A FORGE_DERIVED-valued field makes no
           independent claim, so it does not count -- fixed after review
           found the tool's own remediation advice, "point beyond the
           first at FORGE_DERIVED", was previously flagged as a violation
           by this same rule;
       (b) same-block: a single YAML mapping literally repeats the same
           `issue_N:` key twice. `yaml.safe_load` silently keeps only the
           last occurrence (Python dict/last-key-wins semantics), so this
           is invisible to any check that only inspects the PARSED result
           -- detected here via `yaml.compose()`, which preserves duplicate
           mapping keys as distinct node pairs before construction discards
           them. This is the single most realistic vector: the real ledger
           keeps ~20+ issue blocks in a handful of large fenced sections,
           exactly the shape where a copy-paste duplicate key survives
           silently.

  R2 STALE_LINE_LOCATOR
     A backtick-quoted `:NNN`-shaped site locator (the convention this repo
     uses for "line N of file X") appears anywhere in the document.

  R3 ORPHANED_CURRENT_SHA
     A full 40-hex object id appears anywhere in the PARSED VALUE of a
     CURRENT-axis field (final_head, codex_status, ci_status, next_gate,
     qualification_status, current_status), for ANY issue occurrence found
     by the (now recursive, depth-independent) occurrence walker. Checking
     the parsed value rather than a raw-text line regex closes three
     reproduced evasions: a YAML folded/block scalar (`final_head: >-` /
     SHA on the next line), a list-valued field, and an uppercase-hex SHA.
     There is deliberately NO "HISTORICAL" comment escape hatch any more --
     a prior version allowed the bare substring "historical" anywhere on
     the same raw text line to suppress the finding, which a trailing
     comment could trivially spoof (reproduced: "# not historical at all,
     still current" disabled the check). Current-axis fields structurally
     must never carry a literal subject SHA at all under this
     architecture -- legitimate historical SHA citations belong in
     narrative/evidence fields (`correction_rounds[].finding`,
     `round_2_review`, ...), which are not in CURRENT_AXIS_FIELD_NAMES and
     were never in scope for this rule.

  R4 UNQUALIFIED_EXACT_COUNT_CLAIM
     A phrase asserting an exact witness/test/mutation count as a standing
     fact, scanned over each occurrence's own PARSED field values (not raw
     text) so it can exclude HISTORICAL_NARRATIVE_FIELD_NAMES
     (`correction_rounds`, `round_2_review`) the same way R3 excludes them
     from SHA scope -- a count describing what a SPECIFIC PAST correction
     round did, already epoch-labelled by its own `round: N` position, is
     not the defect this rule exists to catch; an exact count asserted as
     though it were still true of the current corpus is. Broadened from
     the original narrow phrase list (which missed the ledger's OWN
     realistic phrasings like "5 subclass witnesses" or "20 new tests") to
     a general `\\d+\\s+(new\\s+)?(witness(es)?|tests?)` pattern, plus the
     original absolute-phrase forms.

  R5 INTERNAL_LIFECYCLE_CONTRADICTION
     Within one issue block, `implementation_status` asserts completion
     while `disposition`/`current_status` asserts the work has not
     started, or vice versa. COMPLETION_VALUES/NOT_STARTED_VALUES now
     include this ledger's OWN documented taxonomy (`CLOSED_COMPLETED`,
     `COMPLETED_CLOSE_CANDIDATE*`, `OPEN_ARCHITECTURE_ONLY`,
     `OPEN_NOT_STARTED`, `ATTEMPTED_STOPPED*`, ...) rather than only the
     small generic vocabulary the first version invented -- a real stale
     row using the ledger's actual taxonomy previously produced no
     finding.

Usage:
    lint-canonical-ledger.py --check [PATH]
    lint-canonical-ledger.py --audit-live [PATH]

Exactly one of `--check` / `--audit-live` is required (the first version
silently ran `--check` logic by default even with neither flag given, which
made the flag meaningless; that is fixed).

`--check` (the CI-gated mode): run R1-R5, offline, deterministic. Exit
non-zero iff any violation is found, or if the document fails to parse as
YAML at all (fails closed, with a clear diagnostic rather than a raw
traceback).

`--audit-live` (NOT CI-gated, informational only): for every issue number
referenced in the ledger, calls `gh issue view <N> --json state,stateReason`
against the canonical repository explicitly (`-R mglpsw/aiops-orchestrator`
-- the first version omitted `-R` and so was silently vulnerable to
whatever repository happened to be the ambient `gh` context) and reports
drift between the ledger's own axis-D claim and the live forge state, in
BOTH directions: "ledger says active, forge is closed" AND "ledger says
closed, forge was reopened" (the first version only checked the former).
A field holding the literal sentinel value FORGE_DERIVED makes no
independent lifecycle claim and is correctly skipped rather than reported
as drifted. Never gates CI (CAEM P020: forge state changes independently of
any commit here). Reports GATE_UNAVAILABLE, never a fabricated verdict,
when `gh`/network is absent OR the document fails to parse (CAEM P024).
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
CANONICAL_REPO = "mglpsw/aiops-orchestrator"

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

# The literal sentinel this architecture uses for "no claim is made here;
# read the forge". Never a value R1/R3 should treat as a competing claim.
FORGE_DERIVED_SENTINEL = "FORGE_DERIVED"

# This ledger's OWN documented classification taxonomy (Section 1's header)
# plus the values actually observed in its rows, sorted into which side of
# "done" they mean. Deliberately generous rather than a small invented
# vocabulary -- R5 exists to catch REAL rows, and a vocabulary narrower than
# the document's own risks silently doing nothing.
COMPLETION_VALUES = frozenset(
    {
        "complete",
        "completed",
        "implemented",
        "integrated_completed",
        "closed_completed",
        "completed_close_candidate",
        "completed_close_candidate_pending_221",
        "post_merge_qualified",
    }
)
# Deliberately does NOT include "n/a": that means "field not applicable",
# a different concept than "not started" (latent conflation found by an
# independent adversarial lane -- `qualification_status: n/a` is the
# dominant real pattern in this ledger, used 6+ times, and none of those
# rows are contradictions).
NOT_STARTED_VALUES = frozenset(
    {
        "not_started",
        "open_not_started",
        "open_architecture_only",
        "architecture_only",
        "attempted_stopped",
    }
)

FULL_SHA_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")

# Two independently-necessary shapes, because the REAL historical defect
# (reproduced by an independent adversarial lane replaying every historical
# revision of this ledger's subject file) was never the backtick-wrapped
# form -- it was bare: "commondir :632, packed-refs :935, HEAD x3
# :616/:721/:968, objects/info/alternates :853" and "(:616)". A version of
# this rule that only matched `` `:NNN` `` would have fired zero times
# across all 15 historical revisions containing that defect -- verified.
#   (a) backtick-wrapped, this repo's own corrected-prose convention
#   (b) bare: an identifier-shaped word (3+ chars) immediately followed by
#       `:NNN`, OR `:NNN` in parens, OR `:NNN` chained via a leading `/`
#       (the "x3 :616/:721/:968" shape) -- excluding common short
#       prepositions ("on", "at", "via", "to") and network-context words
#       ("port", "listen", "bind", "address", "socket") that precede a
#       genuine bind-address/port number, e.g. "the Router listens on
#       `:8080`" or "bind :8080", which must NOT be flagged.
_LOCATOR_EXCLUDED_PRECEDING_WORDS = frozenset(
    {"on", "at", "via", "to", "port", "listen", "listens", "listening", "bind", "bound", "address", "socket"}
)
_BARE_LOCATOR_RE = re.compile(r"\b([A-Za-z][\w./-]{2,})\s+:(\d{2,5})\b|\(:(\d{2,5})\)|/:(\d{2,5})\b")
STALE_LOCATOR_RE = re.compile(r"`:\d{2,5}`")
EXACT_COUNT_RE = re.compile(
    r"\bkills?\s+exactly\s+(?:the\s+|all\s+)?\d+\b"
    r"|\bkilled\s+exactly\s+(?:the\s+|all\s+)?\d+\b"
    r"|\b\d+\s+(?:new\s+)?tests?\b"
    r"|\b\d+\s+(?:new\s+)?witnesses\b"
    r"|\bexactly\s+(?:the\s+|all\s+)?\d+\s+witness(?:es)?\b"
    r"|\b(?:ten|nine|eight|seven|six|five|four|three|two|one)\s+witnesses\b",
    re.IGNORECASE,
)
ISSUE_KEY_RE = re.compile(r"issue_(\d+)")

YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)


class LedgerParseError(Exception):
    """Raised when the document does not parse as valid YAML inside its
    fenced blocks. Both --check and --audit-live must fail closed on this,
    with a clear message, rather than a raw traceback or (worse) silently
    treating an unparseable block as though it had zero content."""


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


def _extract_yaml_blocks(text: str) -> list[str]:
    """Every fenced ```yaml block's raw text."""
    return [match.group(1) for match in YAML_BLOCK_RE.finditer(text)]


def _parse_yaml_blocks(raw_blocks: list[str]) -> list[Any]:
    parsed: list[Any] = []
    for raw in raw_blocks:
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise LedgerParseError(f"a fenced yaml block failed to parse: {exc}") from exc
        if loaded is not None:
            parsed.append(loaded)
    return parsed


def _coerce_issue_number(raw: object) -> int | None:
    if isinstance(raw, bool):  # bool is a subclass of int; never a real issue number
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _walk_for_occurrences(node: Any, occurrences: list[tuple[int, dict[str, Any]]]) -> None:
    """Recursive, depth-independent collection of every issue occurrence.

    Shape A: a mapping key matching `issue_N` whose value is itself a
    mapping, at ANY nesting depth (the first version only looked at the
    document's own top level).
    Shape B: any mapping with an `issue:` key, at any depth, whose value
    coerces to an int (accepting both `issue: 312` and `issue: "312"` --
    the first version silently dropped the latter).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict):
                match = ISSUE_KEY_RE.fullmatch(str(key))
                if match:
                    occurrences.append((int(match.group(1)), value))
                _walk_for_occurrences(value, occurrences)
            elif isinstance(value, list):
                _walk_for_occurrences(value, occurrences)
        if "issue" in node:
            num = _coerce_issue_number(node["issue"])
            if num is not None:
                occurrences.append((num, node))
    elif isinstance(node, list):
        for item in node:
            _walk_for_occurrences(item, occurrences)


def _iter_issue_occurrences(parsed_blocks: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    occurrences: list[tuple[int, dict[str, Any]]] = []
    for doc in parsed_blocks:
        _walk_for_occurrences(doc, occurrences)
    return occurrences


def _walk_compose_tree_for_duplicate_keys(node: yaml.Node | None, found: list[int]) -> None:
    """Duplicate `issue_N:` keys within the SAME YAML mapping.

    `yaml.compose()` builds the node tree WITHOUT constructing Python
    objects, so unlike `yaml.safe_load` it does not silently collapse two
    identical mapping keys into one (last-value-wins) -- both key/value
    pairs are still present as distinct entries in `MappingNode.value`.
    This is the only way to detect the single most realistic duplication
    shape: two `issue_312:` keys accidentally left in the same fenced
    section after an edit.
    """
    if node is None:
        return
    if isinstance(node, yaml.MappingNode):
        seen: dict[str, int] = {}
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode) and ISSUE_KEY_RE.fullmatch(str(key_node.value)):
                seen[key_node.value] = seen.get(key_node.value, 0) + 1
            _walk_compose_tree_for_duplicate_keys(value_node, found)
        for key, count in seen.items():
            if count > 1:
                found.append(int(ISSUE_KEY_RE.fullmatch(key).group(1)))  # type: ignore[union-attr]
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _walk_compose_tree_for_duplicate_keys(item, found)


def _find_same_block_duplicate_issue_keys(raw_blocks: list[str]) -> list[int]:
    found: list[int] = []
    for raw in raw_blocks:
        try:
            node = yaml.compose(raw)
        except yaml.YAMLError:
            continue  # already reported as a parse error by _parse_yaml_blocks
        _walk_compose_tree_for_duplicate_keys(node, found)
    return found


def _rule_duplicate_lifecycle_claim(
    occurrences: list[tuple[int, dict[str, Any]]],
    same_block_duplicates: list[int],
    result: LintResult,
) -> None:
    for num in sorted(set(same_block_duplicates)):
        result.add(
            "R1_DUPLICATE_LIFECYCLE_CLAIM",
            f"issue #{num} has the SAME `issue_{num}:` key repeated within one YAML "
            "mapping -- yaml.safe_load silently keeps only the last occurrence "
            "(Python last-key-wins), so a stale copy can sit right next to its "
            "replacement with nothing ever seeing it. Remove the duplicate key.",
        )

    lifecycle_occurrence_count: dict[int, int] = {}
    fields_seen: dict[int, set[str]] = {}
    for num, block in occurrences:
        claimed = {
            k
            for k in block.keys()
            if k in LIFECYCLE_CLAIM_FIELD_NAMES
            and str(block.get(k, "")).strip().upper() != FORGE_DERIVED_SENTINEL
        }
        if claimed:
            lifecycle_occurrence_count[num] = lifecycle_occurrence_count.get(num, 0) + 1
            fields_seen.setdefault(num, set()).update(claimed)
    for num, count in sorted(lifecycle_occurrence_count.items()):
        if count > 1:
            result.add(
                "R1_DUPLICATE_LIFECYCLE_CLAIM",
                f"issue #{num} has lifecycle-claim fields ({sorted(fields_seen[num])}) "
                f"independently asserted (with a non-{FORGE_DERIVED_SENTINEL} value) in "
                f"{count} separate ledger blocks -- nothing keeps them in sync; consolidate "
                f"to one location, or make every location beyond the first "
                f"`{FORGE_DERIVED_SENTINEL}`. See #324's finding on issue #312.",
            )


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


# A line mentioning bind-address/networking context is never a source-line
# locator, regardless of whether the number happens to be backtick-quoted --
# reproduced false positive: "the Router listens on `:8080`".
_NETWORK_CONTEXT_RE = re.compile(
    r"\b(?:listen(?:s|ing)?|bind(?:s|ing)?|bound|port|address|socket)\b", re.IGNORECASE
)


def _line_has_bare_locator(line: str) -> bool:
    for match in _BARE_LOCATOR_RE.finditer(line):
        preceding_word = match.group(1)
        if preceding_word is not None and preceding_word.lower() in _LOCATOR_EXCLUDED_PRECEDING_WORDS:
            continue
        return True
    return False


def _rule_stale_line_locator(text: str, result: LintResult) -> None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _NETWORK_CONTEXT_RE.search(line):
            continue
        if STALE_LOCATOR_RE.search(line) or _line_has_bare_locator(line):
            result.add(
                "R2_STALE_LINE_LOCATOR",
                f"line-number site locator found (backtick-quoted or bare) -- replace "
                f"with a stable semantic site name: {line.strip()[:140]!r}",
                line=lineno,
            )


def _stringify_field_value(value: Any) -> str:
    """Render a parsed YAML value (scalar, folded/block scalar, or list) as
    one string for substring/regex inspection -- this is what makes R3
    immune to a folded scalar or list-valued field hiding a SHA on a
    different physical line than the field name."""
    if isinstance(value, list):
        return " ".join(_stringify_field_value(item) for item in value)
    return str(value)


def _rule_orphaned_current_sha(occurrences: list[tuple[int, dict[str, Any]]], result: LintResult) -> None:
    for num, block in occurrences:
        for field_name in sorted(CURRENT_AXIS_FIELD_NAMES):
            if field_name not in block:
                continue
            rendered = _stringify_field_value(block[field_name])
            if FULL_SHA_RE.search(rendered):
                result.add(
                    "R3_ORPHANED_CURRENT_SHA",
                    f"issue #{num}: current-axis field `{field_name}` carries a literal "
                    f"40-hex subject id: {rendered[:160]!r}. Current-axis fields must "
                    f"never pin a literal subject identity -- use `{FORGE_DERIVED_SENTINEL}` "
                    "or move the citation to a narrative/evidence field "
                    "(e.g. `correction_rounds[].finding`).",
                )


# Fields whose content is legitimately EPOCH-BOUND historical narrative
# (already labelled by its own `round: N` position, exactly like R3's
# narrative-field exemption) rather than a standing current-state claim.
# An exact count describing what a SPECIFIC PAST correction round did is
# not the defect R4 exists to catch; an exact count asserted as though it
# were still true of the current corpus is.
HISTORICAL_NARRATIVE_FIELD_NAMES = frozenset({"correction_rounds", "round_2_review"})


def _rule_unqualified_exact_count(occurrences: list[tuple[int, dict[str, Any]]], result: LintResult) -> None:
    for num, block in occurrences:
        for key, value in block.items():
            if key in HISTORICAL_NARRATIVE_FIELD_NAMES:
                continue
            rendered = _stringify_field_value(value)
            match = EXACT_COUNT_RE.search(rendered)
            if match:
                result.add(
                    "R4_UNQUALIFIED_EXACT_COUNT_CLAIM",
                    f"issue #{num}, field `{key}`: exact witness/test count asserted as a "
                    "standing ledger fact -- defer the number to the owning PR's own "
                    "exact-head qualification comment; state only that a discriminating "
                    f"witness exists: {rendered[max(0, match.start() - 40):match.end() + 40]!r}",
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
    raw_blocks = _extract_yaml_blocks(text)
    parsed_blocks = _parse_yaml_blocks(raw_blocks)  # raises LedgerParseError, deliberately not caught here
    occurrences = _iter_issue_occurrences(parsed_blocks)
    same_block_duplicates = _find_same_block_duplicate_issue_keys(raw_blocks)
    _rule_duplicate_lifecycle_claim(occurrences, same_block_duplicates, result)
    _rule_stale_line_locator(text, result)
    _rule_orphaned_current_sha(occurrences, result)
    _rule_unqualified_exact_count(occurrences, result)
    _rule_internal_lifecycle_contradiction(occurrences, result)
    return result


def audit_live(text: str) -> list[str]:
    """Best-effort, advisory-only live-forge drift report. Never gates CI.

    Uses `gh issue view <N> --json state,stateReason` bound explicitly to
    CANONICAL_REPO via `-R` (never the ambient `gh` context). If `gh` is
    unavailable, the document fails to parse, or a specific lookup fails,
    reports GATE_UNAVAILABLE for the affected scope rather than inferring
    anything (CAEM P024) -- this function must never raise.
    """
    if shutil.which("gh") is None:
        return ["GATE_UNAVAILABLE: `gh` not found on PATH -- cannot audit live forge state."]

    try:
        raw_blocks = _extract_yaml_blocks(text)
        parsed_blocks = _parse_yaml_blocks(raw_blocks)
    except LedgerParseError as exc:
        return [f"GATE_UNAVAILABLE: document did not parse -- {exc}"]

    occurrences = _iter_issue_occurrences(parsed_blocks)
    lines: list[str] = []
    seen: set[int] = set()
    for num, block in occurrences:
        if num in seen:
            continue
        seen.add(num)
        disp = str(block.get("disposition", block.get("current_status", ""))).strip()
        if not disp or disp.upper() == FORGE_DERIVED_SENTINEL:
            lines.append(f"issue #{num}: ledger makes no independent lifecycle claim here (skipped)")
            continue
        try:
            proc = subprocess.run(
                ["gh", "issue", "view", str(num), "-R", CANONICAL_REPO, "--json", "state,stateReason"],
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
        looks_closed = any(token in disp.upper() for token in ("CLOSED", "COMPLETED", "MERGED", "INTEGRATED"))
        if live_state == "CLOSED" and not looks_closed:
            lines.append(
                f"DRIFT issue #{num}: ledger claims {disp!r} (active-shaped), live forge "
                f"state is CLOSED/{live_reason} -- ledger row is stale."
            )
        elif live_state == "OPEN" and looks_closed:
            lines.append(
                f"DRIFT issue #{num}: ledger claims {disp!r} (closed-shaped), live forge "
                f"state is OPEN -- issue was reopened after this row was written."
            )
        else:
            lines.append(f"issue #{num}: ledger={disp!r} live={live_state}/{live_reason} (no drift detected)")
    return lines


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=str(DEFAULT_LEDGER_PATH))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="CI-gated mode: R1-R5, offline, deterministic")
    mode.add_argument(
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

    try:
        result = lint_text(text)
    except LedgerParseError as exc:
        print(f"canonical ledger structural invariants: PARSE ERROR -- {exc}", file=sys.stderr)
        return 1
    if result.ok:
        print(f"canonical ledger structural invariants: clean ({path})")
        return 0
    for finding in result.findings:
        print(finding.render(), file=sys.stderr)
    print(f"canonical ledger structural invariants: {len(result.findings)} violation(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
