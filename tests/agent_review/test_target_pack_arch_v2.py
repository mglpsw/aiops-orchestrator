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

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app" / "agent_review"
TEMPLATES_DIR = REPO_ROOT / "templates" / "agentreview-v2-target-pack"
DOCTOR_MODULE_PATH = APP_DIR / "target_pack_doctor_v2.py"
VALIDATE_MODULE_PATH = APP_DIR / "target_pack_validate_v2.py"
TARGET_PACK_MODULE_PATHS = sorted(APP_DIR.glob("target_pack_*.py"))

# Every module whose own docstring claims READ-ONLY BY CONSTRUCTION. A
# claim in prose is not a guarantee; each one below is held to the same
# mechanical proof (#203-S2 adds `validate` to what `doctor` established).
_READ_ONLY_MODULE_PATHS_V2 = (DOCTOR_MODULE_PATH, VALIDATE_MODULE_PATH)

# Attribute names that, called on ANY object, would indicate a write/mutate
# operation. Deliberately broad (matches the method name regardless of
# receiver) since `doctor` has no legitimate reason to call any of these on
# anything.
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
        "open",  # os.open / Path.open in write modes -- flagged conservatively
    }
)
_FORBIDDEN_MODULE_CALLS_V2 = frozenset({"remove", "mkdir", "makedirs", "rename", "replace", "unlink"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("module_path", _READ_ONLY_MODULE_PATHS_V2, ids=lambda p: p.name)
def test_read_only_module_calls_no_filesystem_mutating_primitive(module_path: Path) -> None:
    tree = _parse(module_path)
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS_V2:
            offenders.append(f"{module_path.name}:{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in _FORBIDDEN_MODULE_CALLS_V2:
            offenders.append(f"{module_path.name}:{func.id}() at line {node.lineno}")

    assert not offenders, (
        f"{module_path.name} calls a filesystem-mutating primitive, "
        f"violating its own 'READ-ONLY BY CONSTRUCTION' docstring guarantee: {offenders}"
    )


def test_run_doctor_v2_has_no_mutating_parameter() -> None:
    """A second, independent check: `run_doctor_v2` itself must never
    accept a `plan`/`force_overwrite_paths`/anything shaped like a write
    instruction -- if it ever did, the AST scan above could stop being
    sufficient (a future caller-supplied write could reach the filesystem
    without a literal `write_text`/`mkdir` call appearing in THIS file)."""

    tree = _parse(DOCTOR_MODULE_PATH)
    forbidden_param_names = {"plan", "force_overwrite_paths", "seed_content_by_path", "write", "apply"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_doctor_v2":
            all_args = [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]
            offending = [arg.arg for arg in all_args if arg.arg in forbidden_param_names]
            assert not offending, f"run_doctor_v2 accepts a write-shaped parameter: {offending}"
            return
    raise AssertionError("run_doctor_v2 not found in target_pack_doctor_v2.py")


def test_run_validate_v2_has_no_mutating_parameter() -> None:
    """Same second, independent check for `#203-S2`'s `run_validate_v2`:
    the AST scan above proves this module writes nothing ITSELF, but a
    write-shaped parameter would let a caller push a write through it
    without any literal write primitive appearing in this file."""

    tree = _parse(VALIDATE_MODULE_PATH)
    forbidden_param_names = {"plan", "force_overwrite_paths", "seed_content_by_path", "write", "apply"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_validate_v2":
            all_args = [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]
            offending = [arg.arg for arg in all_args if arg.arg in forbidden_param_names]
            assert not offending, f"run_validate_v2 accepts a write-shaped parameter: {offending}"
            return
    raise AssertionError("run_validate_v2 not found in target_pack_validate_v2.py")


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
