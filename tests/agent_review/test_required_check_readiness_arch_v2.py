"""AST/call-graph proof that `#201-C`'s single production constructor path
holds structurally, not just by convention (plan rev.2.1, §6.3/§6.5).

This file proves what code review and docstrings cannot: that no
production function anywhere in ``app/``/``scripts/`` can construct a
``ReviewReadinessV2`` outside ``produce_review_readiness_v2``'s own chain,
that no production function accepts a
``RequiredCheckReadinessAssessmentV2`` or a caller-supplied required-check
name list, that the ``#201-C0`` boundary is never wrapped in an
``except``, and that no TEST fixture creates a production-reachable
positive-authority path (a real, if narrower, guarantee than "nobody has
done this yet" -- a regression here fails loudly, by name, instead of
silently).

These asserts are deliberately mechanical rather than semantic: they scan
syntax, not behavior. That is the point -- a refactor that violates one of
these should fail here even if every other test still passes, because
these are exactly the properties ordinary tests cannot see (the ABSENCE of
a second call site, the ABSENCE of a parameter, the ABSENCE of a
monkeypatch). When one of these fails, the fix is almost always to the
production code, not to this file -- widening an assert here is itself a
architecture decision and should be treated with the same suspicion as
loosening the C0 boundary itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app" / "agent_review"
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent

# v1 is explicitly out of #201-C's scope and shares no symbols with v2's
# readiness authority -- scanning it would only add noise.
PRODUCTION_FILES = sorted(
    [p for p in APP_DIR.glob("*.py")]
    + [p for p in SCRIPTS_DIR.glob("*.py") if p.name != "aiops-review-quality-gate.py"]
)

FORBIDDEN_ASSESSMENT_ANNOTATIONS = {"RequiredCheckReadinessAssessmentV2"}
FORBIDDEN_COMPLETENESS_PARAM_NAMES = {"required_check_names", "loaded_policy"}
BOUNDARY_FUNCTION_NAMES = {
    "reassemble_and_verify_required_checks_v2",
    "verify_independent_semantic_judge_v2",
    "_verify_and_assess_required_checks_v2",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _iter_functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _call_name(node: ast.expr) -> str | None:
    """The bare name of a `Call.func`, whether `Name` or `Attribute` --
    `foo()` and `module.foo()` both resolve to `"foo"`."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_calls(tree: ast.Module, target_name: str) -> list[tuple[ast.Call, ast.FunctionDef | None]]:
    """Every call to `target_name` in `tree`, paired with its enclosing
    function (None if at module level)."""

    results: list[tuple[ast.Call, ast.FunctionDef | None]] = []
    for func in [None, *list(_iter_functions(tree))]:
        scope: ast.AST = func if func is not None else tree
        for node in ast.walk(scope):
            if func is not None and node is func:
                continue
            if isinstance(node, ast.Call) and _call_name(node.func) == target_name:
                # Only count this call in the SMALLEST enclosing scope --
                # avoid double-counting a call inside an inner function when
                # walking its outer function too.
                if func is None or not any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not func
                    for n in ast.walk(func)
                    if _contains(n, node)
                ):
                    results.append((node, func))
    # De-duplicate: the module-level walk (func=None) also visits nodes
    # inside every function. Keep only the entry whose enclosing scope is
    # the innermost one containing the call.
    deduped: dict[int, tuple[ast.Call, ast.FunctionDef | None]] = {}
    for call, func in results:
        key = id(call)
        current = deduped.get(key)
        if current is None or (current[1] is None and func is not None):
            deduped[key] = (call, func)
    return list(deduped.values())


def _contains(container: ast.AST, target: ast.AST) -> bool:
    return any(n is target for n in ast.walk(container))


def _production_calls(target_name: str) -> list[tuple[Path, ast.Call, str]]:
    """`(file, call_node, enclosing_function_name_or_module)` for every
    call to `target_name` across all production files."""

    found: list[tuple[Path, ast.Call, str]] = []
    for path in PRODUCTION_FILES:
        tree = _parse(path)
        for call, func in _find_calls(tree, target_name):
            found.append((path, call, func.name if func is not None else "<module>"))
    return found


# -- assert 1: single ReviewReadinessV2( construction site ------------------


def test_single_construction_site_for_review_readiness_v2() -> None:
    calls = _production_calls("ReviewReadinessV2")
    # Constructing calls only -- the frozen contract's own class body
    # (`class ReviewReadinessV2(ContractV2Model):` in contracts_v2.py) is a
    # ClassDef, not a Call, and never matches this search.
    locations = [(str(p.relative_to(p.parents[2])), fn) for p, _, fn in calls]
    assert len(calls) == 1, f"expected exactly one ReviewReadinessV2(...) call, found {locations}"
    path, _, func_name = calls[0]
    assert path.name == "review_readiness_emission_v2.py"
    assert func_name == "_assemble_review_readiness_v2"


# -- assert 2: single caller of the assembler --------------------------------


def test_single_caller_of_the_assembler() -> None:
    calls = _production_calls("_assemble_review_readiness_v2")
    locations = [(str(p.relative_to(p.parents[2])), fn) for p, _, fn in calls]
    assert len(calls) == 1, f"expected exactly one _assemble_review_readiness_v2(...) call, found {locations}"
    path, _, func_name = calls[0]
    assert path.name == "review_readiness_emission_v2.py"
    assert func_name == "produce_review_readiness_v2"


# -- corollary: single caller of the pure assessment helper ------------------


def test_single_caller_of_the_pure_assessment_helper() -> None:
    """`_assess_required_checks_v2` is pure and total, but it is NOT a
    second entry point: in production it is called from exactly one place,
    which always derives `required_check_names` from the trusted profile
    first. Tests are free to call it directly (Class B, composition) --
    that is not a production call and is not what this asserts."""

    calls = _production_calls("_assess_required_checks_v2")
    locations = [(str(p.relative_to(p.parents[2])), fn) for p, _, fn in calls]
    assert len(calls) == 1, f"expected exactly one _assess_required_checks_v2(...) call, found {locations}"
    path, _, func_name = calls[0]
    assert path.name == "required_check_readiness_v2.py"
    assert func_name == "_verify_and_assess_required_checks_v2"


# -- assert 3: the path always traverses the C0 boundary ---------------------


def test_the_path_always_traverses_the_c0_boundary() -> None:
    produce_calls = _production_calls("produce_review_readiness_v2")
    verify_and_assess_calls = _production_calls("_verify_and_assess_required_checks_v2")
    reassemble_calls = _production_calls("reassemble_and_verify_required_checks_v2")

    # produce_review_readiness_v2 itself calls _verify_and_assess_required_checks_v2 ...
    assert any(fn == "produce_review_readiness_v2" for _, _, fn in verify_and_assess_calls)
    # ... which itself calls the real C0 verifier.
    assert any(fn == "_verify_and_assess_required_checks_v2" for _, _, fn in reassemble_calls)
    # And produce_review_readiness_v2 is a real, defined, reachable function.
    definitions = [
        node
        for path in PRODUCTION_FILES
        for node in _iter_functions(_parse(path))
        if node.name == "produce_review_readiness_v2"
    ]
    assert len(definitions) == 1


# -- assert 4: no production function accepts an assessment as authority ----


def test_no_public_production_function_accepts_an_assessment() -> None:
    """Scoped to PUBLIC names (no leading underscore) deliberately:
    `readiness_decision_v2._apply_required_check_assessment_v2` and its own
    `_required_check_detail_v2` helper legitimately consume an assessment
    -- they sit strictly downstream of `_verify_and_assess_required_checks_
    v2` in the one verified chain (see `test_the_path_always_traverses_the_
    c0_boundary`), never reachable with an externally-supplied assessment.
    What must never exist is a PUBLIC function -- `produce_review_
    readiness_v2`, `run_synthetic_review_v2`, or anything else an external
    caller could import and call directly -- that treats a caller-supplied
    assessment as proof of authority."""

    offenders: list[str] = []
    for path in PRODUCTION_FILES:
        tree = _parse(path)
        for func in _iter_functions(tree):
            if func.name.startswith("_"):
                continue
            all_args = [*func.args.args, *func.args.kwonlyargs, *func.args.posonlyargs]
            for arg in all_args:
                if arg.annotation is None:
                    continue
                annotation_name = _call_name(arg.annotation) or (
                    arg.annotation.id if isinstance(arg.annotation, ast.Name) else None
                )
                if annotation_name in FORBIDDEN_ASSESSMENT_ANNOTATIONS:
                    offenders.append(f"{path.name}:{func.name}({arg.arg})")
    assert not offenders, f"public production function(s) accept a RequiredCheckReadinessAssessmentV2 directly: {offenders}"


# -- assert 5: no handler converts a C0 refusal into an artifact ------------


def test_no_except_handler_swallows_the_c0_refusal() -> None:
    """`RequiredCheckProvenanceErrorV2` must propagate uncaught from
    `_verify_and_assess_required_checks_v2` through `produce_review_
    readiness_v2` to the CLI's own top-level dispatch (which prints the
    reason code and exits nonzero, still writing no artifact -- see
    `main()` in the quality-gate CLI). This checks the two library-level
    functions specifically: neither may contain a `try/except` around the
    boundary call."""

    for filename, func_name in [
        ("required_check_readiness_v2.py", "_verify_and_assess_required_checks_v2"),
        ("review_readiness_emission_v2.py", "produce_review_readiness_v2"),
    ]:
        path = APP_DIR / filename
        tree = _parse(path)
        func = next(node for node in _iter_functions(tree) if node.name == func_name)
        for node in ast.walk(func):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    caught = _exception_names(handler.type)
                    assert "RequiredCheckProvenanceErrorV2" not in caught, (
                        f"{filename}:{func_name} catches RequiredCheckProvenanceErrorV2 -- "
                        "a forged/invalid submission must propagate uncaught, never become an artifact"
                    )


def _exception_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for elt in node.elts:
            names |= _exception_names(elt)
        return names
    name = _call_name(node) or (node.id if isinstance(node, ast.Name) else None)
    return {name} if name else set()


# -- assert 6: completeness never comes from the caller ----------------------


def test_completeness_is_never_a_caller_supplied_parameter() -> None:
    """No PUBLIC production entry point (`produce_review_readiness_v2`,
    `run_synthetic_review_v2`, or the quality-gate CLI's own `main`) may
    accept `required_check_names`/`required_checks`(as a parameter)/
    `loaded_policy` -- the required set is derived exclusively inside
    `_verify_and_assess_required_checks_v2`, from a trusted
    `target_profile_root`.

    `_assess_required_checks_v2` legitimately HAS a `required_check_names`
    parameter -- it is a pure, internal, single-caller helper (see
    `test_single_caller_of_the_pure_assessment_helper`), not a public entry
    point, and its one caller always derives that value itself. This test
    does not, and should not, flag it.

    `policies: TargetPoliciesV2` is deliberately NOT forbidden here: it is
    a real, legitimate, preexisting parameter of `compute_readiness_
    decision_v2`/`run_synthetic_review_v2` governing CONTENT decisions
    (e.g. `coverage_failure_state`) entirely unrelated to required-check
    completeness, predating `#201-C`. Required-check completeness is a
    narrower claim than "some TargetPoliciesV2 was involved somewhere" --
    it means specifically "which names does the caller get to assert are
    required", which only `required_check_names`/`loaded_policy` can
    smuggle."""

    entry_points = {"produce_review_readiness_v2", "run_synthetic_review_v2", "main"}
    offenders: list[str] = []
    for path in PRODUCTION_FILES:
        tree = _parse(path)
        for func in _iter_functions(tree):
            if func.name not in entry_points:
                continue
            all_args = [*func.args.args, *func.args.kwonlyargs, *func.args.posonlyargs]
            for arg in all_args:
                if arg.arg in FORBIDDEN_COMPLETENESS_PARAM_NAMES:
                    offenders.append(f"{path.name}:{func.name}({arg.arg})")
    assert not offenders, f"a public entry point accepts caller-supplied completeness input: {offenders}"


# -- assert 7 (§6.5): no fixture creates a production-reachable positive
# authority path -- temporary_until_203, removed (not relaxed) once a
# legitimately promotable source exists (plan rev.2.1 §12, class C).
# -----------------------------------------------------------------------


PRODUCTION_ENTRY_CALL_NAMES = {"produce_review_readiness_v2", "run_synthetic_review_v2"}
_READY_LIKE_VALUES = {"ready", "blocked_pipeline"}


def _test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _root_name(node: ast.expr) -> str | None:
    """Walk an attribute/subscript chain (`outcome.readiness.state`,
    `readiness["state"]`) down to its root `Name`."""

    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _result_names_from(func: ast.FunctionDef) -> set[str]:
    """Every variable name directly assigned the return value of a
    production entry-point call within `func` -- e.g. `outcome` in
    `outcome = run_synthetic_review_v2(...)`, or `readiness` in
    `readiness = produce_review_readiness_v2(...)`."""

    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _call_name(node.value.func) in PRODUCTION_ENTRY_CALL_NAMES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _compares_state_to_ready_like(node: ast.AST, result_names: set[str]) -> str | None:
    """If `node` is a `Compare` asserting `<result>.state` (or
    `<result>.readiness.state`, for the `SyntheticReviewOutcomeV2` wrapper)
    equals a ready-like value, where `<result>` is one of `result_names`,
    return that value; else None. Scoped to the entry point's OWN return
    value specifically -- an intermediate `decision.state == READY` used to
    set up a fixture's PRECONDITION (the content decision the required-check
    gate is about to narrow) is not a claim about what the entry point
    itself produced, and must not be flagged."""

    if not isinstance(node, ast.Compare):
        return None
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.Is)):
        return None
    left, right = node.left, node.comparators[0]

    def _is_result_state_access(n: ast.expr) -> bool:
        is_state = (isinstance(n, ast.Attribute) and n.attr == "state") or (
            isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "state"
        )
        return is_state and _root_name(n) in result_names

    def _ready_like_value(n: ast.expr) -> str | None:
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _READY_LIKE_VALUES:
            return n.value
        if isinstance(n, ast.Attribute) and n.attr.lower() in {"ready", "blocked_pipeline"}:
            return n.attr.lower()
        return None

    if _is_result_state_access(left):
        return _ready_like_value(right)
    if _is_result_state_access(right):
        return _ready_like_value(left)
    return None


def test_no_fixture_creates_a_production_reachable_positive_authority_path() -> None:
    for path in _test_files():
        tree = _parse(path)
        source = path.read_text(encoding="utf-8")
        for func in _iter_functions(tree):
            call_names = {_call_name(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)}
            if not (call_names & PRODUCTION_ENTRY_CALL_NAMES):
                continue

            # No monkeypatch/mock.patch of the boundary in a function that
            # also calls a production entry point.
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    called = _call_name(node.func)
                    if called in {"setattr", "patch"} or (
                        isinstance(node.func, ast.Attribute) and node.func.attr in {"setattr", "patch", "object"}
                    ):
                        segment = ast.get_source_segment(source, node) or ""
                        for boundary_name in BOUNDARY_FUNCTION_NAMES:
                            assert boundary_name not in segment, (
                                f"{path.name}:{func.name} patches {boundary_name} in a function that also "
                                "calls a production readiness entry point -- this is exactly the test-only "
                                "authority bypass #201-C's stop conditions forbid"
                            )

            # No assertion that the ENTRY POINT'S OWN RESULT has
            # state == ready/blocked_pipeline.
            result_names = _result_names_from(func)
            if not result_names:
                continue
            for node in ast.walk(func):
                value = _compares_state_to_ready_like(node, result_names)
                if value is not None:
                    raise AssertionError(
                        f"{path.name}:{func.name} calls a production readiness entry point AND asserts "
                        f"its result's state == {value!r} -- no fixture may claim READY/BLOCKED_PIPELINE is "
                        "reachable through the real C0 boundary today (plan rev.2.1 R7); see class B/C in "
                        "the plan for how to test positive states legitimately"
                    )
