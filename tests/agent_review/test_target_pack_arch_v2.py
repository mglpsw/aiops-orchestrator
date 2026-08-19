"""AST/call-graph proof that `#203`'s `doctor` subcommand is read-only by
construction, not merely by docstring convention -- the same "mechanical
proof" discipline `#201-C`'s own `test_required_check_readiness_arch_v2.py`
established for its choke-point invariants, applied here to a new one:
`run_doctor_v2` and everything it transitively calls within `app.agent_
review.target_pack_doctor_v2` must never call a filesystem-mutating
primitive.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app" / "agent_review"
TEMPLATES_DIR = REPO_ROOT / "templates" / "agentreview-v2-target-pack"
DOCTOR_MODULE_PATH = APP_DIR / "target_pack_doctor_v2.py"
VALIDATE_MODULE_PATH = APP_DIR / "target_pack_validate_v2.py"
TARGET_PACK_MODULE_PATHS = sorted(APP_DIR.glob("target_pack_*.py"))

# Registry of modules claiming read-only-by-construction in their own
# docstring, each with its own entry-point function's name -- `doctor` and
# `validate` (`#203-C2`) share this mechanical proof rather than each
# hand-rolling a copy, so a future third read-only command extends the
# registry instead of copy-pasting the scan.
_READ_ONLY_MODULES_V2: tuple[tuple[Path, str], ...] = (
    (DOCTOR_MODULE_PATH, "run_doctor_v2"),
    (VALIDATE_MODULE_PATH, "run_validate_v2"),
)

# Attribute names that, called on ANY object, would indicate a write/mutate
# operation. Deliberately broad (matches the method name regardless of
# receiver) since neither `doctor` nor `validate` has any legitimate reason
# to call any of these on anything. `open` is handled separately below --
# validate's own bounded/streamed reads legitimately call `Path.open`, so a
# blanket ban would also forbid its read path; only a statically-provable
# non-read-only mode is forbidden for `open`.
_FORBIDDEN_ATTR_CALLS_V2 = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "replace",
        "touch",
        "chmod",
        "copy",
        "copyfile",
        "copytree",
        "move",
        "rmtree",
    }
)
_FORBIDDEN_MODULE_CALLS_V2 = frozenset({"remove", "mkdir", "makedirs", "rename", "replace", "unlink"})
_ALLOWED_OPEN_MODES_V2 = frozenset({"r", "rb"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _open_call_mode_offense(node: ast.Call) -> str | None:
    """Returns a description of the offense if this `.open(...)` call's
    mode is not statically provable as read-only, else `None`. Missing
    mode argument is an offense too -- `Path.open()`'s default `"r"` IS
    read-only, but requiring it explicit keeps the mode a single glance
    away from this scan, matching the discipline already established
    for `target_pack_validate_v2`'s own bounded-read call sites."""

    mode_arg: ast.expr | None = None
    if node.args:
        mode_arg = node.args[0]
    for kw in node.keywords:
        if kw.arg == "mode":
            mode_arg = kw.value
    if mode_arg is None:
        return f"open() at line {node.lineno} has no explicit mode argument"
    if not (isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str)):
        return f"open() at line {node.lineno} has a non-literal mode argument"
    if mode_arg.value not in _ALLOWED_OPEN_MODES_V2:
        return f"open() at line {node.lineno} uses non-read-only mode {mode_arg.value!r}"
    return None


def test_read_only_modules_call_no_filesystem_mutating_primitive() -> None:
    for module_path, _entry_point in _READ_ONLY_MODULES_V2:
        tree = _parse(module_path)
        offenders: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "open":
                offense = _open_call_mode_offense(node)
                if offense is not None:
                    offenders.append(f"{module_path.name}:{offense}")
            elif isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS_V2:
                offenders.append(f"{module_path.name}:{func.attr}() at line {node.lineno}")
            elif isinstance(func, ast.Name) and func.id in _FORBIDDEN_MODULE_CALLS_V2:
                offenders.append(f"{module_path.name}:{func.id}() at line {node.lineno}")

        assert not offenders, (
            f"{module_path.name} calls a filesystem-mutating primitive (or an open() with a "
            f"non-statically-provable read-only mode), violating its own 'READ-ONLY BY "
            f"CONSTRUCTION' docstring guarantee: {offenders}"
        )


def test_read_only_entry_points_have_no_mutating_parameter() -> None:
    """A second, independent check per module: the entry point itself
    must never accept a `plan`/`force_overwrite_paths`/anything shaped
    like a write instruction -- if it ever did, the AST scan above could
    stop being sufficient (a future caller-supplied write could reach
    the filesystem without a literal `write_text`/`mkdir` call appearing
    in the module at all)."""

    forbidden_param_names = {"plan", "force_overwrite_paths", "seed_content_by_path", "write", "apply"}
    for module_path, entry_point in _READ_ONLY_MODULES_V2:
        tree = _parse(module_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == entry_point:
                all_args = [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]
                offending = [arg.arg for arg in all_args if arg.arg in forbidden_param_names]
                assert not offending, f"{entry_point} accepts a write-shaped parameter: {offending}"
                break
        else:
            raise AssertionError(f"{entry_point} not found in {module_path.name}")


# Real target/consumer names and target-specific tool commands. If any of
# these appear as a Python string literal in the generic pack engine, that
# is target leakage (spec `§6`/`§9`'s "no target-name branch in generic
# engine" scenario) -- the pack must derive every one of these from a
# target-authored input file, never hardcode one.
_FORBIDDEN_TARGET_LITERALS_V2 = frozenset(
    {"AgentEscala", "agent_escala", "InterLeitos", "interleitos", "pytest", "mypy", "flake8", "ruff"}
)


def test_no_target_specific_literal_in_the_generic_pack_engine() -> None:
    offenders: list[str] = []
    for module_path in TARGET_PACK_MODULE_PATHS:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for literal in _FORBIDDEN_TARGET_LITERALS_V2:
                    if literal in node.value:
                        offenders.append(f"{module_path.name}:{node.lineno}: {literal!r} in {node.value!r}")
    assert not offenders, f"target-specific literal found in generic pack engine: {offenders}"


def test_no_target_specific_literal_in_shipped_templates() -> None:
    offenders: list[str] = []
    for template_path in TEMPLATES_DIR.rglob("*"):
        if not template_path.is_file():
            continue
        text = template_path.read_text(encoding="utf-8")
        for literal in ("AgentEscala", "agent_escala", "InterLeitos", "interleitos"):
            if literal in text:
                offenders.append(f"{template_path.relative_to(TEMPLATES_DIR)}: {literal!r}")
    assert not offenders, f"target-specific literal found in a shipped template: {offenders}"


# No GitHub branch-protection/required-check-promotion-shaped API call
# anywhere in the pack engine -- structural proof that "SHADOW_FULL never
# self-promotes to a required/default check" (spec `§8`) is an ABSENT
# capability, not merely an unused one. AST-based (identifiers/attribute
# names actually used in CODE), not a raw-text scan -- this file's own
# docstrings legitimately NAME the absent capability in prose to explain
# its absence, which a text-level regex cannot distinguish from a real call.
_FORBIDDEN_API_IDENTIFIER_RE = re.compile(
    r"branch[_-]?protection|required[_-]?status[_-]?checks|set[_-]?default[_-]?branch", re.IGNORECASE
)


def test_no_branch_protection_or_required_check_promotion_capability_exists() -> None:
    offenders: list[str] = []
    for module_path in TARGET_PACK_MODULE_PATHS:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name and _FORBIDDEN_API_IDENTIFIER_RE.search(name):
                offenders.append(f"{module_path.name}:{node.lineno}: {name!r}")
    assert not offenders, f"branch-protection/required-check-promotion shaped code found in: {offenders}"


# PR-C1: `contracts_v2.Repository`'s own regex is the ONLY authority that
# classifies an owner/name repository shape (`TargetInstallReceiptV2.
# target_repo`, `TargetPackInstallIdentityV2.target_repo`). A private CLI
# or per-module regex reimplementing the same classification would
# silently reintroduce a second, independently-maintained definition that
# can drift from `Repository`'s -- exactly the class of defect the
# SafeText -> Repository tightening exists to close for good. BEHAVIORAL,
# not string-content matching: any newly-added compiled regex, anywhere
# in the scanned modules, that happens to accept "owner/repo" and reject
# "not-a-repository" is flagged, regardless of its exact spelling.
_CLI_MODULE_PATH_V2 = REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py"


def test_no_duplicated_repository_shape_authority_outside_contracts_v2() -> None:
    offenders: list[str] = []
    for module_path in (*TARGET_PACK_MODULE_PATHS, _CLI_MODULE_PATH_V2):
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_re_compile = (
                isinstance(func, ast.Attribute)
                and func.attr == "compile"
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
            )
            if not is_re_compile or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            pattern = node.args[0].value
            if not isinstance(pattern, str):
                continue
            try:
                compiled = re.compile(pattern)
            except re.error:
                continue
            if compiled.fullmatch("owner/repo") and not compiled.fullmatch("not-a-repository"):
                offenders.append(f"{module_path.name}:{node.lineno}: {pattern!r}")
    assert not offenders, (
        f"a second regex independently classifying owner/name repository shape was found "
        f"outside contracts_v2.Repository: {offenders}"
    )


def test_bounded_artifact_read_always_passes_an_explicit_size_argument() -> None:
    """`#203-C2`: `_observe_bounded_artifact_v2` promises a BOUNDED read
    of the two `.aiops` artifacts (never materialising an arbitrarily
    large target-authored document before deciding it is over budget).
    A `stream.read()` call with no argument reads the WHOLE remaining
    file regardless of `_ARTIFACT_BYTE_LIMIT_V2` -- mechanically proves
    every `.read(...)` call inside this function passes an explicit
    argument, not merely that the CURRENT test fixtures happen to be
    small enough for the difference to go unnoticed."""

    tree = _parse(VALIDATE_MODULE_PATH)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_observe_bounded_artifact_v2"):
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "read"
                and not call.args
                and not call.keywords
            ):
                offenders.append(f"line {call.lineno}: stream.read() with no size argument")
    assert not offenders, f"an unbounded whole-file read exists in _observe_bounded_artifact_v2: {offenders}"


# `#203-C1.amend`: `target_pack_build_v2` is the ONE authority for the
# shipped seed profile's placeholder identity (see its own docstring's
# "single source of truth" claim for pack material). A private second
# literal in the writer (`operation`) or the reader (`validate`) would
# resurrect exactly the desynchronised-authorities class `#203-C1`
# removed for `Repository`/`RelativePath` -- this proves neither module
# independently spells the placeholder, both import the shared constant,
# and the shipped template still carries the value the constant names.
BUILD_MODULE_PATH_V2 = APP_DIR / "target_pack_build_v2.py"
_SEED_PLACEHOLDER_CONSUMER_MODULES_V2 = (
    APP_DIR / "target_pack_operation_v2.py",
    VALIDATE_MODULE_PATH,
)


def test_seed_placeholder_has_exactly_one_authority() -> None:
    import app.agent_review.target_pack_build_v2 as build_module

    placeholder_value = build_module.SEED_PROFILE_IDENTITY_PLACEHOLDER_V2

    # 1. No OTHER target-pack production module spells the placeholder as
    # its own string literal -- only the owning module may.
    offenders: list[str] = []
    for module_path in TARGET_PACK_MODULE_PATHS:
        if module_path == BUILD_MODULE_PATH_V2:
            continue
        tree = _parse(module_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == placeholder_value:
                offenders.append(f"{module_path.name}:{node.lineno}")
    assert not offenders, (
        f"a target-pack module other than {BUILD_MODULE_PATH_V2.name} spells the seed "
        f"placeholder as its own literal instead of importing it: {offenders}"
    )

    # 2. Both known consumers actually IMPORT the shared name from the
    # owning module (not merely avoid re-spelling it by coincidence).
    for module_path in _SEED_PLACEHOLDER_CONSUMER_MODULES_V2:
        tree = _parse(module_path)
        imported = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "app.agent_review.target_pack_build_v2"
                and any(alias.name == "SEED_PROFILE_IDENTITY_PLACEHOLDER_V2" for alias in node.names)
            ):
                imported = True
                break
        assert imported, f"{module_path.name} does not import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2 from target_pack_build_v2"

    # 3. The shipped seed template's identity still carries the SAME value
    # the constant names -- the constant is not just internally consistent
    # with itself, it matches the real shipped material.
    template_path = TEMPLATES_DIR / "target-profile.v2.yaml"
    template_text = template_path.read_text(encoding="utf-8")
    assert f"repo: {placeholder_value}" in template_text, (
        f"the shipped seed template no longer carries the placeholder value "
        f"{placeholder_value!r} that SEED_PROFILE_IDENTITY_PLACEHOLDER_V2 names"
    )


# ---------------------------------------------------------------------
# Codex Round 2, P2-B: the operative spec's CURRENT deferral list and the
# CLI parser's exposed subcommands cannot contradict each other.
# ---------------------------------------------------------------------

_SPEC_PATH_V2 = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md"
_SPEC_DEFERRED_HEADING_V2 = "## 14. Deferred (explicitly, not silently)"


def _cli_exposed_subcommands_v2() -> set[str]:
    """Structural, not textual: the names the argparse surface actually
    registers via `sub.add_parser("<name>", ...)`."""

    tree = _parse(_CLI_MODULE_PATH_V2)
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
    return names


def _spec_deferred_subcommand_bullet_v2() -> str:
    """The one bullet in the operative spec's deferral section that
    ENUMERATES deferred subcommands, identified semantically (it is the
    bullet naming `rollback`, the last subcommand in the CLI surface),
    never positionally.

    Deliberately scoped to that single bullet: the section legitimately
    keeps deferring non-subcommand work, and the rest of the document
    legitimately contains historical mentions of `deferred` that must NOT
    be read as current classification."""

    text = _SPEC_PATH_V2.read_text(encoding="utf-8")
    assert _SPEC_DEFERRED_HEADING_V2 in text, "the operative spec's deferral section heading moved; update this test deliberately"
    section = text.split(_SPEC_DEFERRED_HEADING_V2, 1)[1]
    bullets = [chunk for chunk in section.split("\n- ") if chunk.strip()]
    enumerating = [chunk for chunk in bullets if "`rollback`" in chunk]
    assert len(enumerating) == 1, (
        f"expected exactly one bullet enumerating deferred subcommands (the one naming `rollback`), found {len(enumerating)}"
    )
    return enumerating[0]


def test_operative_spec_does_not_defer_a_subcommand_the_cli_exposes() -> None:
    """PR #244 exposed `validate` while the operative specification still
    listed it as a deferred, unwritten subcommand. Because that document
    declares itself the authority maintainers cite for deferred-subcommand
    classification, the two states must not disagree."""

    exposed = _cli_exposed_subcommands_v2()
    assert "validate" in exposed, "the CLI no longer exposes validate; this test's premise changed"

    deferred_bullet = _spec_deferred_subcommand_bullet_v2()
    contradictions = sorted(name for name in exposed if f"`{name}`" in deferred_bullet)
    assert not contradictions, (
        f"the operative spec's deferral bullet still classifies {contradictions} as deferred "
        f"while the CLI parser exposes them: {sorted(exposed)}"
    )


def test_operative_spec_still_defers_the_genuinely_unshipped_subcommands() -> None:
    """The reconciliation must not have over-corrected into claiming the
    whole `#203` surface ships."""

    deferred_bullet = _spec_deferred_subcommand_bullet_v2()
    for name in ("conformance", "install-workflows", "upgrade", "rollback"):
        assert f"`{name}`" in deferred_bullet, f"{name} is not shipped but the spec stopped deferring it"
        assert name not in _cli_exposed_subcommands_v2(), f"{name} is exposed by the CLI but still listed as deferred"


# ---------------------------------------------------------------------
# Codex Round 3: EVERY filesystem resolution reachable from
# `run_validate_v2` must sit inside a typed observation boundary.
#
# Load-bearing. Round 2 closed the root observer's `.resolve()`; Round 3
# proved leaf-by-leaf coverage was insufficient, because `.aiops`, the
# two `.aiops` artifacts and every ledger claim resolve through the
# SHARED containment authority, which translates RuntimeError/ValueError
# into PlanError but lets OSError propagate untyped. These tests make
# the abstraction -- not the individual catch sites -- the thing that is
# enforced, so a future consumer cannot reintroduce a private path.
# ---------------------------------------------------------------------

_CONTAINED_RESOLUTION_ADAPTER_V2 = "_resolve_contained_path_v2"
_ROOT_OBSERVER_V2 = "_observe_root_v2"


def _enclosing_function_by_lineno_v2(tree: ast.Module) -> dict[int, str]:
    """Maps every line inside a top-level function body to that
    function's name, so a call node can be attributed to its owner."""

    owners: dict[int, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            for line in range(node.lineno, end + 1):
                owners[line] = node.name
    return owners


def test_containment_authority_is_called_only_from_the_single_adapter() -> None:
    """`resolve_within_target_root_v2` is the pack's ONE definition of
    containment and is deliberately not re-implemented here -- but every
    validate consumer must reach it through this module's single typed
    adapter, never directly, or each new consumer becomes another place
    an untyped OSError can escape."""

    tree = _parse(VALIDATE_MODULE_PATH)
    owners = _enclosing_function_by_lineno_v2(tree)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "resolve_within_target_root_v2":
                owner = owners.get(node.lineno, "<module level>")
                if owner != _CONTAINED_RESOLUTION_ADAPTER_V2:
                    offenders.append(f"{owner}:{node.lineno}")
    assert not offenders, (
        f"resolve_within_target_root_v2 is called outside {_CONTAINED_RESOLUTION_ADAPTER_V2}: {offenders}. "
        f"Route the new consumer through the adapter instead of catching PlanError/OSError locally."
    )


def test_the_contained_resolution_adapter_exists_and_types_both_failure_families() -> None:
    """Guards the adapter's own totality: catching only PlanError there
    would recreate the exact Round-3 defect at the one place every
    consumer now depends on."""

    tree = _parse(VALIDATE_MODULE_PATH)
    adapter = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == _CONTAINED_RESOLUTION_ADAPTER_V2
        ),
        None,
    )
    assert adapter is not None, f"{_CONTAINED_RESOLUTION_ADAPTER_V2} is missing"

    handled: set[str] = set()
    for node in ast.walk(adapter):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
            handled.add(node.type.id)
    assert {"PlanError", "OSError"} <= handled, (
        f"{_CONTAINED_RESOLUTION_ADAPTER_V2} must type BOTH failure families; it handles {sorted(handled)}"
    )


def test_raw_path_resolve_is_confined_to_the_root_observer() -> None:
    """The root observer legitimately calls `Path.resolve` directly: it
    observes the root ITSELF, which has no enclosing root to be contained
    in. Every other resolution is of a descendant and belongs to the
    containment adapter. This is what caught the redundant second
    `.resolve()` at the registry-seeding site, which re-resolved a path
    the authority had already returned."""

    tree = _parse(VALIDATE_MODULE_PATH)
    owners = _enclosing_function_by_lineno_v2(tree)
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve"
        ):
            owner = owners.get(node.lineno, "<module level>")
            if owner not in {_ROOT_OBSERVER_V2, _CONTAINED_RESOLUTION_ADAPTER_V2}:
                offenders.append(f"{owner}:{node.lineno}")
    assert not offenders, (
        f"raw .resolve() called outside {_ROOT_OBSERVER_V2}/{_CONTAINED_RESOLUTION_ADAPTER_V2}: {offenders}. "
        f"Use the Path the containment authority already returned, or route through the adapter."
    )


# ---------------------------------------------------------------------
# PR-D0 (Round 1): documentation-truth gate, rebuilt as an explicit
# projection boundary.
#
# The first cut of this block collapsed representation and interpretation
# into single `_check_*` functions asserting on substrings. That projection
# was lossy in four independent ways -- it lost the SUBJECT a claim is
# about, the ROLE a number plays, the POLARITY of a status word, and the
# EQUIVALENCE of two accurate wordings -- so it was simultaneously
# under-discriminating (different meanings, same signal) and
# over-discriminating (same meaning, different signal). The layering is now
# explicit:
#
#     document text
#       -> subject-bounded region      (uniqueness-enforced)
#       -> document-specific projector (representation)
#       -> typed projection            (one carrier per claim actually made)
#       -> one shared validator        (interpretation)
#       -> assert
#
# THREE DISTINCT AUTHORITIES, never derived from one another:
#
#     operative spec §4 command block      -> DECLARED SURFACE
#                                             which subcommands exist as product
#     operative spec §4 status paragraph   -> CANONICAL LIFECYCLE
#                                             which are canonical on master
#     argparse of the analyzed source tree -> SUBJECT AVAILABILITY
#                                             which commands exist in THIS tree
#     target_pack_validate_v2 constants    -> VALIDATE INVENTORY
#
# The forbidden collapse, stated explicitly because it is the one a future
# maintainer is most likely to reintroduce:
#
#     deferred_on_master != declared_surface - exposed_in_subject
#
# On a future candidate branch argparse legitimately exposes a command that
# `master` does not yet have (PR-C will expose `conformance`), and CURRENT
# documentation must keep calling it deferred until that PR merges. Deriving
# the documentary expectation from argparse would force the candidate to
# claim canonicality before merging -- exactly the candidate-vs-canonical
# collapse this whole reconciliation exists to prevent.
#
# EPISTEMIC CEILING of this gate. It statically proves: the normative
# surface is parseable; the lifecycle paragraph partitions that surface;
# CURRENT documents agree with the lifecycle authority; commands called
# canonical exist in the analyzed tree; inventory claims agree with the
# production-derived inventory. It does NOT prove that the §4 lifecycle
# paragraph itself matches live GitHub `master`, that a candidate command
# has merged, or any forge/PR state. Those remain live-forge facts and must
# be revalidated against the forge for any candidate-to-canonical
# transition, merge qualification, or post-merge canonical-truth claim.
# ---------------------------------------------------------------------

README_PATH_V2 = REPO_ROOT / "README.md"
PROJECT_STATUS_PATH_V2 = REPO_ROOT / "docs" / "PROJECT_STATUS.md"
ARCHITECTURE_PATH_V2 = REPO_ROOT / "docs" / "ARCHITECTURE.md"
CURRENT_CHECKPOINT_PATH_V2 = REPO_ROOT / "docs" / "engineering" / "CURRENT_CHECKPOINT.md"
TARGET_PACK_DOC_PATH_V2 = REPO_ROOT / "docs" / "AGENT_REVIEW_V2_TARGET_PACK.md"
CHANGELOG_PATH_V2 = REPO_ROOT / "CHANGELOG.md"


# --- L1: bounded observation -----------------------------------------


def _bounded_region_v2(
    text: str, start_anchor: str, end_anchor: str | None, *, require_unique_start: bool = True
) -> str:
    """Extracts the text between two stable anchors, failing loudly rather
    than silently taking a first match.

    `require_unique_start` is the default and is load-bearing: the original
    ARCHITECTURE check anchored on `- **Implementado:**`, which occurs 14
    times in that document, so it silently read Orchestrator Core's bullet
    instead of the target pack's. An anchor that is meant to identify one
    subject but matches many is a defect, not a convenience -- callers that
    genuinely need a repeated anchor must first narrow to a subject region
    where it is unique (see `_architecture_target_pack_section_v2`)."""

    assert start_anchor in text, f"anchor moved: {start_anchor!r} not found; update this test deliberately"
    if require_unique_start:
        occurrences = text.count(start_anchor)
        assert occurrences == 1, (
            f"anchor {start_anchor!r} is ambiguous ({occurrences} occurrences) in the given text; "
            f"bound the subject first instead of taking the first match"
        )
    after = text.split(start_anchor, 1)[1]
    if end_anchor is None:
        return start_anchor + after
    assert end_anchor in after, f"end anchor moved: {end_anchor!r} not found after {start_anchor!r}"
    return start_anchor + after.split(end_anchor, 1)[0]


def _backticked_names_v2(text: str) -> list[str]:
    return re.findall(r"`([a-z][a-z0-9-]*)`", text)


# --- Authority A: the declared subcommand surface ---------------------


def _operative_spec_declared_subcommands_v2() -> frozenset[str]:
    """THE product's subcommand surface, read from the operative spec's §4
    normative CLI block.

    Deliberately NOT a set literal in this file. A hand-written copy of the
    seven names would be a second authority synchronized by hand -- the exact
    defect this workstream keeps removing. For the same reason there is no
    cardinality assertion either: a `len(...) == 7` check would just hide the
    same hand-written authority behind a number, and would make the
    surface-authority mutation impossible to observe. The spec owns both
    membership and cardinality; this function only proves its own parsing
    integrity."""

    text = _SPEC_PATH_V2.read_text(encoding="utf-8")
    section = _bounded_region_v2(text, "## 4. CLI surface", "## 5. ")
    blocks = re.findall(r"```text\n(.*?)```", section, flags=re.DOTALL)
    assert blocks, "the §4 CLI-surface fenced block moved; update this test deliberately"
    names: list[str] = []
    for line in blocks[0].splitlines():
        if not line.strip() or line[:1].isspace():
            continue  # indented continuation lines are arguments, never commands
        names.append(line.split()[0])
    assert names, "no subcommand names extracted from the §4 CLI-surface block"
    assert len(names) == len(set(names)), f"duplicate subcommand names in the §4 block: {names}"
    return frozenset(names)


# --- Authority B: the canonical lifecycle -----------------------------


@dataclass(frozen=True)
class _CanonicalLifecycleProjectionV2:
    """The spec's own statement of which subcommands are canonical on
    `master` and which remain deferred."""

    canonical: frozenset[str]
    deferred: frozenset[str]


_SPEC_CANONICAL_CLAUSE_RE_V2 = re.compile(
    r"above\)\.\*\*\s*(?P<names>.*?)\s+are implemented and \*\*canonical", re.DOTALL
)
_SPEC_DEFERRED_CLAUSE_RE_V2 = re.compile(
    r"\)\.\s*(?P<names>[^.]*?)\s+remain\s+specified here and\s+deferred", re.DOTALL
)


def _operative_spec_lifecycle_projection_v2() -> _CanonicalLifecycleProjectionV2:
    """Reads §4's implementation-status paragraph with a closed grammar:
    one clause naming what `… are implemented and **canonical on master**`,
    one naming what `… remain specified here and deferred`.

    The clause boundaries are matched explicitly rather than by splitting on
    a status word, because the paragraph legitimately contains other
    backticked tokens -- notably the merge SHA -- that are not subcommand
    names. Deliberately NOT filtered against `declared_surface`: doing so
    would mask a lifecycle paragraph that classifies a command the surface
    no longer declares, which is precisely the divergence the
    surface-authority mutation must expose."""

    text = _SPEC_PATH_V2.read_text(encoding="utf-8")
    paragraph = _bounded_region_v2(text, "**Implementation status", "The distinction between")
    canonical_match = _SPEC_CANONICAL_CLAUSE_RE_V2.search(paragraph)
    deferred_match = _SPEC_DEFERRED_CLAUSE_RE_V2.search(paragraph)
    assert canonical_match, (
        "the §4 status paragraph's canonical-on-master phrasing is not recognized by this grammar; "
        "extend the grammar deliberately rather than loosening it"
    )
    assert deferred_match, (
        "the §4 status paragraph's deferral phrasing is not recognized by this grammar; "
        "extend the grammar deliberately rather than loosening it"
    )
    return _CanonicalLifecycleProjectionV2(
        canonical=frozenset(_backticked_names_v2(canonical_match.group("names"))),
        deferred=frozenset(_backticked_names_v2(deferred_match.group("names"))),
    )


# --- Typed projections: one carrier per claim a region actually makes --


@dataclass(frozen=True)
class _FullSubcommandLifecycleProjectionV2:
    canonical: frozenset[str]
    deferred: frozenset[str]


@dataclass(frozen=True)
class _DeferredSubcommandProjectionV2:
    deferred: frozenset[str]


@dataclass(frozen=True)
class _LifecycleProjectionV2:
    validate: str  # canonical | candidate_only | deferred | contradictory | unknown


@dataclass(frozen=True)
class _InventoryProjectionV2:
    total: int
    locally_evaluable: int
    permanently_unavailable: int


_LIFECYCLE_CANONICAL_V2 = "canonical"
_LIFECYCLE_CANDIDATE_ONLY_V2 = "candidate_only"
_LIFECYCLE_DEFERRED_V2 = "deferred"
_LIFECYCLE_CONTRADICTORY_V2 = "contradictory"
_LIFECYCLE_UNKNOWN_V2 = "unknown"

_CANDIDATE_ONLY_MARKERS_V2 = (
    "PR #244 candidate",
    "not canonical yet",
    "open and Draft at this writing",
)


def _classify_validate_lifecycle_v2(region: str, positive_phrases: tuple[str, ...]) -> str:
    """Closed, per-region grammar. Never `"canonical" in region` -- that
    accepts "not canonical" verbatim, which is how the first cut of this
    gate let a negated status pass.

    Only the controlled positive phrasing a region actually uses counts as
    canonical, and a known negation of that same phrasing is classified
    `contradictory` rather than silently ignored. Wording this grammar does
    not recognize is `unknown`, never optimistically accepted: a reword must
    surface as a loud failure telling the author to extend the grammar
    deliberately. There is deliberately no general not/never/nao/nunca
    detector -- that would be ad-hoc natural-language parsing in pytest, and
    would become the next lossy layer."""

    for marker in _CANDIDATE_ONLY_MARKERS_V2:
        if marker in region:
            return _LIFECYCLE_CANDIDATE_ONLY_V2
    for phrase in positive_phrases:
        negated = [
            phrase.replace("are ", "are not ", 1),
            phrase.replace("is ", "is not ", 1),
            phrase.replace("is ", "is **not** ", 1),
            phrase.replace("are ", "are **not** ", 1),
        ]
        if any(candidate in region for candidate in negated if candidate != phrase):
            return _LIFECYCLE_CONTRADICTORY_V2
    if "**not** canonical" in region or "is not canonical" in region or "não é canônic" in region:
        return _LIFECYCLE_CONTRADICTORY_V2
    for phrase in positive_phrases:
        if phrase in region:
            return _LIFECYCLE_CANONICAL_V2
    return _LIFECYCLE_UNKNOWN_V2


# --- L2: one projector per surface ------------------------------------
#
# Each projector returns ONLY what its region actually asserts. A single
# all-fields carrier would force a partial surface (§14 says "these four
# remain deferred", nothing about init/doctor) to fabricate claims it never
# makes: absence of assertion is not an assertion inferred from elsewhere.
#
# `surface_kind` is declared per row, because CURRENT regions legitimately
# carry non-subcommand capabilities (operation-plan binding, H1-A identity
# hardening, #203-C1 contracts, trusted-check inventory integration). Those
# must never be compared against the CLI surface -- doing so would be a
# false RED -- but on a surface that is purely a subcommand list, an
# unexpected member means the row drifted and IS a failure.

_SURFACE_PURE_SUBCOMMAND_V2 = "pure_subcommand"
_SURFACE_MIXED_CAPABILITY_V2 = "mixed_capability"


def _project_readme_v2(text: str, declared_surface: frozenset[str]) -> _FullSubcommandLifecycleProjectionV2:
    """README's target-pack row. Two wordings are equally accurate and must
    project identically -- an explicit deferral list, or the generic "demais
    subcomandos deferidos". Computing the complement for the generic form
    interprets THIS DOCUMENT's own "remaining subcommands" phrase against the
    declared surface; it does not derive master lifecycle from argparse."""

    row = _bounded_region_v2(text, "| Target Pack v2 |", "\n")
    cells = row.split("|")
    assert len(cells) >= 4, f"README target-pack row lost its expected column shape: {row!r}"
    notes = cells[3]
    implemented_part, sep, deferred_part = notes.partition(";")
    assert sep, f"README target-pack notes cell lost its implemented/deferred split: {notes!r}"
    assert "implementados" in implemented_part, f"README implemented clause moved: {implemented_part!r}"
    assert "deferid" in deferred_part, f"README deferred clause moved: {deferred_part!r}"

    canonical = frozenset(n for n in _backticked_names_v2(implemented_part) if n in declared_surface)
    explicit_deferred = frozenset(n for n in _backticked_names_v2(deferred_part) if n in declared_surface)
    if explicit_deferred:
        deferred = explicit_deferred
    else:
        assert "demais subcomandos" in deferred_part, (
            f"README deferred clause names no subcommand and is not the generic form: {deferred_part!r}"
        )
        deferred = declared_surface - canonical
    return _FullSubcommandLifecycleProjectionV2(canonical=canonical, deferred=deferred)


def _project_project_status_v2(text: str, declared_surface: frozenset[str]) -> _FullSubcommandLifecycleProjectionV2:
    """Mixed-capability narrative: the IMPLEMENTED sentence also names
    `operation-plan binding` and the H1-A identity hardening, which are
    capabilities, not CLI subcommands, and are excluded from the projection
    rather than compared against the CLI surface."""

    region = _bounded_region_v2(text, "### Target Pack v2", "See [target pack]")
    canonical_clause, sep, deferred_clause = region.partition("`NOT YET IMPLEMENTED`")
    assert sep, "PROJECT_STATUS lost its NOT YET IMPLEMENTED marker"
    assert "`IMPLEMENTED`" in canonical_clause, "PROJECT_STATUS lost its IMPLEMENTED marker"
    return _FullSubcommandLifecycleProjectionV2(
        canonical=frozenset(n for n in _backticked_names_v2(canonical_clause) if n in declared_surface),
        deferred=frozenset(n for n in _backticked_names_v2(deferred_clause.split(".", 1)[0]) if n in declared_surface),
    )


def _architecture_target_pack_section_v2(text: str) -> str:
    """Bind the SUBJECT before reading any bullet. `- **Implementado:**`
    occurs once per component (14 times document-wide); the first belongs to
    Orchestrator Core. Narrowing to the target-pack component first is what
    makes the bullet anchors unique and the reading honest."""

    return _bounded_region_v2(
        text, "### Componente: AgentReview Target Pack (v2)", "### Componente: CAEM (consumo)"
    )


def _project_architecture_v2(text: str, declared_surface: frozenset[str]) -> _FullSubcommandLifecycleProjectionV2:
    section = _architecture_target_pack_section_v2(text)
    canonical_clause = _bounded_region_v2(section, "- **Implementado:**", "- **Deferido:**")
    deferred_clause = _bounded_region_v2(section, "- **Deferido:**", "- **Garantia:**")
    return _FullSubcommandLifecycleProjectionV2(
        canonical=frozenset(n for n in _backticked_names_v2(canonical_clause) if n in declared_surface),
        deferred=frozenset(n for n in _backticked_names_v2(deferred_clause) if n in declared_surface),
    )


def _project_current_checkpoint_v2(text: str, declared_surface: frozenset[str]) -> _DeferredSubcommandProjectionV2:
    """Mixed-capability narrative that states a deferral list explicitly and
    describes canonical capabilities in prose; only the deferral claim is
    projected."""

    region = _bounded_region_v2(text, "- **Target pack (`#203`):**", "- **ProjectOps v1:**")
    assert "não implementados" in region, "CURRENT_CHECKPOINT lost its not-implemented marker"
    clause = region.rsplit("não implementados", 1)[0].rsplit(".", 2)[-1]
    return _DeferredSubcommandProjectionV2(
        deferred=frozenset(n for n in _backticked_names_v2(clause) if n in declared_surface)
    )


def _project_target_pack_status_table_v2(
    text: str, declared_surface: frozenset[str]
) -> _FullSubcommandLifecycleProjectionV2:
    """Mixed-capability table: it also carries `operation-plan binding`
    (IMPLEMENTED) and `trusted-check inventory integration` (PLANNED, a
    third status). Rows whose subject is not a declared subcommand are
    preserved by the document and skipped by the projection."""

    region = _bounded_region_v2(text, "## Subcommand status", "## What this is")
    canonical: set[str] = set()
    deferred: set[str] = set()
    for line in region.splitlines():
        if not line.startswith("| "):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        subject_names = [n for n in _backticked_names_v2(cells[1]) if n in declared_surface]
        if not subject_names:
            continue
        assert len(subject_names) == 1, f"ambiguous status-table subject: {cells[1]!r}"
        status_cell = cells[2]
        if "`IMPLEMENTED`" in status_cell:
            canonical.add(subject_names[0])
        elif "`DEFERRED`" in status_cell:
            deferred.add(subject_names[0])
        else:
            raise AssertionError(f"subcommand row with unrecognized status: {line!r}")
    return _FullSubcommandLifecycleProjectionV2(canonical=frozenset(canonical), deferred=frozenset(deferred))


def _project_target_pack_deferred_section_v2(
    text: str, declared_surface: frozenset[str]
) -> _DeferredSubcommandProjectionV2:
    region = _bounded_region_v2(text, "## Deferred", None)
    first_bullet = region.split("\n- ", 2)[1]
    return _DeferredSubcommandProjectionV2(
        deferred=frozenset(n for n in _backticked_names_v2(first_bullet) if n in declared_surface)
    )


def _project_operative_spec_status_lifecycle_v2(text: str) -> _LifecycleProjectionV2:
    region = _bounded_region_v2(text, "**Implementation status", "`doctor` is **READ-ONLY")
    return _LifecycleProjectionV2(
        validate=_classify_validate_lifecycle_v2(
            region, ("are implemented and **canonical\non `master`**", "are implemented and **canonical on `master`**")
        )
    )


def _project_operative_spec_deferred_bullet_lifecycle_v2(text: str) -> _LifecycleProjectionV2:
    region = _bounded_region_v2(text, "- `validate` is", "- `TrustedCheckInventoryV2`")
    return _LifecycleProjectionV2(
        validate=_classify_validate_lifecycle_v2(
            region, ("is **shipped and canonical on `master`**",)
        )
    )


def _project_labeled_inventory_v2(region: str) -> _InventoryProjectionV2:
    """Binds each count to the ROLE it claims. The first cut asserted only
    that each number's string occurred somewhere in the region, so swapping
    the total and the locally-evaluable count still passed."""

    def one(label_pattern: str, label: str) -> int:
        matches = re.findall(label_pattern, region)
        assert len(matches) == 1, f"expected exactly one {label!r} count, found {matches!r}"
        return int(matches[0])

    return _InventoryProjectionV2(
        total=one(r"(\d+) total dimensions", "total"),
        locally_evaluable=one(r"(\d+) locally evaluable", "locally evaluable"),
        # Two controlled wordings are in use ("permanently disclosed
        # `unavailable`" and "permanently `unavailable`"); both are accepted
        # explicitly, and anything else fails loudly rather than silently.
        permanently_unavailable=one(r"(\d+) permanently (?:disclosed )?`unavailable`", "permanently unavailable"),
    )


def _production_inventory_v2() -> _InventoryProjectionV2:
    import app.agent_review.target_pack_validate_v2 as validate_module

    total = len(validate_module.VALIDATE_CHECK_ORDER_V2)
    unavailable = {name for name, _ in validate_module.UNVALIDATED_CAPABILITIES_V2}
    return _InventoryProjectionV2(
        total=total,
        locally_evaluable=total - len(unavailable),
        permanently_unavailable=len(unavailable),
    )


# --- L3/L4: one shared validator --------------------------------------

_LIFECYCLE_SURFACES_V2: tuple[tuple[str, Path, object, str], ...] = (
    ("README.md target-pack row", README_PATH_V2, _project_readme_v2, _SURFACE_PURE_SUBCOMMAND_V2),
    ("PROJECT_STATUS.md Target Pack v2", PROJECT_STATUS_PATH_V2, _project_project_status_v2, _SURFACE_MIXED_CAPABILITY_V2),
    ("ARCHITECTURE.md target-pack bullets", ARCHITECTURE_PATH_V2, _project_architecture_v2, _SURFACE_MIXED_CAPABILITY_V2),
    ("CURRENT_CHECKPOINT.md target-pack bullet", CURRENT_CHECKPOINT_PATH_V2, _project_current_checkpoint_v2, _SURFACE_MIXED_CAPABILITY_V2),
    ("AGENT_REVIEW_V2_TARGET_PACK.md status table", TARGET_PACK_DOC_PATH_V2, _project_target_pack_status_table_v2, _SURFACE_MIXED_CAPABILITY_V2),
    ("AGENT_REVIEW_V2_TARGET_PACK.md Deferred section", TARGET_PACK_DOC_PATH_V2, _project_target_pack_deferred_section_v2, _SURFACE_MIXED_CAPABILITY_V2),
)

_VALIDATE_LIFECYCLE_SURFACES_V2: tuple[tuple[str, Path, object], ...] = (
    ("operative spec §4 status paragraph", _SPEC_PATH_V2, _project_operative_spec_status_lifecycle_v2),
    ("operative spec §14 validate bullet", _SPEC_PATH_V2, _project_operative_spec_deferred_bullet_lifecycle_v2),
)


def test_the_three_authorities_are_internally_coherent() -> None:
    """The spec's lifecycle must partition the spec's own declared surface,
    and anything it calls canonical must actually exist in this tree.

    Deliberately absent: any assertion that `deferred == declared_surface -
    exposed_in_subject`. That equation is false by design on a candidate
    branch, where argparse exposes a command `master` does not yet have."""

    declared_surface = _operative_spec_declared_subcommands_v2()
    lifecycle = _operative_spec_lifecycle_projection_v2()
    exposed_in_subject = _cli_exposed_subcommands_v2()

    assert declared_surface, "the operative spec declares no subcommand surface"
    assert lifecycle.canonical.isdisjoint(lifecycle.deferred), (
        f"the §4 status paragraph both canonicalizes and defers: {sorted(lifecycle.canonical & lifecycle.deferred)}"
    )
    assert (lifecycle.canonical | lifecycle.deferred) == declared_surface, (
        f"the §4 lifecycle does not partition the §4 declared surface; "
        f"unclassified={sorted(declared_surface - lifecycle.canonical - lifecycle.deferred)}, "
        f"unknown={sorted((lifecycle.canonical | lifecycle.deferred) - declared_surface)}"
    )
    assert lifecycle.canonical <= exposed_in_subject, (
        f"the spec calls {sorted(lifecycle.canonical - exposed_in_subject)} canonical, "
        f"but this source tree's CLI does not expose them"
    )


def test_every_current_target_pack_surface_agrees_with_the_lifecycle_authority() -> None:
    """Each CURRENT surface is projected with its own document-specific
    grammar into a typed claim, then compared against the SPEC's lifecycle
    authority -- never against argparse, which answers a different question."""

    declared_surface = _operative_spec_declared_subcommands_v2()
    lifecycle = _operative_spec_lifecycle_projection_v2()

    failures: list[str] = []
    for doc_name, path, project, _surface_kind in _LIFECYCLE_SURFACES_V2:
        text = path.read_text(encoding="utf-8")
        try:
            projection = project(text, declared_surface)
            if isinstance(projection, _FullSubcommandLifecycleProjectionV2):
                assert projection.canonical == lifecycle.canonical, (
                    f"canonical set differs: document={sorted(projection.canonical)} "
                    f"spec={sorted(lifecycle.canonical)}"
                )
            assert projection.deferred == lifecycle.deferred, (
                f"deferred set differs: document={sorted(projection.deferred)} spec={sorted(lifecycle.deferred)}"
            )
        except AssertionError as exc:
            failures.append(f"{doc_name} ({path.name}): {exc}")
    assert not failures, "CURRENT target-pack surface disagrees with the lifecycle authority:\n" + "\n".join(failures)


def test_validate_lifecycle_claims_reject_negated_and_unrecognized_wording() -> None:
    """Only the controlled positive phrasing passes. `contradictory`,
    `candidate_only`, `deferred` and `unknown` all fail -- there is no
    optimistic default and no `else -> canonical`."""

    failures: list[str] = []
    for doc_name, path, project in _VALIDATE_LIFECYCLE_SURFACES_V2:
        text = path.read_text(encoding="utf-8")
        try:
            projection = project(text)
            assert projection.validate == _LIFECYCLE_CANONICAL_V2, (
                f"validate lifecycle projects as {projection.validate!r}, not {_LIFECYCLE_CANONICAL_V2!r}"
            )
        except AssertionError as exc:
            failures.append(f"{doc_name} ({path.name}): {exc}")
    assert not failures, "validate lifecycle claim is not canonical:\n" + "\n".join(failures)


def test_documented_check_inventories_bind_each_count_to_its_label() -> None:
    """Both documents advertising the inventory are compared as whole typed
    projections against the production-derived one, so swapping two counts
    can no longer pass."""

    expected = _production_inventory_v2()
    changelog_region = _bounded_region_v2(
        CHANGELOG_PATH_V2.read_text(encoding="utf-8"),
        "bounded offline target-pack validation",
        "- **`agentreview-v2-target-pack` — installable target pack, first slice",
    )
    assert _project_labeled_inventory_v2(changelog_region) == expected, (
        f"CHANGELOG C2 inventory {_project_labeled_inventory_v2(changelog_region)} != production {expected}"
    )

    doc_region = _bounded_region_v2(
        TARGET_PACK_DOC_PATH_V2.read_text(encoding="utf-8"),
        "## What C2 added",
        "## Templates shipped",
    )
    assert _project_labeled_inventory_v2(doc_region) == expected, (
        f"target-pack doc C2 inventory {_project_labeled_inventory_v2(doc_region)} != production {expected}"
    )

    import app.agent_review.target_pack_validate_v2 as validate_module

    for dimension in sorted({name for name, _ in validate_module.UNVALIDATED_CAPABILITIES_V2}):
        assert dimension in changelog_region, f"CHANGELOG C2 entry does not name unavailable dimension {dimension!r}"


def test_changelog_first_slice_entry_does_not_predict_a_same_branch_delivery() -> None:
    """The `#223` entry may truthfully say validate was absent from the
    first slice -- it must not predict WHEN or WHERE it would ship, since
    that prediction was falsified (validate shipped via #243 then #244, two
    separate later PRs, not the same branch/PR)."""

    region = _bounded_region_v2(
        CHANGELOG_PATH_V2.read_text(encoding="utf-8"),
        "installable target pack, first slice",
        "- **AgentReview v2 required-check readiness wiring",
    )
    assert "same branch/PR" not in region, "CHANGELOG #223 entry still predicts a same-branch/PR delivery for validate"
    assert "first slice" in region, "CHANGELOG #223 entry lost its historical first-slice framing"
