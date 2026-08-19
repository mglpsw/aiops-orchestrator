"""`#203` -- anchor-bound compiler for the target-pack CURRENT truth carrier.

INTERNAL repository tooling/documentation infrastructure, not a public
AgentReview target contract: no target ever reads this module or its output
at install/validate time, so its identifiers use `format_id`, never
`contract_id`.

## Why this module exists

`#203-D0` and its Round 1/2 corrections (PR #245, frozen as a forensic
predecessor after `STOP_REDESIGN`) tried to make Markdown itself
machine-verifiable by parsing CURRENT-status prose with progressively wider
grammars. Every round required reconstructing one more implicit dimension
from prose -- subject, membership, polarity, role, cardinality, set identity,
temporal epoch -- and the residual kept moving. That is the signature of a
*view* being used as a *carrier*. This module inverts the relation: it
compiles one small structured state from real authorities, and Markdown
becomes a generated VIEW of that state, never a place state is inferred from.

## Authority model -- never derive one authority from another

```
normative manual   -- docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md
                       §4's structured `declared[]` block: the INTENDED total
                       CLI surface. The only product datum a human writes.

anchor-derived      -- read via `git show <anchor>:<path>` + `ast.parse`,
                       NEVER by importing or executing anchor code:
                         - canonical  = CLI subcommands argparse actually
                                        registers AT THE ANCHOR
                         - deferred   = declared_surface - canonical
                         - validate_inventory (total / locally_evaluable /
                                        permanently_unavailable names)

declared once       -- config/agent-review/target-pack-current-inputs.json:
                       implementation_anchor, reconciliation.reconciled_at,
                       historical_evidence records (tested_sha, canonical_sha,
                       suite counts, evidence_ref)

git-derived         -- anchor.committed_at, tested/canonical tree SHAs,
                       canonicalization_relation (never hand-declared)

generated           -- the compiled JSON this module produces, and every
                       Markdown CURRENT block rendered from it
```

`declared_surface` is read from the **working tree** (it is the successor
PR's own normative edit, not yet present at the anchor -- see the bootstrap
migration proof in the generator script). `canonical` and `validate_inventory`
are read from **the anchor's git blobs**, never the working tree, so a
candidate branch can never republish its own facts as canonical (the defect
this whole workstream exists to remove).

## Epistemic ceiling

This module proves: the normative surface parses; `canonical` is a subset of
`declared`; the compiled counts match the anchor's own static authorities;
historical evidence SHAs exist and are internally consistent. It does **not**
prove that `implementation_anchor` equals live GitHub `master` -- that is a
forge fact, checked once by the generator script before publication, never by
this module.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# --- Errors -----------------------------------------------------------


class TargetPackCurrentStateError(Exception):
    """Raised for any diagnosable defect in inputs, the normative surface,
    an anchor's static authorities, or their mutual consistency. Never
    silently downgraded to a partial/best-effort result -- fail closed."""


# --- Git primitives -- static, read-only, never execute anchor code ----

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_full_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


def git_commit_exists(repo_root: Path, sha: str) -> bool:
    if not is_full_sha(sha):
        return False
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return proc.returncode == 0


def git_commit_committed_at(repo_root: Path, sha: str) -> datetime:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%cI", sha],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    stamp = proc.stdout.strip()
    if not stamp:
        raise TargetPackCurrentStateError(f"git produced no committer date for {sha!r}")
    return datetime.fromisoformat(stamp)


def git_tree_sha(repo_root: Path, sha: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", f"{sha}^{{tree}}"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    tree = proc.stdout.strip()
    if not tree:
        raise TargetPackCurrentStateError(f"git produced no tree sha for {sha!r}")
    return tree


def read_anchor_blob(repo_root: Path, anchor_sha: str, relative_path: str) -> str:
    """`git show <anchor>:<path>` -- static blob content only. Never
    `importlib`, never `exec`, never adds the anchor's tree to `sys.path`.
    The anchor commit may be an ancestor of a later, unrelated HEAD; this
    reads exactly the blob that commit recorded, regardless of the current
    working tree."""

    proc = subprocess.run(
        ["git", "show", f"{anchor_sha}:{relative_path}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TargetPackCurrentStateError(
            f"could not read {relative_path!r} at anchor {anchor_sha!r}: {proc.stderr.strip()}"
        )
    return proc.stdout


BlobReader = Callable[[str, str], str]
CommittedAtReader = Callable[[str], datetime]
TreeShaReader = Callable[[str], str]
CommitExistsChecker = Callable[[str], bool]


# --- Static AST extraction ---------------------------------------------


def extract_cli_subcommands(source: str) -> frozenset[str]:
    """Every `sub.add_parser("<name>", ...)` registration, found
    structurally (any `*.add_parser(...)` call whose first positional
    argument is a string literal) -- works regardless of line wrapping,
    unlike a line-based scan, which silently misses a multi-line call and
    would have under-reported `validate` at this project's own anchor."""

    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return frozenset(names)


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Every top-level `NAME = "literal"` or `NAME: T = "literal"` -- the
    symbol table `VALIDATE_CHECK_ORDER_V2`/`UNVALIDATED_CAPABILITIES_V2`
    reference by name rather than repeating string literals."""

    consts: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            consts[target.id] = value.value
    return consts


def _module_level_value(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
            return node.value
    raise TargetPackCurrentStateError(f"no module-level assignment of {name!r} found")


def _resolve_name_sequence(value: ast.expr, consts: dict[str, str], *, owner: str) -> tuple[str, ...]:
    if not isinstance(value, (ast.Tuple, ast.List)):
        raise TargetPackCurrentStateError(f"{owner} is not a tuple/list literal; not statically representable")
    resolved: list[str] = []
    for element in value.elts:
        if not isinstance(element, ast.Name):
            raise TargetPackCurrentStateError(f"{owner} contains a non-Name element; not statically representable")
        if element.id not in consts:
            raise TargetPackCurrentStateError(f"{owner} references undefined constant {element.id!r}")
        resolved.append(consts[element.id])
    return tuple(resolved)


@dataclass(frozen=True)
class ValidateAuthorityV1:
    total: int
    locally_evaluable: int
    permanently_unavailable: frozenset[str]


def extract_validate_authority(source: str) -> ValidateAuthorityV1:
    """Statically extracts the validate check inventory from
    `target_pack_validate_v2.py`'s own `VALIDATE_CHECK_ORDER_V2` and
    `UNVALIDATED_CAPABILITIES_V2` module constants -- never imports the
    module, never falls back to a hardcoded 17/11/6, and fails closed the
    instant either constant stops being a plain tuple-of-Names (or
    tuple-of-(Name,Name)-pairs) literal."""

    tree = ast.parse(source)
    consts = _module_string_constants(tree)

    order_value = _module_level_value(tree, "VALIDATE_CHECK_ORDER_V2")
    check_order = _resolve_name_sequence(order_value, consts, owner="VALIDATE_CHECK_ORDER_V2")

    unavailable_value = _module_level_value(tree, "UNVALIDATED_CAPABILITIES_V2")
    if not isinstance(unavailable_value, (ast.Tuple, ast.List)):
        raise TargetPackCurrentStateError("UNVALIDATED_CAPABILITIES_V2 is not a tuple/list literal")
    unavailable_names: list[str] = []
    for pair in unavailable_value.elts:
        if not isinstance(pair, ast.Tuple) or len(pair.elts) != 2 or not isinstance(pair.elts[0], ast.Name):
            raise TargetPackCurrentStateError("UNVALIDATED_CAPABILITIES_V2 contains a non-(Name, ...) pair")
        name_node = pair.elts[0]
        if name_node.id not in consts:
            raise TargetPackCurrentStateError(f"UNVALIDATED_CAPABILITIES_V2 references undefined constant {name_node.id!r}")
        unavailable_names.append(consts[name_node.id])

    unavailable = frozenset(unavailable_names)
    if len(unavailable_names) != len(unavailable):
        raise TargetPackCurrentStateError("UNVALIDATED_CAPABILITIES_V2 contains a duplicate dimension name")
    if not unavailable <= set(check_order):
        raise TargetPackCurrentStateError("UNVALIDATED_CAPABILITIES_V2 names a dimension absent from VALIDATE_CHECK_ORDER_V2")

    return ValidateAuthorityV1(
        total=len(check_order),
        locally_evaluable=len(check_order) - len(unavailable),
        permanently_unavailable=unavailable,
    )


# --- Normative surface (working tree, not anchor) -----------------------

NORMATIVE_SURFACE_FORMAT_ID_V1 = "aiops.agent-review.target-pack-surface.v1"
NORMATIVE_SURFACE_SLOT_ID = "target-pack-current.spec.lifecycle-prose"
_NORMATIVE_BEGIN = "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->"
_NORMATIVE_END = "<!-- END NORMATIVE: target-pack-surface-v1 -->"


def _strict_json_loads(text: str, *, owner: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise TargetPackCurrentStateError(f"{owner}: duplicate JSON key {key!r}")
            seen[key] = value
        return seen

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise TargetPackCurrentStateError(f"{owner}: invalid JSON: {exc}") from exc


def extract_declared_surface(spec_text: str) -> frozenset[str]:
    """Reads the ONE normative structured block in §4 -- the only
    hand-maintained product datum in this whole pipeline. `canonical` and
    `deferred` are never declared; they are derived from this surface and
    the anchor's own argparse registrations."""

    begin_count = spec_text.count(_NORMATIVE_BEGIN)
    if begin_count == 0:
        raise TargetPackCurrentStateError(f"normative block start marker {_NORMATIVE_BEGIN!r} not found")
    if begin_count > 1:
        raise TargetPackCurrentStateError(
            f"normative block start marker {_NORMATIVE_BEGIN!r} is not unique ({begin_count} occurrences)"
        )
    end_count = spec_text.count(_NORMATIVE_END)
    if end_count != 1:
        raise TargetPackCurrentStateError(
            f"normative block end marker {_NORMATIVE_END!r} occurs {end_count} times, expected exactly 1"
        )
    after = spec_text.split(_NORMATIVE_BEGIN, 1)[1]
    region = after.split(_NORMATIVE_END, 1)[0]

    fence_matches = re.findall(r"```json\n(.*?)```", region, flags=re.DOTALL)
    if len(fence_matches) != 1:
        raise TargetPackCurrentStateError(
            f"expected exactly one fenced json block inside the normative markers, found {len(fence_matches)}"
        )
    doc = _strict_json_loads(fence_matches[0], owner="normative surface block")
    if not isinstance(doc, dict):
        raise TargetPackCurrentStateError("normative surface block is not a JSON object")
    if set(doc.keys()) != {"format_id", "declared"}:
        raise TargetPackCurrentStateError(f"normative surface block has unexpected keys: {sorted(doc.keys())}")
    if doc.get("format_id") != NORMATIVE_SURFACE_FORMAT_ID_V1:
        raise TargetPackCurrentStateError(f"normative surface format_id mismatch: {doc.get('format_id')!r}")
    declared = doc.get("declared")
    if not isinstance(declared, list) or not declared:
        raise TargetPackCurrentStateError("normative surface 'declared' must be a non-empty JSON array")
    if not all(isinstance(name, str) and name for name in declared):
        raise TargetPackCurrentStateError("normative surface 'declared' must contain only non-empty strings")
    if len(declared) != len(set(declared)):
        raise TargetPackCurrentStateError("normative surface 'declared' contains a duplicate name")
    return frozenset(declared)


# --- Non-derivable inputs ------------------------------------------------

CURRENT_INPUTS_FORMAT_ID_V1 = "aiops.agent-review.target-pack-current-inputs.v1"

_RFC3339_OFFSET_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _parse_rfc3339_offset_aware(value: object, *, owner: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339_OFFSET_RE.match(value):
        raise TargetPackCurrentStateError(f"{owner}: not an RFC3339 timestamp with an explicit offset: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TargetPackCurrentStateError(f"{owner}: timestamp has no offset: {value!r}")
    return parsed


@dataclass(frozen=True)
class EvidenceRefV1:
    kind: str
    sha: str


@dataclass(frozen=True)
class HistoricalEvidenceRecordV1:
    pr: int
    tested_sha: str
    canonical_sha: str
    suite_passed: int
    suite_skipped: int
    evidence_class: str
    evidence_ref: EvidenceRefV1


@dataclass(frozen=True)
class CurrentInputsV1:
    implementation_anchor: str
    reconciled_at: datetime
    historical_evidence: tuple[HistoricalEvidenceRecordV1, ...]


def _require_keys(obj: dict, expected: set[str], *, owner: str) -> None:
    if set(obj.keys()) != expected:
        extra = sorted(set(obj.keys()) - expected)
        missing = sorted(expected - set(obj.keys()))
        raise TargetPackCurrentStateError(f"{owner}: unexpected keys={extra} missing={missing}")


def load_current_inputs(
    path: Path,
    *,
    commit_exists: CommitExistsChecker,
) -> CurrentInputsV1:
    text = path.read_text(encoding="utf-8")
    doc = _strict_json_loads(text, owner=str(path))
    if not isinstance(doc, dict):
        raise TargetPackCurrentStateError(f"{path}: top level must be a JSON object")
    _require_keys(doc, {"format_id", "implementation_anchor", "reconciliation", "historical_evidence"}, owner=str(path))

    if doc.get("format_id") != CURRENT_INPUTS_FORMAT_ID_V1:
        raise TargetPackCurrentStateError(f"{path}: format_id mismatch: {doc.get('format_id')!r}")

    anchor = doc.get("implementation_anchor")
    if not is_full_sha(anchor):
        raise TargetPackCurrentStateError(f"{path}: implementation_anchor is not a 40-hex lowercase sha: {anchor!r}")
    if not commit_exists(anchor):
        raise TargetPackCurrentStateError(f"{path}: implementation_anchor {anchor!r} does not exist as a commit")

    reconciliation = doc.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise TargetPackCurrentStateError(f"{path}: 'reconciliation' must be a JSON object")
    _require_keys(reconciliation, {"reconciled_at"}, owner=f"{path}:reconciliation")
    reconciled_at = _parse_rfc3339_offset_aware(reconciliation.get("reconciled_at"), owner=f"{path}:reconciliation.reconciled_at")

    evidence_raw = doc.get("historical_evidence")
    if not isinstance(evidence_raw, list):
        raise TargetPackCurrentStateError(f"{path}: 'historical_evidence' must be a JSON array")

    records: list[HistoricalEvidenceRecordV1] = []
    for i, raw in enumerate(evidence_raw):
        owner = f"{path}:historical_evidence[{i}]"
        if not isinstance(raw, dict):
            raise TargetPackCurrentStateError(f"{owner}: must be a JSON object")
        _require_keys(
            raw,
            {"pr", "tested_sha", "canonical_sha", "suite", "evidence_class", "evidence_ref"},
            owner=owner,
        )
        pr = raw.get("pr")
        if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
            raise TargetPackCurrentStateError(f"{owner}.pr must be a positive integer, not a bool: {pr!r}")

        tested_sha = raw.get("tested_sha")
        canonical_sha = raw.get("canonical_sha")
        for label, sha in (("tested_sha", tested_sha), ("canonical_sha", canonical_sha)):
            if not is_full_sha(sha):
                raise TargetPackCurrentStateError(f"{owner}.{label} is not a 40-hex lowercase sha: {sha!r}")
            if not commit_exists(sha):
                raise TargetPackCurrentStateError(f"{owner}.{label} {sha!r} does not exist as a commit")

        suite = raw.get("suite")
        if not isinstance(suite, dict):
            raise TargetPackCurrentStateError(f"{owner}.suite must be a JSON object")
        _require_keys(suite, {"passed", "skipped"}, owner=f"{owner}.suite")
        passed, skipped = suite.get("passed"), suite.get("skipped")
        for label, val in (("passed", passed), ("skipped", skipped)):
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise TargetPackCurrentStateError(f"{owner}.suite.{label} must be a non-negative integer, not a bool: {val!r}")

        evidence_class = raw.get("evidence_class")
        if not isinstance(evidence_class, str) or not evidence_class:
            raise TargetPackCurrentStateError(f"{owner}.evidence_class must be a non-empty string")

        evidence_ref_raw = raw.get("evidence_ref")
        if not isinstance(evidence_ref_raw, dict):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref must be a JSON object")
        _require_keys(evidence_ref_raw, {"kind", "sha"}, owner=f"{owner}.evidence_ref")
        ref_kind, ref_sha = evidence_ref_raw.get("kind"), evidence_ref_raw.get("sha")
        if not isinstance(ref_kind, str) or not ref_kind:
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.kind must be a non-empty string")
        if not is_full_sha(ref_sha):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha is not a 40-hex lowercase sha: {ref_sha!r}")
        if ref_kind == "git_commit_message" and ref_sha != tested_sha:
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha must equal tested_sha for kind=git_commit_message")
        if not commit_exists(ref_sha):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha {ref_sha!r} does not exist as a commit")

        records.append(
            HistoricalEvidenceRecordV1(
                pr=pr, tested_sha=tested_sha, canonical_sha=canonical_sha,
                suite_passed=passed, suite_skipped=skipped,
                evidence_class=evidence_class,
                evidence_ref=EvidenceRefV1(kind=ref_kind, sha=ref_sha),
            )
        )

    return CurrentInputsV1(
        implementation_anchor=anchor,
        reconciled_at=reconciled_at,
        historical_evidence=tuple(records),
    )


# --- Compiled state -------------------------------------------------------


@dataclass(frozen=True)
class HistoricalEvidenceStateV1:
    pr: int
    tested_sha: str
    canonical_sha: str
    suite_passed: int
    suite_skipped: int
    evidence_class: str
    evidence_ref_kind: str
    evidence_ref_sha: str
    canonicalization_relation: str  # derived, never declared


@dataclass(frozen=True)
class TargetPackCurrentStateV1:
    format_id: str
    declared_surface: frozenset[str]
    canonical: frozenset[str]
    deferred: frozenset[str]
    validate_total: int
    validate_locally_evaluable: int
    validate_permanently_unavailable: frozenset[str]
    implementation_anchor: str
    anchor_committed_at: datetime
    reconciled_at: datetime
    historical_evidence: tuple[HistoricalEvidenceStateV1, ...]


COMPILED_STATE_FORMAT_ID_V1 = "aiops.agent-review.target-pack-current-state.v1"


def compile_current_state(
    *,
    inputs: CurrentInputsV1,
    declared_surface: frozenset[str],
    read_blob: BlobReader,
    committed_at: CommittedAtReader,
    tree_sha: TreeShaReader,
) -> TargetPackCurrentStateV1:
    """Pure given its callables: no filesystem/subprocess access happens
    inside this function itself, so unit tests can inject fixture readers
    without real git commits, while the generator script wires real
    anchor-bound git access."""

    anchor = inputs.implementation_anchor

    cli_source = read_blob(anchor, "scripts/agent-review-target-pack-v2.py")
    canonical = extract_cli_subcommands(cli_source)
    if not canonical <= declared_surface:
        raise TargetPackCurrentStateError(
            f"canonical_not_subset_declared: anchor {anchor!r} exposes {sorted(canonical - declared_surface)} "
            f"not present in the normative declared surface"
        )
    deferred = declared_surface - canonical

    validate_source = read_blob(anchor, "app/agent_review/target_pack_validate_v2.py")
    authority = extract_validate_authority(validate_source)

    anchor_committed_at = committed_at(anchor)
    if inputs.reconciled_at < anchor_committed_at:
        raise TargetPackCurrentStateError(
            f"reconciled_at {inputs.reconciled_at.isoformat()!r} precedes the implementation anchor's own "
            f"committed_at {anchor_committed_at.isoformat()!r} -- observation time cannot predate the event "
            f"it observes"
        )

    evidence_states: list[HistoricalEvidenceStateV1] = []
    for record in inputs.historical_evidence:
        tested_tree = tree_sha(record.tested_sha)
        canonical_tree = tree_sha(record.canonical_sha)
        relation = "tree_identical" if tested_tree == canonical_tree else "not_tree_identical"
        evidence_states.append(
            HistoricalEvidenceStateV1(
                pr=record.pr, tested_sha=record.tested_sha, canonical_sha=record.canonical_sha,
                suite_passed=record.suite_passed, suite_skipped=record.suite_skipped,
                evidence_class=record.evidence_class,
                evidence_ref_kind=record.evidence_ref.kind, evidence_ref_sha=record.evidence_ref.sha,
                canonicalization_relation=relation,
            )
        )

    return TargetPackCurrentStateV1(
        format_id=COMPILED_STATE_FORMAT_ID_V1,
        declared_surface=declared_surface,
        canonical=canonical,
        deferred=deferred,
        validate_total=authority.total,
        validate_locally_evaluable=authority.locally_evaluable,
        validate_permanently_unavailable=authority.permanently_unavailable,
        implementation_anchor=anchor,
        anchor_committed_at=anchor_committed_at,
        reconciled_at=inputs.reconciled_at,
        historical_evidence=tuple(evidence_states),
    )


def compiled_state_to_json_dict(state: TargetPackCurrentStateV1, *, source_inputs_path: str) -> dict:
    """The single serialization site -- the generator's `write` and
    `--check` modes both call this, so there is no second place the JSON
    shape could drift from what the compiler actually produced."""

    return {
        "format_id": state.format_id,
        "generated": {
            "generator": "target-pack-current-state-v1",
            "source_inputs": source_inputs_path,
            "implementation_anchor": state.implementation_anchor,
        },
        "state": {
            "surface": {
                "declared": sorted(state.declared_surface),
                "canonical": sorted(state.canonical),
                "deferred": sorted(state.deferred),
            },
            "validate_inventory": {
                "total": state.validate_total,
                "locally_evaluable": state.validate_locally_evaluable,
                "permanently_unavailable": sorted(state.validate_permanently_unavailable),
            },
            "temporal": {
                "implementation_anchor": state.implementation_anchor,
                "anchor_committed_at": state.anchor_committed_at.isoformat(),
                "reconciled_at": state.reconciled_at.isoformat(),
            },
            "historical_evidence": [
                {
                    "pr": e.pr,
                    "tested_sha": e.tested_sha,
                    "canonical_sha": e.canonical_sha,
                    "suite": {"passed": e.suite_passed, "skipped": e.suite_skipped},
                    "evidence_class": e.evidence_class,
                    "evidence_ref": {"kind": e.evidence_ref_kind, "sha": e.evidence_ref_sha},
                    "canonicalization_relation": e.canonicalization_relation,
                }
                for e in state.historical_evidence
            ],
        },
    }


def render_compiled_json(state: TargetPackCurrentStateV1, *, source_inputs_path: str) -> str:
    payload = compiled_state_to_json_dict(state, source_inputs_path=source_inputs_path)
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
