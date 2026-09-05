"""AST/call-graph proof that `#201-C`'s single production constructor path
holds structurally, not just by convention (plan rev.2.1, §6.3/§6.5).

This file proves what code review and docstrings cannot: that no
production function in the modules it scans can construct a
``ReviewReadinessV2`` outside ``produce_review_readiness_v2``'s own chain,
that no production function accepts a
``RequiredCheckReadinessAssessmentV2`` or a caller-supplied required-check
name list, that the ``#201-C0`` boundary is never wrapped in an ``except``,
and that no test fixture calling a production readiness entry point also
monkeypatches an authority-boundary function in the same function body.

The last of those replaced a broader invariant -- "no test fixture creates a
production-reachable positive-authority path" -- which `#331` SGAQ-CI1R makes
false on purpose. See the retirement note further down. The replacement is
narrower than the sentence it replaces, and narrower than its own name might
suggest; its exact reach is stated where it is enforced.

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
import textwrap
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app" / "agent_review"
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent

# v1 is explicitly out of #201-C's scope and shares no symbols with v2's
# readiness authority -- scanning it would only add noise.
# NOTE ON SCAN SCOPE, because the docstring above used to say "anywhere in
# `app/`/`scripts/`" and this glob is NOT recursive: it sees
# `app/agent_review/*.py` and the scripts listed below, and no nested package.
# Every module that can construct a `ReviewReadinessV2` lives at that level
# today, so the proof holds -- but it holds by repository layout, not by
# construction, and a future `app/agent_review/<subpackage>/` would be
# invisible here.
PRODUCTION_FILES = sorted(
    [p for p in APP_DIR.glob("*.py")]
    + [p for p in SCRIPTS_DIR.glob("*.py") if p.name != "aiops-review-quality-gate.py"]
)

FORBIDDEN_ASSESSMENT_ANNOTATIONS = {"RequiredCheckReadinessAssessmentV2"}
# Adversarial review finding, confirmed and fixed (round 6): this set
# omitted "required_checks", even though `test_completeness_is_never_a_
# caller_supplied_parameter`'s own docstring explicitly claims that name is
# covered ("required_check_names/required_checks(as a parameter)/
# loaded_policy"). A future `required_checks: list[str]` parameter on a
# public entry point -- smuggling a caller-supplied "which checks are
# required" list, the exact #145/#201-C attack class -- would have passed
# silently.
FORBIDDEN_COMPLETENESS_PARAM_NAMES = {"required_check_names", "required_checks", "loaded_policy"}
BOUNDARY_FUNCTION_NAMES = {
    "reassemble_and_verify_required_checks_v2",
    "verify_independent_semantic_judge_v2",
    # Adversarial review finding, confirmed and fixed (`#331` SGAQ-CI1R): this
    # set is the guard on authority gates, and adding a new authority gate
    # without adding it here leaves the new gate monkeypatchable in exactly the
    # way `#201-C`'s stop conditions forbid. Reproduced: a probe patching
    # `verify_execution_mode_is_policy_authorized_v2` while calling a
    # production readiness entry point PASSED, while the byte-identical probe
    # naming `verify_independent_semantic_judge_v2` failed as intended.
    #
    # This is the SECOND time this set was found under-drawn -- see the
    # round-6 note on FORBIDDEN_COMPLETENESS_PARAM_NAMES above. The set does
    # not grow by itself; a slice that adds a gate must add it here.
    "verify_execution_mode_is_policy_authorized_v2",
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


def _annotation_names(node: ast.expr) -> set[str]:
    """Every bare name reachable inside a type annotation -- robust to
    `X | None` (`ast.BinOp`), `Optional[X]`/`list[X]` (`ast.Subscript`),
    and string forward-references (`"X"`), not just a bare `X`/`module.X`.

    Adversarial review finding, confirmed and fixed (round 6): the original
    single-name resolution (`_call_name(node) or (node.id if ast.Name else
    None)`) silently returned `None` -- never flagging the offender -- for
    any of those three spellings, even though this same file's own type
    hints use `X | None` idiomatically, making the gap realistic rather
    than academic. `ast.walk` already recurses into `BinOp`/`Subscript`
    children, so collecting every `Name`/`Attribute` it finds handles the
    first two automatically; string constants are additionally re-parsed
    as an expression and recursed into, to catch forward-referenced
    annotations too.
    """

    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                parsed = ast.parse(sub.value, mode="eval").body
            except SyntaxError:
                continue
            names |= _annotation_names(parsed)
    return names


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
    """Single CALLER FUNCTION, not single call expression: `produce_review_
    readiness_v2` legitimately calls `_assemble_review_readiness_v2` from
    two branches (the STALE short-circuit and the normal path) -- both
    still inside the one function that is the assembler's only caller."""

    calls = _production_calls("_assemble_review_readiness_v2")
    locations = [(str(p.relative_to(p.parents[2])), fn) for p, _, fn in calls]
    caller_functions = {(str(p.relative_to(p.parents[2])), fn) for p, _, fn in calls}
    assert caller_functions == {("app/agent_review/review_readiness_emission_v2.py", "produce_review_readiness_v2")}, (
        f"expected _assemble_review_readiness_v2(...) called only from produce_review_readiness_v2, found {locations}"
    )


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
                if FORBIDDEN_ASSESSMENT_ANNOTATIONS & _annotation_names(arg.annotation):
                    offenders.append(f"{path.name}:{func.name}({arg.arg})")
    assert not offenders, f"public production function(s) accept a RequiredCheckReadinessAssessmentV2 directly: {offenders}"


def test_annotation_names_resolves_union_optional_and_forward_ref_spellings() -> None:
    """Direct unit test of `_annotation_names`, proving the round-6 fix
    without depending on any production file happening to use one of these
    spellings today: `X` (bare), `X | None`, `Optional[X]`, and a string
    forward-reference must all resolve to a set containing `X`."""

    bare = ast.parse("x: RequiredCheckReadinessAssessmentV2", mode="exec").body[0].annotation
    union = ast.parse("x: RequiredCheckReadinessAssessmentV2 | None", mode="exec").body[0].annotation
    optional = ast.parse("x: Optional[RequiredCheckReadinessAssessmentV2]", mode="exec").body[0].annotation
    forward_ref = ast.parse('x: "RequiredCheckReadinessAssessmentV2"', mode="exec").body[0].annotation
    qualified = ast.parse("x: mod.RequiredCheckReadinessAssessmentV2", mode="exec").body[0].annotation

    for node in (bare, union, optional, forward_ref, qualified):
        assert "RequiredCheckReadinessAssessmentV2" in _annotation_names(node), ast.dump(node)

    unrelated = ast.parse("x: int | None", mode="exec").body[0].annotation
    assert "RequiredCheckReadinessAssessmentV2" not in _annotation_names(unrelated)


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


def test_forbidden_completeness_param_names_matches_its_own_docstring_claim() -> None:
    """Adversarial review finding, confirmed and fixed (round 6):
    `test_completeness_is_never_a_caller_supplied_parameter`'s own
    docstring names three forbidden spellings -- `required_check_names`,
    `required_checks`, `loaded_policy` -- but `FORBIDDEN_COMPLETENESS_
    PARAM_NAMES` omitted `required_checks`, silently narrower than what
    the docstring claimed to cover."""

    assert FORBIDDEN_COMPLETENESS_PARAM_NAMES == {"required_check_names", "required_checks", "loaded_policy"}


def test_the_protected_boundary_set_is_pinned() -> None:
    """`BOUNDARY_FUNCTION_NAMES` is what the boundary-patch guard protects, and
    nothing else in this file constrains its contents.

    A mutation dropping a member was reproduced and SURVIVED the whole file:
    `test_the_boundary_patch_detector_rejects_a_synthetic_bypass` iterates over
    the set, so removing a name makes it test fewer names rather than fail. A
    set cannot witness its own completeness, which is the same defect that made
    `FORBIDDEN_COMPLETENESS_PARAM_NAMES` above silently narrower than its own
    docstring, and the same one that left this very set missing
    `verify_execution_mode_is_policy_authorized_v2` when `#331` added it.

    Pinned to a literal so removing OR adding a gate is a deliberate edit here,
    visible in review, rather than a silent change in what is protected."""

    assert BOUNDARY_FUNCTION_NAMES == {
        "reassemble_and_verify_required_checks_v2",
        "verify_independent_semantic_judge_v2",
        "verify_execution_mode_is_policy_authorized_v2",
        "_verify_and_assess_required_checks_v2",
    }
# -- assert 7 (§6.5), RETIRED AND REPLACED by `#331` SGAQ-CI1R.
#
# The original invariant was "no fixture creates a production-reachable
# positive authority path", carried as temporary_until_203. CI1R makes that
# proposition FALSE on purpose: an `independent_data_only_host_tool` producer
# under a base-owned policy that authorizes the mode is a legitimate positive
# path, and `test_aiops_review_quality_gate_v2_cli.py` exercises it through the
# real gate subprocess.
#
# Codex found that the old guard did not merely become obsolete -- it became
# FALSE STRUCTURAL EVIDENCE. It recognised only direct AST calls to the two
# in-process entry points, so the CLI-subprocess fixture that reaches
# `state: ready` was skipped entirely and the guard kept passing while its
# stated invariant no longer held.
#
# Teaching the detector about `_run()` was deliberately NOT done: that would
# re-enforce a revoked proposition against a wider surface. The invariant is
# retired instead.
#
# What survives is the half that is still true and still load-bearing: a test
# exercising a production readiness path must not GAIN AUTHORITY by
# monkeypatching or replacing an authority-boundary function. That property is
# independent of whether positive states are reachable, and it is the one
# `#201-C`'s stop conditions actually turn on.
#
# Positive READY is now legitimate when reached through the real boundary with
# the independent execution mode AND an explicit trusted-base policy opt-in.
# The behavioural truth table for that lives in the CLI suite and is NOT
# duplicated here as another brittle AST approximation.
# -----------------------------------------------------------------------


PRODUCTION_ENTRY_CALL_NAMES = {"produce_review_readiness_v2", "run_synthetic_review_v2"}

def _test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _root_name(node: ast.expr) -> str | None:
    """Walk an attribute/subscript chain (`outcome.readiness.state`,
    `readiness["state"]`) down to its root `Name`."""

    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None

def _patches_authority_boundary(func: ast.AST, source: str) -> str | None:
    """The boundary-name a `func` monkeypatches, if any.

    Recognises `setattr(...)`, `monkeypatch.setattr(...)`, `patch(...)`,
    `mock.patch.object(...)` and friends, then looks for any
    `BOUNDARY_FUNCTION_NAMES` member inside the call's own source segment."""

    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        called = _call_name(node.func)
        if called in {"setattr", "patch"} or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"setattr", "patch", "object"}
        ):
            segment = ast.get_source_segment(source, node) or ""
            for boundary_name in BOUNDARY_FUNCTION_NAMES:
                if boundary_name in segment:
                    return boundary_name
    return None


def test_no_production_readiness_fixture_patches_an_authority_boundary() -> None:
    """The surviving half of the retired assert-7.

    A fixture may legitimately reach a positive readiness state now. It may
    not, IN THE SAME FUNCTION BODY, both call a production readiness entry
    point and `setattr`/`patch` an authority-boundary function.

    THE REACH OF THIS GUARD, STATED EXACTLY, BECAUSE IT IS NARROWER THAN THE
    PROPERTY IT SERVES. An independent claim audit reproduced three bypasses
    this mechanism does NOT flag:

    - the patch applied in a `@pytest.fixture`, the entry point called in the
      test that consumes it;
    - the patch applied by a module-level helper the test calls;
    - plain attribute assignment (`mod.verify_x = lambda ...`), with no
      `setattr`/`patch` call at all.

    So this is a syntactic tripwire for the obvious form, not a proof that no
    fixture anywhere gains authority by replacement. It is kept because the
    obvious form is the one written by accident, and because a tripwire that
    names its own limits is worth more than a deleted one. It is not evidence
    of exhaustiveness and must not be cited as such."""

    for path in _test_files():
        tree = _parse(path)
        source = path.read_text(encoding="utf-8")
        for func in _iter_functions(tree):
            call_names = {_call_name(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)}
            if not (call_names & PRODUCTION_ENTRY_CALL_NAMES):
                continue
            patched = _patches_authority_boundary(func, source)
            assert patched is None, (
                f"{path.name}:{func.name} patches {patched} in a function that also "
                "calls a production readiness entry point -- this is exactly the test-only "
                "authority bypass #201-C's stop conditions forbid"
            )


def test_the_boundary_patch_detector_rejects_a_synthetic_bypass() -> None:
    """The required falsifier, and the reason this guard is not vacuous.

    The enforcement loop above passes trivially if no test file happens to
    contain a bypass -- which is the desired steady state, and also exactly
    what a broken detector looks like. This feeds the detector a fixture that
    DOES both things and requires it to be caught, so the DETECTOR is proven
    to recognise the canonical `monkeypatch.setattr` form rather than being
    vacuously green. It does not prove the guard's SCOPE -- see the reach note
    on the enforcement test above.

    Each member of `BOUNDARY_FUNCTION_NAMES` is exercised against that form.
    Note what this cannot do: the synthetic source is interpolated from the
    same set the detector reads, so a name is found by construction and this
    loop can never fail because a name is missing from the set. The risk it
    does NOT cover -- a new authority gate added in `app/` and never added to
    the set -- is covered by nothing here; `test_the_protected_boundary_set_
    is_pinned` only makes changing the set a deliberate, reviewable edit."""

    for boundary in sorted(BOUNDARY_FUNCTION_NAMES):
        bypass = textwrap.dedent(
            f"""
            def test_synthetic_bypass(monkeypatch):
                monkeypatch.setattr(module, "{boundary}", lambda **kw: None)
                return produce_review_readiness_v2(payload)
            """
        )
        func = ast.parse(bypass).body[0]
        assert _patches_authority_boundary(func, bypass) == boundary, boundary

    honest = textwrap.dedent(
        """
        def test_honest_positive():
            result = produce_review_readiness_v2(payload)
            assert result.state == "ready"
        """
    )
    func = ast.parse(honest).body[0]
    assert _patches_authority_boundary(func, honest) is None, (
        "asserting a positive state through the real boundary is legitimate under "
        "`#331` SGAQ-CI1R and must not be flagged"
    )
