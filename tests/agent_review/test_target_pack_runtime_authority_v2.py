"""`#203` PR A -- tests for the Target Pack INTERNAL runtime authorities.

The claim under test is directional, and subset containment alone does not
establish it:

```
Runtime       = Consumer(Authority)      not   Authority describes Runtime
Documentation = Projection(Authority)    not   Documentation = Infer(Source)
```

So the suite proves both directions. For the CLI, `C = K = P` closes the
surface in both senses plus a bypass guard. For validate, `R(x) subset of D`
and `U subset of R(x)` bound every run, while a POSITIVE WITNESS -- a healthy
installation for which `O(h) = L` exactly -- is what rules out a declared spec
the runtime can never emit. `emitted subset of registry` would be satisfied by
such a dead spec, which is precisely why it is not sufficient on its own.

Fixtures come from the validate suite rather than being duplicated here: a
second copy of the profile/receipt corpus would be a second fixture authority,
the defect class this PR exists to remove.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

from app.agent_review.target_pack_runtime_authority_v2 import (  # noqa: E402
    TARGET_PACK_CLI_COMMANDS_V2,
    TARGET_PACK_VALIDATE_CHECKS_V2,
    GENERATED_FILE_INTEGRITY_CHECK_V2,
    TARGET_OWNED_INTEGRITY_CHECK_V2,
    TargetPackCliCommandSpecV2,
    TargetPackRuntimeAuthorityError,
    TargetPackValidateCheckSpecV2,
    ValidateEvaluationClassV2,
    _verify_cli_authority_v2,
    _verify_validate_authority_v2,
    cli_command_names_v2,
    validate_check_domain_names_v2,
    validate_check_index_v2,
    validate_locally_evaluable_names_v2,
    validate_unvalidated_specs_v2,
)
from app.agent_review.target_pack_validate_v2 import (  # noqa: E402
    STATUS_PASS_V2,
    STATUS_UNAVAILABLE_V2,
    UNVALIDATED_CAPABILITIES_V2,
    VALIDATE_CHECK_ORDER_V2,
    ValidateCheckV2,
    ValidateReportConstructionErrorV2,
    _finalize_validate_checks_v2,
    run_validate_v2,
)
from tests.agent_review.test_target_pack_validate_v2 import _install, _receipt  # noqa: E402

CLI_SCRIPT = REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py"
VIEW_PATH = REPO_ROOT / "docs" / "generated" / "target-pack-runtime-authority.v1.json"
VIEW_GENERATOR = REPO_ROOT / "scripts" / "generate-target-pack-runtime-authority-view.py"


def _load_cli_module():
    module_name = "agent_review_target_pack_v2_cli_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, CLI_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclass/annotation resolution needs this first
    spec.loader.exec_module(module)
    return module


# --- Authority well-formedness (raises, never asserts) -------------------


def test_invariants_raise_rather_than_assert() -> None:
    """`python -O` strips `assert`, so a load-bearing invariant expressed as
    one would silently vanish in an optimized run."""

    source = (REPO_ROOT / "app" / "agent_review" / "target_pack_runtime_authority_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)], (
        "the authority module must not express invariants with `assert`"
    )


def test_declared_authorities_are_well_formed() -> None:
    _verify_cli_authority_v2()
    _verify_validate_authority_v2()


def test_duplicate_cli_command_identity_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_runtime_authority_v2 as authority

    monkeypatch.setattr(
        authority, "TARGET_PACK_CLI_COMMANDS_V2",
        (TargetPackCliCommandSpecV2("init", "a"), TargetPackCliCommandSpecV2("init", "b")),
    )
    with pytest.raises(TargetPackRuntimeAuthorityError, match="duplicate CLI command identity"):
        authority._verify_cli_authority_v2()


def test_duplicate_check_identity_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_runtime_authority_v2 as authority

    dup = TargetPackValidateCheckSpecV2("x", ValidateEvaluationClassV2.LOCALLY_EVALUABLE)
    monkeypatch.setattr(authority, "TARGET_PACK_VALIDATE_CHECKS_V2", (dup, dup))
    with pytest.raises(TargetPackRuntimeAuthorityError, match="duplicate check identity"):
        authority._verify_validate_authority_v2()


def test_unvalidated_spec_without_reason_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_runtime_authority_v2 as authority

    monkeypatch.setattr(
        authority, "TARGET_PACK_VALIDATE_CHECKS_V2",
        (TargetPackValidateCheckSpecV2("x", ValidateEvaluationClassV2.UNVALIDATED, None),),
    )
    with pytest.raises(TargetPackRuntimeAuthorityError, match="declares no unvalidated_reason_code"):
        authority._verify_validate_authority_v2()


def test_locally_evaluable_spec_with_reason_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_runtime_authority_v2 as authority

    monkeypatch.setattr(
        authority, "TARGET_PACK_VALIDATE_CHECKS_V2",
        (TargetPackValidateCheckSpecV2("x", ValidateEvaluationClassV2.LOCALLY_EVALUABLE, "why"),),
    )
    with pytest.raises(TargetPackRuntimeAuthorityError, match="declares an unvalidated_reason_code"):
        authority._verify_validate_authority_v2()


def test_domain_is_the_disjoint_union_of_its_two_classes() -> None:
    """`D = L (+) U`."""

    domain = set(validate_check_domain_names_v2())
    local = set(validate_locally_evaluable_names_v2())
    unvalidated = {spec.name for spec in validate_unvalidated_specs_v2()}
    assert local & unvalidated == set()
    assert local | unvalidated == domain
    assert len(domain) == len(TARGET_PACK_VALIDATE_CHECKS_V2)


# --- CLI: C = K = P, plus a bypass guard ---------------------------------


def test_cli_surface_equals_authority_and_configurators() -> None:
    """`C = K = P`, with `P` read back from a really-constructed parser."""

    module = _load_cli_module()
    C = set(cli_command_names_v2())
    K = set(module._CONFIGURATORS_V2)
    namespace_parser = module._parse_args(["validate", "--target-root", "/nonexistent"])
    assert namespace_parser.command == "validate"

    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    module._build_subcommands_v2(sub)
    P = set(sub.choices)

    assert C == K == P == {"init", "doctor", "validate"}


def test_authority_command_without_configurator_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    import app.agent_review.target_pack_runtime_authority_v2 as authority

    module = _load_cli_module()
    monkeypatch.setattr(
        authority, "TARGET_PACK_CLI_COMMANDS_V2",
        (*TARGET_PACK_CLI_COMMANDS_V2, TargetPackCliCommandSpecV2("conformance", "not wired")),
    )
    monkeypatch.setattr(module, "TARGET_PACK_CLI_COMMANDS_V2", authority.TARGET_PACK_CLI_COMMANDS_V2)
    monkeypatch.setattr(module, "cli_command_names_v2", authority.cli_command_names_v2)

    sub = argparse.ArgumentParser().add_subparsers(dest="command", required=True)
    with pytest.raises(module.TargetPackCliSurfaceErrorV2, match="authority-only=\\['conformance'\\]"):
        module._build_subcommands_v2(sub)


def test_configurator_without_authority_command_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    module = _load_cli_module()
    monkeypatch.setitem(module._CONFIGURATORS_V2, "rogue", lambda parser: None)

    sub = argparse.ArgumentParser().add_subparsers(dest="command", required=True)
    with pytest.raises(module.TargetPackCliSurfaceErrorV2, match="configurator-only=\\['rogue'\\]"):
        module._build_subcommands_v2(sub)


def test_no_subcommand_construction_bypasses_the_authorized_builder() -> None:
    """Iterating the authority is necessary but not sufficient: nothing stops a
    later `add_parser()` or a second `add_subparsers()` elsewhere in the file.
    AST is legitimate here because it forbids bypass of a choke point that
    exists -- it is not being used to infer semantic truth from source."""

    tree = ast.parse(CLI_SCRIPT.read_text(encoding="utf-8"))
    sites: dict[str, list[str]] = {"add_parser": [], "add_subparsers": []}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                if inner.func.attr in sites:
                    sites[inner.func.attr].append(node.name)

    assert sites["add_subparsers"] == ["_parse_args"], sites["add_subparsers"]
    assert sites["add_parser"] == ["_build_subcommands_v2"], sites["add_parser"]


# --- validate: the finalizer is the only report boundary -----------------


def _local_check(name: str, status: str = STATUS_PASS_V2) -> ValidateCheckV2:
    return ValidateCheckV2(name, status, None)


def test_finalizer_rejects_unknown_check_name() -> None:
    with pytest.raises(ValidateReportConstructionErrorV2, match="not in the declared validate check domain"):
        _finalize_validate_checks_v2((_local_check("not_a_real_check"),))


def test_finalizer_rejects_duplicate_observed_check() -> None:
    with pytest.raises(ValidateReportConstructionErrorV2, match="emitted more than once"):
        _finalize_validate_checks_v2((_local_check("target_root"), _local_check("target_root")))


def test_finalizer_rejects_caller_fabricated_unvalidated_check() -> None:
    """Only the authority may emit an `unvalidated` row. A caller doing it
    could silently narrow or widen the disclosed authority boundary."""

    spec = validate_unvalidated_specs_v2()[0]
    with pytest.raises(ValidateReportConstructionErrorV2, match="only the authority may emit it"):
        _finalize_validate_checks_v2((ValidateCheckV2(spec.name, STATUS_UNAVAILABLE_V2, spec.unvalidated_reason_code),))


def test_finalizer_rejects_locally_evaluable_check_marked_unavailable() -> None:
    with pytest.raises(ValidateReportConstructionErrorV2, match="was emitted with status"):
        _finalize_validate_checks_v2((_local_check("target_root", STATUS_UNAVAILABLE_V2),))


def test_finalizer_canonicalizes_into_authority_order_and_always_appends_U() -> None:
    """`R(x) = Canonicalize(O(x) union U)` -- ordering comes from the authority,
    so it is decided in exactly one place."""

    out_of_order = (_local_check("receipt"), _local_check("target_root"))
    finalized = _finalize_validate_checks_v2(out_of_order)
    names = [c.name for c in finalized]
    index = validate_check_index_v2()
    assert [index[n] for n in names] == sorted(index[n] for n in names)
    assert {spec.name for spec in validate_unvalidated_specs_v2()} <= set(names)


def test_no_report_construction_bypasses_the_finalizer() -> None:
    """The finalizer's value is that it REFUSES unsanctioned input, so a caller
    that builds the checks tuple directly would restore today's behaviour
    exactly and no behavioural test would notice. Only a bypass guard closes
    that: every `ValidateReportV2(...)` construction must take its `checks`
    from `_finalize_validate_checks_v2(...)`.

    Same justification as the CLI guard -- forbidding bypass of an existing
    choke point, not inferring semantics from source."""

    validate_source = (REPO_ROOT / "app" / "agent_review" / "target_pack_validate_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(validate_source)

    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ValidateReportV2"
    ]
    assert constructions, "expected at least one ValidateReportV2 construction"
    for call in constructions:
        checks_kwarg = next((kw for kw in call.keywords if kw.arg == "checks"), None)
        assert checks_kwarg is not None, "ValidateReportV2 constructed without an explicit checks= argument"
        assert (
            isinstance(checks_kwarg.value, ast.Call)
            and isinstance(checks_kwarg.value.func, ast.Name)
            and checks_kwarg.value.func.id == "_finalize_validate_checks_v2"
        ), "ValidateReportV2.checks must come directly from _finalize_validate_checks_v2(...)"


def test_finalizer_accepts_the_empty_observation() -> None:
    """An earliest-refusal path observes nothing locally, and must still
    disclose every structurally unvalidated capability."""

    finalized = _finalize_validate_checks_v2(())
    assert [c.name for c in finalized] == [spec.name for spec in validate_unvalidated_specs_v2()]
    assert all(c.status == STATUS_UNAVAILABLE_V2 for c in finalized)


# --- Both directions closed over a real corpus ---------------------------


def _corpus_reports(tmp_path: Path):
    healthy = tmp_path / "healthy"
    healthy.mkdir()
    _install(healthy, receipt=_receipt())

    empty = tmp_path / "empty"
    empty.mkdir()

    aiops_only = tmp_path / "aiops_only"
    (aiops_only / ".aiops").mkdir(parents=True)

    no_profile = tmp_path / "no_profile"
    no_profile.mkdir()
    _install(no_profile, receipt=_receipt(), profile_text=None)

    absent = tmp_path / "absent"  # never created

    return {
        "healthy": run_validate_v2(target_root=healthy),
        "empty": run_validate_v2(target_root=empty),
        "aiops_only": run_validate_v2(target_root=aiops_only),
        "no_profile": run_validate_v2(target_root=no_profile),
        "absent": run_validate_v2(target_root=absent),
    }


def test_every_report_is_bounded_by_the_domain(tmp_path: Path) -> None:
    """`R(x) subset of D` and `U subset of R(x)`, for every corpus input
    including the earliest refusals."""

    domain = set(validate_check_domain_names_v2())
    unvalidated = {spec.name for spec in validate_unvalidated_specs_v2()}
    for label, report in _corpus_reports(tmp_path).items():
        names = [c.name for c in report.checks]
        assert set(names) <= domain, label
        assert unvalidated <= set(names), label
        assert len(names) == len(set(names)), label


def test_healthy_installation_is_a_positive_witness_for_every_local_spec(tmp_path: Path) -> None:
    """`exists h: O(h) = L` -- the direction subset containment cannot give.

    Without this, a `LOCALLY_EVALUABLE` spec the runtime can never emit (a dead
    identity) would still satisfy `R(x) subset of D` on every input, and the
    generated view would publish a check that does not exist."""

    report = _corpus_reports(tmp_path)["healthy"]
    unvalidated = {spec.name for spec in validate_unvalidated_specs_v2()}
    observed_local = {c.name for c in report.checks if c.name not in unvalidated}
    assert observed_local == set(validate_locally_evaluable_names_v2())
    assert report.is_valid is True


def test_ledger_indirected_identities_are_in_the_domain_without_source_parsing(tmp_path: Path) -> None:
    """`target_owned_integrity`/`generated_file_integrity` reach the runtime
    through a dict lookup, so no source scan can establish that they are
    emitted. Membership is now a fact about the authority, and emission a fact
    about a real run -- neither is inferred from Python text."""

    assert TARGET_OWNED_INTEGRITY_CHECK_V2 in validate_check_domain_names_v2()
    assert GENERATED_FILE_INTEGRITY_CHECK_V2 in validate_check_domain_names_v2()
    emitted = {c.name for c in _corpus_reports(tmp_path)["healthy"].checks}
    assert TARGET_OWNED_INTEGRITY_CHECK_V2 in emitted
    assert GENERATED_FILE_INTEGRITY_CHECK_V2 in emitted


# --- Projections are derived, never independently maintained -------------


def test_legacy_constants_are_exactly_projections_of_the_domain() -> None:
    assert VALIDATE_CHECK_ORDER_V2 == validate_check_domain_names_v2()
    assert UNVALIDATED_CAPABILITIES_V2 == tuple(
        (spec.name, spec.unvalidated_reason_code) for spec in validate_unvalidated_specs_v2()
    )


def test_no_second_complete_enumeration_of_either_domain() -> None:
    """A narrow partial binding (ledger kind -> identity) is a different typed
    relation and is allowed; a rival complete list is not."""

    validate_source = (REPO_ROOT / "app" / "agent_review" / "target_pack_validate_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(validate_source)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) >= len(TARGET_PACK_VALIDATE_CHECKS_V2):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                name = getattr(target, "id", "<expr>")
                raise AssertionError(f"{name} looks like a second complete check enumeration")


# --- Generated view -------------------------------------------------------


def _run_view_generator(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VIEW_GENERATOR), *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def test_generated_view_is_in_sync() -> None:
    result = _run_view_generator("--check")
    assert result.returncode == 0, result.stderr


def test_generated_view_projects_both_authorities_exactly() -> None:
    view = json.loads(VIEW_PATH.read_text(encoding="utf-8"))
    assert view["format_id"] == "aiops.agent-review.internal.target-pack-runtime-authority-view.v1"
    assert view["generated"]["generator"] == "target-pack-runtime-authority-view-v1"
    assert view["cli_surface"]["commands"] == sorted(cli_command_names_v2())
    assert [c["name"] for c in view["validate_check_domain"]["checks"]] == list(validate_check_domain_names_v2())
    for entry, spec in zip(view["validate_check_domain"]["checks"], TARGET_PACK_VALIDATE_CHECKS_V2):
        assert entry["evaluation_class"] == spec.evaluation_class.value
        assert entry["unvalidated_reason_code"] == spec.unvalidated_reason_code


def test_generated_view_persists_no_derivable_field() -> None:
    """`total`, per-class counts and a separate `unvalidated_capabilities`
    list are all functions of the specs. Persisting them would create a second
    representation of one fact."""

    view = json.loads(VIEW_PATH.read_text(encoding="utf-8"))
    assert set(view) == {"format_id", "generated", "cli_surface", "validate_check_domain"}
    assert set(view["validate_check_domain"]) == {"checks"}
    assert set(view["cli_surface"]) == {"commands"}
    for entry in view["validate_check_domain"]["checks"]:
        assert set(entry) == {"name", "evaluation_class", "unvalidated_reason_code"}


def test_generated_view_hand_edit_is_detected() -> None:
    original = VIEW_PATH.read_text(encoding="utf-8")
    try:
        mutated = json.loads(original)
        mutated["cli_surface"]["commands"].append("rogue")
        VIEW_PATH.write_text(json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert _run_view_generator("--check").returncode != 0
    finally:
        VIEW_PATH.write_text(original, encoding="utf-8")
