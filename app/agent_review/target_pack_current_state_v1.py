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
                         - canonical  = the ACTIVE top-level subcommands
                                        argparse actually registers AT THE
                                        ANCHOR (see "Active subject
                                        extraction" below)
                         - deferred   = declared_surface - canonical
                         - validate_inventory (total / locally_evaluable /
                                        permanently_unavailable names)

declared once       -- config/agent-review/target-pack-current-inputs.json:
                       implementation_anchor, reconciliation.reconciled_at,
                       historical_evidence records (canonical_sha,
                       evidence_ref -- see "Evidence provenance" below)

git-derived         -- anchor.committed_at; historical evidence suite counts
                       (derived from a canonical commit's own message, never
                       hand-declared -- see below)

gate, never state   -- anchor freshness relative to a canonical ref (see
                       "Anchor freshness" below); checked, never serialized

generated           -- the compiled JSON this module produces, and every
                       Markdown CURRENT block rendered from it
```

`declared_surface` is read from the **working tree** (it is the successor
PR's own normative edit, not yet present at the anchor -- see the bootstrap
migration proof in the generator script). `canonical` and `validate_inventory`
are read from **the anchor's git blobs**, never the working tree, so a
candidate branch can never republish its own facts as canonical (the defect
this whole workstream exists to remove).

## Active subject extraction, not `ast.walk` over the whole module

`extract_cli_subcommands` recognizes ONLY the direct, top-level
`<subparsers>.add_parser("name", ...)` statements inside the anchor's own
`_parse_args` function -- never a call anywhere else in the module. An
unrestricted `ast.walk` would classify a call sitting in an unused helper, a
dead conditional, or a nested parser as a shipped subcommand, publishing a
name argparse never actually exposes as "Canonical on `master`". A
registration found on the recognized subparsers variable but OUTSIDE a
direct top-level statement (nested control flow, helper indirection) fails
closed rather than being silently skipped or silently included.

## Anchor freshness, checked but never serialized

`verify_anchor_freshness` (called by the generator script, never by
`compile_current_state` itself) proves `implementation_anchor` is not stale
relative to a canonical ref (`refs/remotes/origin/master`) WITHOUT requiring
permanent equality -- `anchor == canonical_ref` would fail the instant this
very successor's own tooling/docs commit lands on `master`, despite no
runtime target-pack authority having moved. Freshness instead means: the
anchor is an ancestor of the canonical ref, AND every AUTHORITY-BEARING blob
(the CLI script, the validate module) is byte-identical between them. A
later docs/tooling-only descendant leaves CURRENT fresh; a change to either
authority path does not. The canonical ref's own SHA is deliberately never
part of the compiled state's serialized JSON -- freshness is a precondition
checked once before publication, not stable rendered content.

## Evidence provenance -- claims must be derivable, not merely bound

Earlier revisions accepted hand-declared `pr`, `recorded_tested_sha`, and
`suite` counts in `historical_evidence`, binding only the SHA identity, not
the CLAIM itself, to anything verifiable -- an editor could change the
suite counts or the PR number without the loader ever noticing. This
revision narrows the schema to a single closed evidence kind,
`c2_canonical_commit_qualification_v1`: each record names only a
`canonical_sha` (must exist locally) and an `evidence_ref` (its `sha` MUST
equal `canonical_sha` -- provenance can only bind to an identity this
compiler can prove is reachable). The suite counts are never read from the
input JSON at all -- `compile_current_state` derives them by reading the
canonical commit's OWN message (`git log -1 --format=%B`) and extracting the
one, unambiguous `Full suite: N passed, M skipped.` statement it contains.
Neither a PR number nor a historical "tested" branch-tip SHA is generated
into CURRENT at all -- both proved unreliable in earlier revisions (a
deleted source branch, an unverifiable hand-typed number) and are not
durably derivable offline; that narrative belongs in hand-written history
(e.g. `CHANGELOG.md`), not the generated CURRENT view.

## Epistemic ceiling

This module proves: the normative surface parses; `canonical` is the anchor's
own ACTIVE top-level subcommand set and a subset of `declared`; the compiled
counts match the anchor's own static authorities; `implementation_anchor`
and every `historical_evidence[].canonical_sha`/`evidence_ref.sha` exist as
real, locally reachable commits; each evidence record's suite counts are
mechanically derived from that canonical commit's own message, never
hand-declared. It does **not** prove that `implementation_anchor` equals
live GitHub `master` -- `verify_anchor_freshness` proves only ancestry plus
authority-path identity against a locally trusted ref, checked once by the
generator script before publication, never by this module.
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


def git_ref_exists(repo_root: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return proc.returncode == 0


def git_is_ancestor(repo_root: Path, ancestor: str, descendant_ref: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant_ref],
        cwd=repo_root, capture_output=True, text=True,
    )
    return proc.returncode == 0


def git_commit_message(repo_root: Path, sha: str) -> str:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%B", sha],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return proc.stdout


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
CommitExistsChecker = Callable[[str], bool]
RefExistsChecker = Callable[[str], bool]
IsAncestorChecker = Callable[[str, str], bool]


# --- Anchor freshness (a gate, never serialized into compiled state) ----

AUTHORITY_BEARING_PATHS_V1: tuple[str, ...] = (
    "scripts/agent-review-target-pack-v2.py",
    "app/agent_review/target_pack_validate_v2.py",
)

CANONICAL_REF_V1 = "refs/remotes/origin/master"


def verify_anchor_freshness(
    *,
    anchor: str,
    canonical_ref: str,
    canonical_ref_exists: RefExistsChecker,
    is_ancestor: IsAncestorChecker,
    read_blob_at_ref: BlobReader,
    authority_paths: tuple[str, ...] = AUTHORITY_BEARING_PATHS_V1,
) -> None:
    """A gate, deliberately separate from `compile_current_state`: proves
    `implementation_anchor` is not stale relative to `canonical_ref`, WITHOUT
    pinning CURRENT to permanent equality (`anchor == canonical_ref` would
    fail the instant this very successor's own tooling/docs commit lands on
    `canonical_ref`, despite no runtime target-pack authority having moved).

    Freshness = anchor is an ancestor of canonical_ref AND every
    authority-bearing blob is byte-identical between them. A later
    docs/tooling-only descendant commit therefore leaves CURRENT fresh; only
    a change to an authority path invalidates it. Never fetches network
    itself -- the caller's checkout (`fetch-depth: 0`) is responsible for
    `canonical_ref` already existing locally. The canonical_ref identity is
    intentionally NOT part of this function's return value or the compiled
    state's serialized JSON: freshness is a precondition checked once before
    publication, not stable rendered content."""

    if not canonical_ref_exists(canonical_ref):
        raise TargetPackCurrentStateError(
            f"CURRENT_STATE_STALE: canonical ref {canonical_ref!r} does not resolve to a commit locally"
        )
    if not is_ancestor(anchor, canonical_ref):
        raise TargetPackCurrentStateError(
            f"CURRENT_STATE_STALE: implementation_anchor {anchor!r} is not an ancestor of {canonical_ref!r}"
        )
    for path in authority_paths:
        anchor_blob = read_blob_at_ref(anchor, path)
        canonical_blob = read_blob_at_ref(canonical_ref, path)
        if anchor_blob != canonical_blob:
            raise TargetPackCurrentStateError(
                f"CURRENT_STATE_STALE: authority-bearing path {path!r} differs between anchor {anchor!r} "
                f"and canonical ref {canonical_ref!r} -- regenerate CURRENT against a new anchor"
            )


# --- Static AST extraction ---------------------------------------------


def _is_add_parser_call_on(node: ast.AST, subparsers_var: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == subparsers_var
    )


def extract_cli_subcommands(source: str) -> frozenset[str]:
    """Only ACTIVE, top-level subcommand registrations count as canonical --
    not any `*.add_parser(...)` call anywhere in the module. An unrestricted
    `ast.walk` would classify a call sitting in an unused helper, a dead
    conditional, or a nested parser as a shipped subcommand, publishing
    something argparse never actually exposes as "Canonical on `master`".

    Recognized grammar, matching this project's own anchor exactly:
      - exactly one top-level `def _parse_args(...)`;
      - a direct statement in its body `<var> = <parser>.add_subparsers(...)`,
        establishing the subparsers variable;
      - direct statements in that SAME body -- `<var> = <subparsers>.add_parser(
        "<name>", ...)` or a bare `<subparsers>.add_parser("<name>", ...)` --
        each contributing one name. Multiline calls are fully supported
        (AST is line-agnostic); this project's own anchor wraps `validate`'s
        registration across lines.

    Fails closed, rather than guessing execution semantics, when:
      - `_parse_args` is missing or not unique;
      - no direct `add_subparsers(...)` assignment is found;
      - an `add_parser(...)` call on the recognized subparsers variable
        exists ANYWHERE in `_parse_args` but NOT as a direct top-level
        statement (nested in an `if`/`for`/`try`/`with`/helper indirection);
      - a registration's first argument is not a string literal;
      - two top-level registrations declare the same name.

    A call on a DIFFERENT variable, or inside a DIFFERENT function entirely
    (an unused helper), is not walked at all here and has no effect --
    correctly, since it is not part of `_parse_args`'s own construction."""

    tree = ast.parse(source)

    parse_args_fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_parse_args"]
    if len(parse_args_fns) != 1:
        raise TargetPackCurrentStateError(
            f"expected exactly one top-level _parse_args function, found {len(parse_args_fns)}"
        )
    fn = parse_args_fns[0]

    subparsers_var: str | None = None
    for stmt in fn.body:
        if (
            isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "add_subparsers"
        ):
            subparsers_var = stmt.targets[0].id
            break
    if subparsers_var is None:
        raise TargetPackCurrentStateError(
            "_parse_args has no direct '<var> = <parser>.add_subparsers(...)' statement"
        )

    all_registration_calls = [n for n in ast.walk(fn) if _is_add_parser_call_on(n, subparsers_var)]

    top_level_names: list[str] = []
    top_level_call_ids: set[int] = set()
    for stmt in fn.body:
        call: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        if call is None or not _is_add_parser_call_on(call, subparsers_var):
            continue
        top_level_call_ids.add(id(call))
        if not (call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
            raise TargetPackCurrentStateError(
                f"a top-level {subparsers_var}.add_parser(...) call's first argument is not a string literal"
            )
        top_level_names.append(call.args[0].value)

    if len(all_registration_calls) != len(top_level_call_ids):
        raise TargetPackCurrentStateError(
            f"{subparsers_var}.add_parser(...) is invoked outside a direct top-level statement of "
            f"_parse_args (nested/conditional/helper construction) -- not statically representable"
        )
    if len(top_level_names) != len(set(top_level_names)):
        raise TargetPackCurrentStateError("duplicate top-level subcommand name registered in _parse_args")

    return frozenset(top_level_names)


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Every top-level `NAME = "literal"` or `NAME: T = "literal"` -- the
    symbol table `VALIDATE_CHECK_ORDER_V2`/`UNVALIDATED_CAPABILITIES_V2`
    reference by name rather than repeating string literals. Fails closed on
    a symbol assigned more than once at module level, rather than silently
    resolving to whichever assignment happened to be seen last."""

    consts: dict[str, str] = {}
    duplicated: set[str] = set()
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
            if target.id in consts:
                duplicated.add(target.id)
            consts[target.id] = value.value
    if duplicated:
        raise TargetPackCurrentStateError(
            f"module-level string constant(s) assigned more than once, not unambiguous: {sorted(duplicated)}"
        )
    return consts


def _module_level_value(tree: ast.Module, name: str) -> ast.expr:
    """Fails closed when `name` has zero OR more than one top-level
    assignment -- a duplicate module-level assignment of e.g.
    `VALIDATE_CHECK_ORDER_V2` must not silently resolve via
    first-assignment-wins (or last-assignment-wins) semantics."""

    matches: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            matches.append(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
            matches.append(node.value)
    if not matches:
        raise TargetPackCurrentStateError(f"no module-level assignment of {name!r} found")
    if len(matches) > 1:
        raise TargetPackCurrentStateError(
            f"{name!r} has {len(matches)} module-level assignments, not unambiguous"
        )
    return matches[0]


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
    if len(check_order) != len(set(check_order)):
        raise TargetPackCurrentStateError(
            "VALIDATE_CHECK_ORDER_V2 contains a duplicate check name -- not a malformed-but-countable list"
        )

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
    # Explicit indices, not split()-then-take-first-segment: with each marker
    # unique but END preceding BEGIN, a naive `split(BEGIN)[1].split(END)[0]`
    # never finds END in the tail (it precedes BEGIN, so it isn't THERE) and
    # silently returns everything to EOF -- happily accepting a later,
    # unrelated fenced JSON block as the normative surface.
    begin_idx = spec_text.index(_NORMATIVE_BEGIN)
    end_idx = spec_text.index(_NORMATIVE_END)
    if end_idx < begin_idx:
        raise TargetPackCurrentStateError(
            "normative block END marker precedes its BEGIN marker -- region is reversed or unterminated"
        )
    region = spec_text[begin_idx + len(_NORMATIVE_BEGIN):end_idx]

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


HISTORICAL_EVIDENCE_KIND_C2_CANONICAL_COMMIT_QUALIFICATION_V1 = "c2_canonical_commit_qualification_v1"
EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_C2_QUALIFICATION_V1 = "git_commit_message_c2_qualification_v1"

_C2_QUALIFICATION_LINE_RE = re.compile(r"Full suite:\s*(\d+)\s+passed,\s*(\d+)\s+skipped\.")


def extract_c2_qualification(message: str) -> tuple[int, int]:
    """Derives (passed, skipped) from a canonical commit's OWN message,
    mechanically, rather than trusting hand-declared counts in the inputs
    JSON -- the input can no longer assert a number the compiler never
    checks. Requires exactly one matching statement: zero means the message
    doesn't carry the claim this evidence kind promises, and more than one
    is an ambiguity this compiler refuses to silently resolve by taking the
    first (or last) match."""

    matches = _C2_QUALIFICATION_LINE_RE.findall(message)
    if len(matches) != 1:
        raise TargetPackCurrentStateError(
            f"expected exactly one 'Full suite: N passed, M skipped.' statement in the canonical commit "
            f"message, found {len(matches)}"
        )
    passed_str, skipped_str = matches[0]
    return int(passed_str), int(skipped_str)


@dataclass(frozen=True)
class EvidenceRefV1:
    kind: str
    sha: str


@dataclass(frozen=True)
class HistoricalEvidenceRecordV1:
    kind: str
    canonical_sha: str  # durable identity -- must exist locally; the ONLY subject this record binds to
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
        _require_keys(raw, {"kind", "canonical_sha", "evidence_ref"}, owner=owner)

        kind = raw.get("kind")
        if kind != HISTORICAL_EVIDENCE_KIND_C2_CANONICAL_COMMIT_QUALIFICATION_V1:
            raise TargetPackCurrentStateError(
                f"{owner}.kind is not a supported historical evidence kind: {kind!r}"
            )

        canonical_sha = raw.get("canonical_sha")
        if not is_full_sha(canonical_sha):
            raise TargetPackCurrentStateError(f"{owner}.canonical_sha is not a 40-hex lowercase sha: {canonical_sha!r}")
        if not commit_exists(canonical_sha):
            raise TargetPackCurrentStateError(f"{owner}.canonical_sha {canonical_sha!r} does not exist as a commit")

        evidence_ref_raw = raw.get("evidence_ref")
        if not isinstance(evidence_ref_raw, dict):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref must be a JSON object")
        _require_keys(evidence_ref_raw, {"kind", "sha"}, owner=f"{owner}.evidence_ref")
        ref_kind, ref_sha = evidence_ref_raw.get("kind"), evidence_ref_raw.get("sha")
        if ref_kind != EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_C2_QUALIFICATION_V1:
            raise TargetPackCurrentStateError(
                f"{owner}.evidence_ref.kind is not a supported evidence_ref kind: {ref_kind!r}"
            )
        if not is_full_sha(ref_sha):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha is not a 40-hex lowercase sha: {ref_sha!r}")
        if ref_sha != canonical_sha:
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha must equal canonical_sha")
        if not commit_exists(ref_sha):
            raise TargetPackCurrentStateError(f"{owner}.evidence_ref.sha {ref_sha!r} does not exist as a commit")

        records.append(
            HistoricalEvidenceRecordV1(
                kind=kind, canonical_sha=canonical_sha,
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
    kind: str
    canonical_sha: str
    evidence_ref_kind: str
    evidence_ref_sha: str
    suite_passed: int  # DERIVED from the canonical commit message, never hand-declared
    suite_skipped: int  # DERIVED from the canonical commit message, never hand-declared


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


CommitMessageReader = Callable[[str], str]


def compile_current_state(
    *,
    inputs: CurrentInputsV1,
    declared_surface: frozenset[str],
    read_blob: BlobReader,
    committed_at: CommittedAtReader,
    commit_message: CommitMessageReader,
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
        message = commit_message(record.evidence_ref.sha)
        passed, skipped = extract_c2_qualification(message)
        evidence_states.append(
            HistoricalEvidenceStateV1(
                kind=record.kind, canonical_sha=record.canonical_sha,
                evidence_ref_kind=record.evidence_ref.kind, evidence_ref_sha=record.evidence_ref.sha,
                suite_passed=passed, suite_skipped=skipped,
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
                    "kind": e.kind,
                    "canonical_sha": e.canonical_sha,
                    "evidence_ref": {"kind": e.evidence_ref_kind, "sha": e.evidence_ref_sha},
                    "suite": {"passed": e.suite_passed, "skipped": e.suite_skipped},
                }
                for e in state.historical_evidence
            ],
        },
    }


def render_compiled_json(state: TargetPackCurrentStateV1, *, source_inputs_path: str) -> str:
    payload = compiled_state_to_json_dict(state, source_inputs_path=source_inputs_path)
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
