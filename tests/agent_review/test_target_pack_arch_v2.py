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
