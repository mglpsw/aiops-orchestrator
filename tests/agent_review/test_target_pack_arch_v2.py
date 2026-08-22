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

from app.agent_review.target_pack_runtime_authority_v2 import TARGET_PACK_CLI_COMMANDS_V2

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
_ALLOWED_OBSERVATION_OS_OPEN_FLAGS_V2 = frozenset(
    {
        "O_RDONLY",
        "O_PATH",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_CLOEXEC",
        "O_NONBLOCK",
    }
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _open_call_mode_offense(node: ast.Call) -> str | None:
    """Returns a description of the offense if this `.open(...)` call's
    mode is not statically provable as read-only, else `None`. Missing
    mode argument is an offense too -- `Path.open()`'s default `"r"` IS
    read-only, but requiring it explicit keeps the mode a single glance
    away from this scan, matching the discipline already established
    for `target_pack_validate_v2`'s own bounded-read call sites."""

    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    ):
        if len(node.args) < 2:
            return f"os.open() at line {node.lineno} has no explicit flags argument"

        names: set[str] = set()

        def collect_flags(expr: ast.expr) -> bool:
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
                return collect_flags(expr.left) and collect_flags(expr.right)
            if (
                isinstance(expr, ast.Attribute)
                and isinstance(expr.value, ast.Name)
                and expr.value.id == "os"
                and expr.attr.startswith("O_")
            ):
                names.add(expr.attr)
                return True
            if isinstance(expr, ast.Constant) and expr.value == 0:
                return True
            if (
                isinstance(expr, ast.Call)
                and isinstance(expr.func, ast.Name)
                and expr.func.id == "getattr"
                and len(expr.args) == 3
                and isinstance(expr.args[0], ast.Name)
                and expr.args[0].id == "os"
                and isinstance(expr.args[1], ast.Constant)
                and expr.args[1].value == "O_CLOEXEC"
                and isinstance(expr.args[2], ast.Constant)
                and expr.args[2].value == 0
            ):
                names.add("O_CLOEXEC")
                return True
            return False

        if not collect_flags(node.args[1]):
            return f"os.open() at line {node.lineno} has non-provable flags"
        forbidden = sorted(names - _ALLOWED_OBSERVATION_OS_OPEN_FLAGS_V2)
        if forbidden:
            return f"os.open() at line {node.lineno} uses write-shaped flags {forbidden}"
        if not ({"O_RDONLY", "O_PATH"} & names):
            return f"os.open() at line {node.lineno} has no read/object-binding flag"
        return None

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


def _read_only_offenders_v2(tree: ast.Module, *, module_name: str) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "open":
            offense = _open_call_mode_offense(node)
            if offense is not None:
                offenders.append(f"{module_name}:{offense}")
        elif isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS_V2:
            offenders.append(f"{module_name}:{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in _FORBIDDEN_MODULE_CALLS_V2:
            offenders.append(f"{module_name}:{func.id}() at line {node.lineno}")
    return offenders


def test_read_only_modules_call_no_filesystem_mutating_primitive() -> None:
    for module_path, _entry_point in _READ_ONLY_MODULES_V2:
        tree = _parse(module_path)
        offenders = _read_only_offenders_v2(tree, module_name=module_path.name)

        assert not offenders, (
            f"{module_path.name} calls a filesystem-mutating primitive (or an open() with a "
            f"non-statically-provable read-only mode), violating its own 'READ-ONLY BY "
            f"CONSTRUCTION' docstring guarantee: {offenders}"
        )


def test_doctor_has_one_non_path_content_open_and_it_is_provisionally_nonblocking() -> None:
    """A raced leaf type is discriminated before any potentially blocking read."""

    tree = _parse(DOCTOR_MODULE_PATH)
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1):
                owners[line] = function.name

    content_opens: list[tuple[str, set[str]]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "open"
            and len(node.args) >= 2
        ):
            continue
        names = {
            part.attr
            for part in ast.walk(node.args[1])
            if isinstance(part, ast.Attribute)
            and isinstance(part.value, ast.Name)
            and part.value.id == "os"
            and part.attr.startswith("O_")
        }
        if "O_PATH" not in names:
            content_opens.append((owners.get(node.lineno, "<module>"), names))

    assert content_opens == [
        (
            "_observe_regular_v2",
            {"O_RDONLY", "O_NOFOLLOW", "O_NONBLOCK"},
        )
    ]


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


def test_target_pack_writer_has_one_k_ex_orchestration_boundary() -> None:
    """R38: target mutations cannot silently bypass the live K capability.

    This intentionally proves the current sanctioned writer graph, rather
    than claiming every future Python file in the repository is incapable of
    writing a target.  ``install`` consumes a lease/binding; only ``apply``
    invokes it, and the CLI invokes only that canonical apply boundary.
    """

    install = _parse(APP_DIR / "target_pack_install_v2.py")
    apply = _parse(APP_DIR / "target_pack_apply_v2.py")
    cli = _parse(_CLI_MODULE_PATH_V2)

    for function_name in ("apply_install_plan_v2", "write_receipt_v2"):
        function = next(
            node for node in ast.walk(install) if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        keyword_names = {argument.arg for argument in function.args.kwonlyargs}
        assert {"lease", "target_binding"}.issubset(keyword_names), function_name

    def called_names(tree: ast.Module) -> set[str]:
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    assert "apply_install_plan_v2" in called_names(apply)
    assert "write_receipt_v2" in called_names(apply)
    assert "apply_install_plan_v2" not in called_names(cli)
    assert "write_receipt_v2" not in called_names(cli)
    assert "apply_authorized_target_pack_init_v2" in called_names(cli)


def _doctor_epoch_acquisitions_v2(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "acquire_target_pack_epoch_v2"
    ]


def _one_literal_shared_epoch_v2(tree: ast.Module) -> bool:
    acquisitions = _doctor_epoch_acquisitions_v2(tree)
    if len(acquisitions) != 1:
        return False
    exclusive = next((kw.value for kw in acquisitions[0].keywords if kw.arg == "exclusive"), None)
    return isinstance(exclusive, ast.Constant) and exclusive.value is False


def test_r39_successor_doctor_consumes_one_shared_epoch_while_validate_still_does_not() -> None:
    """Intentional successor to #258 R39; R40 remains unchanged."""

    doctor = _parse(DOCTOR_MODULE_PATH)
    validate = _parse(VALIDATE_MODULE_PATH)
    assert _one_literal_shared_epoch_v2(doctor)

    doctor_calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(doctor)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert "bind_target_root_for_observation_v2" in doctor_calls
    assert "materialize_and_bind_target_root_v2" not in doctor_calls
    assert "bind_target_root_v2" not in doctor_calls

    validate_epoch_imports = [
        node
        for node in ast.walk(validate)
        if isinstance(node, ast.ImportFrom) and node.module == "app.agent_review.target_pack_epoch_v2"
    ]
    assert not validate_epoch_imports


_DOCTOR_REACHABLE_LOCAL_FUNCTIONS_V2 = frozenset(
    {
        "run_doctor_v2",
        "_unknown_for_epoch_error_v2",
        "_unknown_for_epoch_oserror_v2",
        "_classify_root_subject_before_epoch_v2",
        "_classify_root_binding_failure_v2",
        "_snapshot_environment_keys_v2",
        "_check_profile_v2",
        "_check_receipt_v2",
        "_check_secret_names_v2",
        "__init__",
        "__enter__",
        "__exit__",
        "close",
        "_resolve_initial_v2",
        "_prepare_fd_v2",
        "_retain_fd_v2",
        "_register_fd_v2",
        "_release_observation_fds_v2",
        "_release_observation_fd_v2",
        "_root_fd_v2",
        "_is_root_self_v2",
        "_root_self_stat_v2",
        "_diagnose_component_open_error_v2",
        "_open_parent_directory_v2",
        "_open_leaf_path_fd_v2",
        "observe_directory_v2",
        "_observe_regular_v2",
        "observe_bytes_v2",
        "observe_sha256_v2",
        "_transient_current_lookup_v2",
        "_revalidate_root_v2",
        "revalidate_v2",
        "_raise_classified_observation_oserror_v2",
        "_raise_binding_primitive_oserror_v2",
        "_doctor_reason_for_plan_error_v2",
        "_profile_status_for_completed_negative_v2",
        "_file_type_v2",
        "_object_identity_v2",
        "_metadata_identity_v2",
    }
)


def test_doctor_observation_call_graph_registry_is_complete() -> None:
    """Moving or adding a reachable local helper requires deliberate review."""

    tree = _parse(DOCTOR_MODULE_PATH)
    definitions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # Python invokes the session's constructor/context protocol implicitly;
    # seed those protocol methods alongside the explicit entry point.
    reachable = {"run_doctor_v2", "__init__", "__enter__", "__exit__"}
    pending = ["run_doctor_v2", "__init__", "__enter__", "__exit__"]
    while pending:
        owner = pending.pop()
        for call in ast.walk(definitions[owner]):
            if not isinstance(call, ast.Call):
                continue
            called = None
            if isinstance(call.func, ast.Name):
                called = call.func.id
            elif isinstance(call.func, ast.Attribute):
                called = call.func.attr
            if called in definitions and called not in reachable:
                reachable.add(called)
                pending.append(called)
    assert reachable == _DOCTOR_REACHABLE_LOCAL_FUNCTIONS_V2


def _epoch_write_effects_v2(tree: ast.Module) -> set[tuple[str, str]]:
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1):
                owners[line] = function.name

    observed: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = None
        if isinstance(node.func, ast.Attribute):
            call_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            call_name = node.func.id
        if call_name == "mkdir":
            observed.add((owners.get(node.lineno, "<module>"), call_name))
        if call_name == "open" and (
            any(
                isinstance(part, ast.Attribute)
                and part.attr in {"O_CREAT", "O_WRONLY", "O_RDWR", "O_TRUNC", "O_APPEND"}
                for part in ast.walk(node)
            )
            or (
                len(node.args) >= 2
                and isinstance(node.args[1], ast.Name)
                and node.args[1].id == "carrier_flags"
            )
        ):
            observed.add((owners.get(node.lineno, "<module>"), call_name))
    return observed


def test_epoch_effect_domains_are_function_scoped_not_module_exempted() -> None:
    """Carrier writes and the pre-existing writer materializer are explicit."""

    epoch_path = APP_DIR / "target_pack_epoch_v2.py"
    tree = _parse(epoch_path)
    approved = {
        ("_open_protocol_directory_v2", "mkdir"),
        ("acquire_target_pack_epoch_v2", "open"),
        ("materialize_and_bind_target_root_v2", "mkdir"),
    }
    assert _epoch_write_effects_v2(tree) == approved

    acquire = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "acquire_target_pack_epoch_v2")
    carrier_open = next(
        node
        for node in ast.walk(acquire)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "carrier_flags"
    )
    carrier_assignment = next(
        node
        for node in ast.walk(acquire)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "carrier_flags" for target in node.targets)
    )
    carrier_names = {
        part.attr
        for part in ast.walk(carrier_assignment.value)
        if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name) and part.value.id == "os"
    }
    assert {"O_RDWR", "O_CREAT", "O_NOFOLLOW"} <= carrier_names
    dir_fd = next(kw.value for kw in carrier_open.keywords if kw.arg == "dir_fd")
    assert isinstance(dir_fd, ast.Name) and dir_fd.id == "namespace_fd"

    protocol_open = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_open_protocol_directory_v2"
    )
    protocol_mkdir = next(
        node
        for node in ast.walk(protocol_open)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkdir"
    )
    protocol_dir_fd = next(kw.value for kw in protocol_mkdir.keywords if kw.arg == "dir_fd")
    assert isinstance(protocol_dir_fd, ast.Name) and protocol_dir_fd.id == "parent_fd"

    materializer = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "materialize_and_bind_target_root_v2"
    )
    materialize_mkdir = next(
        node
        for node in ast.walk(materializer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkdir"
    )
    assert isinstance(materialize_mkdir.func.value, ast.Name)
    assert materialize_mkdir.func.value.id == "target_root"
    assert all(keyword.arg != "dir_fd" for keyword in materialize_mkdir.keywords)


def _containment_resolver_owners_v2(tree: ast.Module) -> list[str]:
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1):
                owners[line] = function.name

    return [
        owners.get(node.lineno, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_within_target_root_v2"
    ]


def test_doctor_never_releases_an_observation_fd_outside_the_typed_wrapper() -> None:
    """Codex round 5 (C3): every release must carry stage/relation taxonomy.

    Three duplicate-object branches called
    `root_binding.release_observation_fd_v2` directly, so an operational close
    error escaped `run_doctor_v2` as a raw traceback instead of report-zero
    UNKNOWN -- the class `R4`/`F24` closed, at call sites that fix missed.
    Behavioural REDs cover the reproduced instances; this makes the ABSTRACTION
    the enforced thing, so a fourth direct call site cannot reintroduce it.
    """

    tree = _parse(DOCTOR_MODULE_PATH)
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1):
                owners[line] = function.name

    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "release_observation_fd_v2"
        ):
            owner = owners.get(node.lineno, "<module>")
            if owner != "_release_observation_fds_v2":
                offenders.append(f"{owner}:{node.lineno}")
    assert not offenders, (
        "the epoch primitive's release was called outside the session's typed "
        f"wrapper, so an operational close error would escape untyped: {offenders}"
    )


def test_doctor_uses_the_same_containment_authority_for_initial_and_final_lookup() -> None:
    tree = _parse(DOCTOR_MODULE_PATH)
    assert sorted(_containment_resolver_owners_v2(tree)) == ["_resolve_initial_v2", "revalidate_v2"]


def test_doctor_content_and_environment_observation_choke_points_are_single() -> None:
    tree = _parse(DOCTOR_MODULE_PATH)
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(function.lineno, (function.end_lineno or function.lineno) + 1):
                owners[line] = function.name

    content_reads = [
        owners.get(node.lineno, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "read"
    ]
    assert content_reads == ["_observe_regular_v2"]

    path_content_reads = [
        f"{node.func.attr}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"read_bytes", "read_text"}
    ]
    assert not path_content_reads

    environment_keys = [
        owners.get(node.lineno, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "keys"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
        and node.func.value.attr == "environ"
    ]
    assert environment_keys == ["_snapshot_environment_keys_v2"]
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
        and node.value.attr == "environ"
    ]

    environment_references = [
        owners.get(node.lineno, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "environ"
    ]
    assert environment_references == ["_snapshot_environment_keys_v2"]


def _fd_inheritable_offenders_v2(tree: ast.Module) -> list[int]:
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "set_inheritable"
        ):
            continue
        if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant) or node.args[1].value is not False:
            offenders.append(node.lineno)
    return offenders


def test_every_doctor_and_epoch_fd_is_explicitly_non_inheritable() -> None:
    for path in (DOCTOR_MODULE_PATH, APP_DIR / "target_pack_epoch_v2.py"):
        assert not _fd_inheritable_offenders_v2(_parse(path)), path.name


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


def _runtime_authority_cli_commands_v2() -> frozenset[str]:
    """`C` -- the command identities the runtime authority declares.

    This replaces a scan for literal `sub.add_parser("<name>", ...)` calls.
    That scan observed `SourceOccurrenceIdentity` and promoted it to
    `RuntimeRoleIdentity`, which are different propositions -- and once the
    parser began iterating the authority (`add_parser(spec.name, ...)`) the
    scan lost its subject entirely and returned the empty set, silently
    vacating the non-exposure half of these invariants.

    This is a narrow projection of the authority, not a second definition of
    it. `C = K = P` (authority domain = configurator domain = real parser
    choices) is proved in `test_target_pack_runtime_authority_v2.py`; this
    module composes that proved relation with the operative spec, so it does
    not reconstruct `P` here. It also does not read the generated JSON view:
    that is `Projection(Authority)`, a build artifact, and must never mediate
    a semantic decision."""

    return frozenset(spec.name for spec in TARGET_PACK_CLI_COMMANDS_V2)


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


def test_operative_spec_does_not_defer_a_runtime_authority_command() -> None:
    """PR #244 exposed `validate` while the operative specification still
    listed it as a deferred, unwritten subcommand. Because that document
    declares itself the authority maintainers cite for deferred-subcommand
    classification, the two states must not disagree.

    Proves `C intersect S_D = empty`. Composed with the separately proved
    `C = P`, this establishes `P intersect S_D = empty` without this test
    ever re-deriving `P`."""

    commands = _runtime_authority_cli_commands_v2()
    assert "validate" in commands, (
        "the runtime command authority no longer includes validate; this test's premise changed"
    )

    deferred_bullet = _spec_deferred_subcommand_bullet_v2()
    contradictions = sorted(name for name in commands if f"`{name}`" in deferred_bullet)
    assert not contradictions, (
        f"the operative spec's deferral bullet still classifies {contradictions} as deferred "
        f"while the runtime command authority declares them: {sorted(commands)}"
    )


def test_operative_spec_still_defers_the_genuinely_unshipped_subcommands() -> None:
    """The reconciliation must not have over-corrected into claiming the
    whole `#203` surface ships.

    These four names pin a lifecycle decision of the CURRENT operative spec;
    they do not attempt to define the runtime CLI. Restructuring that
    documentary authority belongs to the later documentation successor, not
    to this runtime predecessor."""

    commands = _runtime_authority_cli_commands_v2()
    deferred_bullet = _spec_deferred_subcommand_bullet_v2()
    for name in ("conformance", "install-workflows", "upgrade", "rollback"):
        assert f"`{name}`" in deferred_bullet, f"{name} is not shipped but the spec stopped deferring it"
        assert name not in commands, f"{name} is declared by the runtime command authority but still listed as deferred"


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
