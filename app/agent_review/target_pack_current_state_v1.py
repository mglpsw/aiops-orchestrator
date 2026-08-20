"""`#203` -- compiler for the target-pack ANCHOR-STATE projection.

INTERNAL repository tooling/documentation infrastructure, not a public
AgentReview target contract: no target ever reads this module or its output
at install/validate time, so its identifiers use `format_id`, never
`contract_id`.

*(The physical filename still says `current_state`; that is deliberate,
recorded compatibility debt from the `STOP_REDESIGN_2` structural
replacement, scheduled for a separate path-rename change. Every
machine-readable identifier this module emits already says `anchor`.)*

## Why this module exists, and what it stopped claiming

`#203-D0` produced three successive architectures. PR #245 tried to make
Markdown itself machine-verifiable by parsing status prose with ever-wider
grammars (`STOP_REDESIGN`, frozen). Its successor compiled a structured state
from real authorities -- the right inversion -- but kept asserting
`Canonical on master`, a forge-level fact, and spent two review rounds
acquiring authority to defend it: first a remote-tracking-ref comparison, then
the discovery that neither the ref's identity nor an evidence commit's
membership in canonical history could be established offline at all
(`STOP_REDESIGN_2`).

This revision removes the obligation instead of the machinery's next layer.
The compiler no longer establishes -- and no longer has any code capable of
establishing -- live master identity, forge canonicality, current product
state, PR-to-SHA identity, or lifecycle disposition. It proves only
propositions mechanically supported by the normative surface, the anchor's own
git objects, ancestry inside this checkout, and exact commit-message text.

## Identity is a typed proposition, never a scalar

```
I = ⟨ S subject, A authority, R relation, T epoch, E evidence ⟩
```

These are DISTINCT types with no implicit coercion:

```
GitObjectExists(c)   ≠  AncestorOf(c, HEAD)  ≠  ExposedAtAnchor(cmd, a)
                                             ≠  ForgeMasterAt(t, a)
```

`P ⇏ Q` unless an explicitly authorized composition rule exists. The two
coercions this workstream paid for, now structurally impossible here:

```
CommitExists(c)          ⇏  CanonicalOnMaster(c)
ExposedAtAnchor(cmd, a)  ⇏  Current(cmd, t)
```

## What the artifact is -- a heterogeneous projection, not one commit's state

It fuses a CURRENT-spec authority with a HISTORICAL-anchor authority, so both
are identified in the artifact itself rather than left implicit:

```
component                        subject               authority
declared                         normative spec block  §4 block, by content digest
exposed_at_anchor                target-pack CLI       git blob at the anchor
declared_not_exposed_at_anchor   --                    set difference of the two
validate_inventory_at_anchor     validate module       git blob at the anchor
commit_message_evidence          a commit message      that commit, ⪯ anchor
```

```
𝒜(S,A):  D_S = DeclaredSurface(S)      E_A = ExposedCLI(A)     V_A = ValidateInventory(A)
         N_SA = D_S − E_A              Q_C = FullSuiteCounts(C),  C ⪯ A

invariants:  E_A ⊆ D_S   N_SA = D_S − E_A   E_A ∩ N_SA = ∅   E_A ∪ N_SA = D_S   C ⪯ A ⪯ HEAD
negatives:   𝒜(S,A) ⇏ ForgeCanonical(A)     𝒜(S,A) ⇏ LiveCurrentState
```

"State" here means *a typed set of propositions whose central relation is the
implementation anchor* -- NOT "every component came from the same commit".

## No lifecycle is derivable from exposure

`declared_not_exposed_at_anchor` proves exactly *not exposed at that anchor*.
It does not mean deferred: a name could later be absent because it was
deferred, removed, reserved, unsupported, planned, abandoned -- or dropped by
an accidental regression. Converting the observation into a lifecycle verdict
would silently relabel a product regression as valid planning. Lifecycle
disposition belongs to the roadmap/spec authority (§14), never here.

## Anchor-derived reads are static, never executed

`git show <anchor>:<path>` + `ast.parse`. Never `importlib`, never `exec`,
never `sys.path`. `extract_cli_subcommands` recognizes ONLY direct, top-level
`<subparsers>.add_parser("name", ...)` statements inside the anchor's own
`_parse_args`; a registration on that variable found anywhere else (nested
control flow, helper indirection) fails closed rather than being silently
included or silently skipped.

## Coherence, not freshness -- and no remote dependency at all

`verify_anchor_coherence` proves only that the anchor exists and lies in this
checkout's own lineage (`anchor ⪯ HEAD`). `HEAD` is used purely as the local
lineage subject; it is never treated as canonical, master, current, or any
forge authority. Withdrawn entirely, with NO replacement: the canonical-ref
constant, ref existence lookups, authority-blob equality against a moving ref,
and the `CURRENT_STATE_STALE` verdict. This module performs no ref discovery
and has no remote dependency, so a checkout whose remote is renamed, absent,
or represented only by local branches compiles identically.

## Evidence is a fact about a message, not a role

Each record names one `commit_sha` that must exist and satisfy
`commit_sha ⪯ implementation_anchor` -- an ordinary git graph fact. Suite
counts are never read from the input; they are derived from that commit's own
message, which must carry exactly one `Full suite: N passed, M skipped.`
statement (zero or many fails closed). Nothing here asserts the commit was
canonical, was a PR head, or that the run "qualified" anything: those are
propositions from other authorities and belong to hand-written history.

## Epistemic ceiling

Proven: the normative surface parses and is identified by digest;
`exposed_at_anchor` is the anchor's own active top-level subcommand set and a
subset of `declared`; the validate inventory matches the anchor's own static
constants; the anchor exists and is in this checkout's lineage; each evidence
commit exists, is an ancestor of the anchor, and its message mechanically
yields the reported counts. NOT proven, and not attempted: any relation
between the anchor and live GitHub `master`, any forge canonicality, any
temporal "current" claim.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

# --- Errors -----------------------------------------------------------


class TargetPackAnchorStateError(Exception):
    """Raised for any diagnosable defect in inputs, the normative surface,
    an anchor's static authorities, anchor coherence, or their mutual
    consistency. Never silently downgraded to a partial/best-effort
    result -- fail closed."""


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
        raise TargetPackAnchorStateError(f"git produced no committer date for {sha!r}")
    return datetime.fromisoformat(stamp)


def git_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """Pure git-graph relation between two commit-ish operands. Reflexive:
    `git merge-base --is-ancestor X X` succeeds, so one predicate expresses
    "equal to or an ancestor of". Never consults a remote-tracking ref --
    callers pass SHAs, or `HEAD`, which exists in every valid checkout."""

    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
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
    Reads exactly the blob that commit recorded, regardless of the current
    working tree, and needs no ref of any kind."""

    proc = subprocess.run(
        ["git", "show", f"{anchor_sha}:{relative_path}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TargetPackAnchorStateError(
            f"could not read {relative_path!r} at anchor {anchor_sha!r}: {proc.stderr.strip()}"
        )
    return proc.stdout


BlobReader = Callable[[str, str], str]
CommittedAtReader = Callable[[str], datetime]
CommitExistsChecker = Callable[[str], bool]
IsAncestorChecker = Callable[[str, str], bool]
CommitMessageReader = Callable[[str], str]


# --- Anchor coherence (a gate; never serialized into the projection) ----

CHECKOUT_LINEAGE_SUBJECT = "HEAD"


def verify_anchor_coherence(
    *,
    anchor: str,
    commit_exists: CommitExistsChecker,
    is_ancestor: IsAncestorChecker,
    lineage_subject: str = CHECKOUT_LINEAGE_SUBJECT,
) -> None:
    """Proves the anchor is a real object inside THIS checkout's lineage --
    nothing more. It deliberately does not, and cannot, establish which
    commit any forge calls canonical.

    The two failures are classified separately on purpose:
    `merge-base --is-ancestor` exits non-zero both for an unknown object and
    for a genuine non-ancestor, and conflating them would report an
    environment/input failure as a truth verdict about the projection.

    `lineage_subject` defaults to `HEAD`, which exists in every valid
    checkout including detached ones and CI's `pull_request` merge commit (of
    which the anchor is still an ancestor). It is the checkout's lineage
    subject only -- never canonical, master, current, or a forge authority."""

    if not commit_exists(anchor):
        raise TargetPackAnchorStateError(
            f"anchor_not_found: implementation_anchor {anchor!r} does not exist as a commit in this repository"
        )
    if not is_ancestor(anchor, lineage_subject):
        raise TargetPackAnchorStateError(
            f"anchor_not_in_checkout_lineage: implementation_anchor {anchor!r} is not an ancestor of "
            f"{lineage_subject} -- this checkout does not contain the lineage of the projection it is materializing"
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
    """The subcommands argparse ACTIVELY registers at top level -- not any
    `*.add_parser(...)` call anywhere in the module. An unrestricted
    `ast.walk` would classify a call in an unused helper, a dead conditional,
    or a nested parser as a shipped subcommand, publishing a name argparse
    never exposes.

    Recognized grammar, matching this project's own anchor exactly:
      - exactly one top-level `def _parse_args(...)`;
      - a direct statement `<var> = <parser>.add_subparsers(...)` in its body;
      - direct statements in that SAME body -- assigned or bare
        `<subparsers>.add_parser("<name>", ...)` -- each contributing one
        name. Multiline calls are supported (AST is line-agnostic); this
        project's anchor wraps `validate`'s registration across lines.

    Fails closed instead of guessing execution semantics when `_parse_args`
    is missing/non-unique, no direct `add_subparsers(...)` assignment exists,
    a registration on the recognized variable sits outside a direct top-level
    statement, a first argument is not a string literal, or two top-level
    registrations declare the same name. A call on a DIFFERENT variable, or
    inside a DIFFERENT function, is not part of `_parse_args`'s construction
    and correctly has no effect."""

    tree = ast.parse(source)

    parse_args_fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_parse_args"]
    if len(parse_args_fns) != 1:
        raise TargetPackAnchorStateError(
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
        raise TargetPackAnchorStateError(
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
            raise TargetPackAnchorStateError(
                f"a top-level {subparsers_var}.add_parser(...) call's first argument is not a string literal"
            )
        top_level_names.append(call.args[0].value)

    if len(all_registration_calls) != len(top_level_call_ids):
        raise TargetPackAnchorStateError(
            f"{subparsers_var}.add_parser(...) is invoked outside a direct top-level statement of "
            f"_parse_args (nested/conditional/helper construction) -- not statically representable"
        )
    if len(top_level_names) != len(set(top_level_names)):
        raise TargetPackAnchorStateError("duplicate top-level subcommand name registered in _parse_args")

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
        raise TargetPackAnchorStateError(
            f"module-level string constant(s) assigned more than once, not unambiguous: {sorted(duplicated)}"
        )
    return consts


def _module_level_value(tree: ast.Module, name: str) -> ast.expr:
    """Fails closed when `name` has zero OR more than one top-level
    assignment -- a duplicate module-level assignment must not silently
    resolve via first- or last-assignment-wins semantics."""

    matches: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            matches.append(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
            matches.append(node.value)
    if not matches:
        raise TargetPackAnchorStateError(f"no module-level assignment of {name!r} found")
    if len(matches) > 1:
        raise TargetPackAnchorStateError(
            f"{name!r} has {len(matches)} module-level assignments, not unambiguous"
        )
    return matches[0]


def _resolve_name_sequence(value: ast.expr, consts: dict[str, str], *, owner: str) -> tuple[str, ...]:
    if not isinstance(value, (ast.Tuple, ast.List)):
        raise TargetPackAnchorStateError(f"{owner} is not a tuple/list literal; not statically representable")
    resolved: list[str] = []
    for element in value.elts:
        if not isinstance(element, ast.Name):
            raise TargetPackAnchorStateError(f"{owner} contains a non-Name element; not statically representable")
        if element.id not in consts:
            raise TargetPackAnchorStateError(f"{owner} references undefined constant {element.id!r}")
        resolved.append(consts[element.id])
    return tuple(resolved)


@dataclass(frozen=True)
class ValidateInventoryAtAnchorV1:
    """What THIS validate implementation, at THIS anchor, can and cannot
    establish from a target alone. `unvalidated_capabilities` deliberately
    carries no permanence claim: the anchor's own constants prove only that
    these dimensions are unvalidated by this implementation, never that they
    are impossible for any future target-pack version."""

    total: int
    locally_evaluable: int
    unvalidated_capabilities: frozenset[str]


def extract_validate_inventory(source: str) -> ValidateInventoryAtAnchorV1:
    """Statically extracts the validate check inventory from
    `target_pack_validate_v2.py`'s own `VALIDATE_CHECK_ORDER_V2` and
    `UNVALIDATED_CAPABILITIES_V2` module constants -- never imports the
    module, never falls back to hardcoded numbers, and fails closed the
    instant either constant stops being a plain tuple-of-Names (or
    tuple-of-(Name,Name)-pairs) literal, repeats a name, or is assigned more
    than once."""

    tree = ast.parse(source)
    consts = _module_string_constants(tree)

    order_value = _module_level_value(tree, "VALIDATE_CHECK_ORDER_V2")
    check_order = _resolve_name_sequence(order_value, consts, owner="VALIDATE_CHECK_ORDER_V2")
    if len(check_order) != len(set(check_order)):
        raise TargetPackAnchorStateError(
            "VALIDATE_CHECK_ORDER_V2 contains a duplicate check name -- not a malformed-but-countable list"
        )

    unavailable_value = _module_level_value(tree, "UNVALIDATED_CAPABILITIES_V2")
    if not isinstance(unavailable_value, (ast.Tuple, ast.List)):
        raise TargetPackAnchorStateError("UNVALIDATED_CAPABILITIES_V2 is not a tuple/list literal")
    unvalidated_names: list[str] = []
    for pair in unavailable_value.elts:
        if not isinstance(pair, ast.Tuple) or len(pair.elts) != 2 or not isinstance(pair.elts[0], ast.Name):
            raise TargetPackAnchorStateError("UNVALIDATED_CAPABILITIES_V2 contains a non-(Name, ...) pair")
        name_node = pair.elts[0]
        if name_node.id not in consts:
            raise TargetPackAnchorStateError(f"UNVALIDATED_CAPABILITIES_V2 references undefined constant {name_node.id!r}")
        unvalidated_names.append(consts[name_node.id])

    unvalidated = frozenset(unvalidated_names)
    if len(unvalidated_names) != len(unvalidated):
        raise TargetPackAnchorStateError("UNVALIDATED_CAPABILITIES_V2 contains a duplicate dimension name")
    if not unvalidated <= set(check_order):
        raise TargetPackAnchorStateError("UNVALIDATED_CAPABILITIES_V2 names a dimension absent from VALIDATE_CHECK_ORDER_V2")

    return ValidateInventoryAtAnchorV1(
        total=len(check_order),
        locally_evaluable=len(check_order) - len(unvalidated),
        unvalidated_capabilities=unvalidated,
    )


# --- Normative surface (working tree, identified by content digest) ------

NORMATIVE_SURFACE_FORMAT_ID_V1 = "aiops.agent-review.target-pack-surface.v1"
NORMATIVE_SURFACE_SLOT_ID = "target-pack-anchor.spec.lifecycle-prose"
_NORMATIVE_BEGIN = "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->"
_NORMATIVE_END = "<!-- END NORMATIVE: target-pack-surface-v1 -->"


def _strict_json_loads(text: str, *, owner: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise TargetPackAnchorStateError(f"{owner}: duplicate JSON key {key!r}")
            seen[key] = value
        return seen

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise TargetPackAnchorStateError(f"{owner}: invalid JSON: {exc}") from exc


@dataclass(frozen=True)
class NormativeSurfaceV1:
    """The declared surface together with the cryptographic identity of the
    authority it was projected from. The digest is what lets the projection
    name its OTHER authority: `exposed_at_anchor` is identified by the anchor
    SHA, and `declared` is identified by this."""

    format_id: str
    declared: frozenset[str]
    content_sha256: str


def canonical_normative_digest(declared: frozenset[str], *, format_id: str) -> str:
    """Digest over the CANONICAL SERIALIZATION OF THE PARSED OBJECT -- never
    raw Markdown bytes, never the fenced block's bytes, never the whole spec
    file.

    Declared names form a set, so the canonical form sorts them; keys are
    sorted and separators are compact. Consequences, both tested: reformatting
    the block (whitespace, key order, element order) leaves the digest
    unchanged, while any material change to `declared` changes it. Digesting
    the whole document instead would churn on every unrelated documentation
    edit; digesting raw bytes would contradict that equivalence invariance."""

    canonical = json.dumps(
        {"format_id": format_id, "declared": sorted(declared)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_normative_surface(spec_text: str) -> NormativeSurfaceV1:
    """Reads the ONE normative structured block in §4 -- the only
    hand-maintained product datum in this pipeline -- and returns it together
    with its content digest, computed from the same parsed object so the two
    cannot drift apart."""

    begin_count = spec_text.count(_NORMATIVE_BEGIN)
    if begin_count == 0:
        raise TargetPackAnchorStateError(f"normative block start marker {_NORMATIVE_BEGIN!r} not found")
    if begin_count > 1:
        raise TargetPackAnchorStateError(
            f"normative block start marker {_NORMATIVE_BEGIN!r} is not unique ({begin_count} occurrences)"
        )
    end_count = spec_text.count(_NORMATIVE_END)
    if end_count != 1:
        raise TargetPackAnchorStateError(
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
        raise TargetPackAnchorStateError(
            "normative block END marker precedes its BEGIN marker -- region is reversed or unterminated"
        )
    region = spec_text[begin_idx + len(_NORMATIVE_BEGIN):end_idx]

    fence_matches = re.findall(r"```json\n(.*?)```", region, flags=re.DOTALL)
    if len(fence_matches) != 1:
        raise TargetPackAnchorStateError(
            f"expected exactly one fenced json block inside the normative markers, found {len(fence_matches)}"
        )
    doc = _strict_json_loads(fence_matches[0], owner="normative surface block")
    if not isinstance(doc, dict):
        raise TargetPackAnchorStateError("normative surface block is not a JSON object")
    if set(doc.keys()) != {"format_id", "declared"}:
        raise TargetPackAnchorStateError(f"normative surface block has unexpected keys: {sorted(doc.keys())}")
    if doc.get("format_id") != NORMATIVE_SURFACE_FORMAT_ID_V1:
        raise TargetPackAnchorStateError(f"normative surface format_id mismatch: {doc.get('format_id')!r}")
    declared = doc.get("declared")
    if not isinstance(declared, list) or not declared:
        raise TargetPackAnchorStateError("normative surface 'declared' must be a non-empty JSON array")
    if not all(isinstance(name, str) and name for name in declared):
        raise TargetPackAnchorStateError("normative surface 'declared' must contain only non-empty strings")
    if len(declared) != len(set(declared)):
        raise TargetPackAnchorStateError("normative surface 'declared' contains a duplicate name")

    declared_set = frozenset(declared)
    return NormativeSurfaceV1(
        format_id=NORMATIVE_SURFACE_FORMAT_ID_V1,
        declared=declared_set,
        content_sha256=canonical_normative_digest(declared_set, format_id=NORMATIVE_SURFACE_FORMAT_ID_V1),
    )


def extract_declared_surface(spec_text: str) -> frozenset[str]:
    """The declared names alone. A thin projection of
    `extract_normative_surface`, never a second parser -- so the surface and
    the digest identifying it are always derived from one parse and cannot
    drift apart. Architecture tests that only need the name set use this."""

    return extract_normative_surface(spec_text).declared


# --- Non-derivable inputs ------------------------------------------------

ANCHOR_INPUTS_FORMAT_ID_V1 = "aiops.agent-review.target-pack-anchor-inputs.v1"

_RFC3339_OFFSET_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _parse_rfc3339_offset_aware(value: object, *, owner: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339_OFFSET_RE.match(value):
        raise TargetPackAnchorStateError(f"{owner}: not an RFC3339 timestamp with an explicit offset: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TargetPackAnchorStateError(f"{owner}: timestamp has no offset: {value!r}")
    return parsed


COMMIT_MESSAGE_EVIDENCE_KIND_V1 = "commit_message_evidence_v1"
EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_V1 = "git_commit_message_v1"

_FULL_SUITE_RE = re.compile(r"Full suite:\s*(\d+)\s+passed,\s*(\d+)\s+skipped\.")


def extract_full_suite_counts(message: str) -> tuple[int, int]:
    """Derives (passed, skipped) from a commit's OWN message rather than
    trusting hand-declared counts -- the input has no field in which to
    assert a number this compiler never checks. Requires exactly one matching
    statement: zero means the message does not carry the claim this evidence
    kind promises, and more than one is an ambiguity this compiler refuses to
    resolve silently by taking the first or last match."""

    matches = _FULL_SUITE_RE.findall(message)
    if len(matches) != 1:
        raise TargetPackAnchorStateError(
            f"expected exactly one 'Full suite: N passed, M skipped.' statement in the commit message, "
            f"found {len(matches)}"
        )
    passed_str, skipped_str = matches[0]
    return int(passed_str), int(skipped_str)


@dataclass(frozen=True)
class EvidenceRefV1:
    kind: str
    sha: str


@dataclass(frozen=True)
class CommitMessageEvidenceRecordV1:
    kind: str
    commit_sha: str
    evidence_ref: EvidenceRefV1


@dataclass(frozen=True)
class AnchorInputsV1:
    implementation_anchor: str
    reconciled_at: datetime
    commit_message_evidence: tuple[CommitMessageEvidenceRecordV1, ...]


def _require_keys(obj: dict, expected: set[str], *, owner: str) -> None:
    if set(obj.keys()) != expected:
        extra = sorted(set(obj.keys()) - expected)
        missing = sorted(expected - set(obj.keys()))
        raise TargetPackAnchorStateError(f"{owner}: unexpected keys={extra} missing={missing}")


def load_anchor_inputs(
    path: Path,
    *,
    commit_exists: CommitExistsChecker,
) -> AnchorInputsV1:
    """Structural validation only. Relations between operands (evidence
    ancestry, temporal ordering) belong to the compile step, where the anchor
    is the authoritative subject."""

    text = path.read_text(encoding="utf-8")
    doc = _strict_json_loads(text, owner=str(path))
    if not isinstance(doc, dict):
        raise TargetPackAnchorStateError(f"{path}: top level must be a JSON object")
    _require_keys(
        doc,
        {"format_id", "implementation_anchor", "reconciliation", "commit_message_evidence"},
        owner=str(path),
    )

    if doc.get("format_id") != ANCHOR_INPUTS_FORMAT_ID_V1:
        raise TargetPackAnchorStateError(f"{path}: format_id mismatch: {doc.get('format_id')!r}")

    anchor = doc.get("implementation_anchor")
    if not is_full_sha(anchor):
        raise TargetPackAnchorStateError(f"{path}: implementation_anchor is not a 40-hex lowercase sha: {anchor!r}")
    if not commit_exists(anchor):
        raise TargetPackAnchorStateError(f"{path}: implementation_anchor {anchor!r} does not exist as a commit")

    reconciliation = doc.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise TargetPackAnchorStateError(f"{path}: 'reconciliation' must be a JSON object")
    _require_keys(reconciliation, {"reconciled_at"}, owner=f"{path}:reconciliation")
    reconciled_at = _parse_rfc3339_offset_aware(
        reconciliation.get("reconciled_at"), owner=f"{path}:reconciliation.reconciled_at"
    )

    evidence_raw = doc.get("commit_message_evidence")
    if not isinstance(evidence_raw, list):
        raise TargetPackAnchorStateError(f"{path}: 'commit_message_evidence' must be a JSON array")

    records: list[CommitMessageEvidenceRecordV1] = []
    for i, raw in enumerate(evidence_raw):
        owner = f"{path}:commit_message_evidence[{i}]"
        if not isinstance(raw, dict):
            raise TargetPackAnchorStateError(f"{owner}: must be a JSON object")
        _require_keys(raw, {"kind", "commit_sha", "evidence_ref"}, owner=owner)

        kind = raw.get("kind")
        if kind != COMMIT_MESSAGE_EVIDENCE_KIND_V1:
            raise TargetPackAnchorStateError(f"{owner}.kind is not a supported evidence kind: {kind!r}")

        commit_sha = raw.get("commit_sha")
        if not is_full_sha(commit_sha):
            raise TargetPackAnchorStateError(f"{owner}.commit_sha is not a 40-hex lowercase sha: {commit_sha!r}")
        if not commit_exists(commit_sha):
            raise TargetPackAnchorStateError(f"{owner}.commit_sha {commit_sha!r} does not exist as a commit")

        evidence_ref_raw = raw.get("evidence_ref")
        if not isinstance(evidence_ref_raw, dict):
            raise TargetPackAnchorStateError(f"{owner}.evidence_ref must be a JSON object")
        _require_keys(evidence_ref_raw, {"kind", "sha"}, owner=f"{owner}.evidence_ref")
        ref_kind, ref_sha = evidence_ref_raw.get("kind"), evidence_ref_raw.get("sha")
        if ref_kind != EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_V1:
            raise TargetPackAnchorStateError(
                f"{owner}.evidence_ref.kind is not a supported evidence_ref kind: {ref_kind!r}"
            )
        if not is_full_sha(ref_sha):
            raise TargetPackAnchorStateError(f"{owner}.evidence_ref.sha is not a 40-hex lowercase sha: {ref_sha!r}")
        if ref_sha != commit_sha:
            raise TargetPackAnchorStateError(f"{owner}.evidence_ref.sha must equal commit_sha")

        records.append(
            CommitMessageEvidenceRecordV1(
                kind=kind, commit_sha=commit_sha,
                evidence_ref=EvidenceRefV1(kind=ref_kind, sha=ref_sha),
            )
        )

    return AnchorInputsV1(
        implementation_anchor=anchor,
        reconciled_at=reconciled_at,
        commit_message_evidence=tuple(records),
    )


# --- The anchor-state projection -----------------------------------------


@dataclass(frozen=True)
class CommitMessageEvidenceStateV1:
    kind: str
    commit_sha: str
    evidence_ref_kind: str
    evidence_ref_sha: str
    suite_passed: int  # DERIVED from the commit message, never hand-declared
    suite_skipped: int  # DERIVED from the commit message, never hand-declared


@dataclass(frozen=True)
class TargetPackAnchorStateV1:
    format_id: str
    normative_surface: NormativeSurfaceV1
    normative_surface_source_path: str
    exposed_at_anchor: frozenset[str]
    declared_not_exposed_at_anchor: frozenset[str]
    validate_inventory: ValidateInventoryAtAnchorV1
    implementation_anchor: str
    anchor_committed_at: datetime
    reconciled_at: datetime
    commit_message_evidence: tuple[CommitMessageEvidenceStateV1, ...]


ANCHOR_STATE_FORMAT_ID_V1 = "aiops.agent-review.target-pack-anchor-state.v1"
ANCHOR_STATE_GENERATOR_ID_V1 = "target-pack-anchor-state-v1"


def compile_anchor_state(
    *,
    inputs: AnchorInputsV1,
    normative_surface: NormativeSurfaceV1,
    normative_surface_source_path: str,
    read_blob: BlobReader,
    committed_at: CommittedAtReader,
    commit_message: CommitMessageReader,
    is_ancestor: IsAncestorChecker,
) -> TargetPackAnchorStateV1:
    """Pure given its callables: no filesystem/subprocess access happens
    inside this function itself, so unit tests inject fixture readers without
    needing real git commits, while the generator wires real anchor-bound git
    access."""

    anchor = inputs.implementation_anchor
    declared = normative_surface.declared

    cli_source = read_blob(anchor, "scripts/agent-review-target-pack-v2.py")
    exposed_at_anchor = extract_cli_subcommands(cli_source)
    if not exposed_at_anchor <= declared:
        raise TargetPackAnchorStateError(
            f"exposed_not_subset_declared: anchor {anchor!r} exposes {sorted(exposed_at_anchor - declared)} "
            f"absent from the normative declared surface"
        )
    declared_not_exposed_at_anchor = declared - exposed_at_anchor

    validate_source = read_blob(anchor, "app/agent_review/target_pack_validate_v2.py")
    validate_inventory = extract_validate_inventory(validate_source)

    anchor_committed_at = committed_at(anchor)
    if inputs.reconciled_at < anchor_committed_at:
        raise TargetPackAnchorStateError(
            f"reconciled_at {inputs.reconciled_at.isoformat()!r} precedes the implementation anchor's own "
            f"committed_at {anchor_committed_at.isoformat()!r} -- observation time cannot predate the event "
            f"it observes"
        )

    evidence_states: list[CommitMessageEvidenceStateV1] = []
    for record in inputs.commit_message_evidence:
        # An ordinary git-graph fact. It does NOT establish that the commit
        # was canonical, a PR head, or a branch tip -- only that it lies in
        # the anchor's own history, which is all this projection needs.
        if not is_ancestor(record.commit_sha, anchor):
            raise TargetPackAnchorStateError(
                f"evidence_commit_not_in_anchor_history: {record.commit_sha!r} is not an ancestor of "
                f"implementation_anchor {anchor!r}"
            )
        passed, skipped = extract_full_suite_counts(commit_message(record.evidence_ref.sha))
        evidence_states.append(
            CommitMessageEvidenceStateV1(
                kind=record.kind, commit_sha=record.commit_sha,
                evidence_ref_kind=record.evidence_ref.kind, evidence_ref_sha=record.evidence_ref.sha,
                suite_passed=passed, suite_skipped=skipped,
            )
        )

    return TargetPackAnchorStateV1(
        format_id=ANCHOR_STATE_FORMAT_ID_V1,
        normative_surface=normative_surface,
        normative_surface_source_path=normative_surface_source_path,
        exposed_at_anchor=exposed_at_anchor,
        declared_not_exposed_at_anchor=declared_not_exposed_at_anchor,
        validate_inventory=validate_inventory,
        implementation_anchor=anchor,
        anchor_committed_at=anchor_committed_at,
        reconciled_at=inputs.reconciled_at,
        commit_message_evidence=tuple(evidence_states),
    )


def anchor_state_to_json_dict(state: TargetPackAnchorStateV1, *, source_inputs_path: str) -> dict:
    """The single serialization site -- the generator's `write` and `--check`
    modes both call this, so the JSON shape cannot drift from what the
    compiler actually produced."""

    return {
        "format_id": state.format_id,
        "generated": {
            "generator": ANCHOR_STATE_GENERATOR_ID_V1,
            "source_inputs": source_inputs_path,
            "implementation_anchor": state.implementation_anchor,
        },
        "state": {
            "normative_surface": {
                "format_id": state.normative_surface.format_id,
                "source_path": state.normative_surface_source_path,
                "content_sha256": state.normative_surface.content_sha256,
            },
            "surface": {
                "declared": sorted(state.normative_surface.declared),
                "exposed_at_anchor": sorted(state.exposed_at_anchor),
                "declared_not_exposed_at_anchor": sorted(state.declared_not_exposed_at_anchor),
            },
            "validate_inventory_at_anchor": {
                "total": state.validate_inventory.total,
                "locally_evaluable": state.validate_inventory.locally_evaluable,
                "unvalidated_capabilities": sorted(state.validate_inventory.unvalidated_capabilities),
            },
            "temporal": {
                "implementation_anchor": state.implementation_anchor,
                "anchor_committed_at": state.anchor_committed_at.isoformat(),
                "reconciled_at": state.reconciled_at.isoformat(),
            },
            "commit_message_evidence": [
                {
                    "kind": e.kind,
                    "commit_sha": e.commit_sha,
                    "evidence_ref": {"kind": e.evidence_ref_kind, "sha": e.evidence_ref_sha},
                    "suite": {"passed": e.suite_passed, "skipped": e.suite_skipped},
                }
                for e in state.commit_message_evidence
            ],
        },
    }


def render_anchor_state_json(state: TargetPackAnchorStateV1, *, source_inputs_path: str) -> str:
    payload = anchor_state_to_json_dict(state, source_inputs_path=source_inputs_path)
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
