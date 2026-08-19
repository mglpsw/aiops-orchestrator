"""`#203-D0` successor -- tests for the anchor-bound CURRENT-truth compiler
(`app.agent_review.target_pack_current_state_v1`) and its generator script
(`scripts/generate-target-pack-current-state.py`).

Two test classes:

- unit tests of the compiler module, using INJECTED fixture readers (no real
  git commits needed) to exercise the mutation classes that killed both
  prior designs -- subject, role, polarity, set-identity, evidence binding;
- integration tests of the generator script against the real repository,
  proving the closed slot registry, byte-identical `--check`, and that a
  hand edit to either the compiled JSON or a generated Markdown block is
  detected.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

from app.agent_review.target_pack_current_state_v1 import (  # noqa: E402
    CURRENT_INPUTS_FORMAT_ID_V1,
    NORMATIVE_SURFACE_FORMAT_ID_V1,
    TargetPackCurrentStateError,
    compile_current_state,
    compiled_state_to_json_dict,
    extract_cli_subcommands,
    extract_declared_surface,
    extract_validate_authority,
    is_full_sha,
    load_current_inputs,
    render_compiled_json,
)

_ANCHOR = "a" * 40
_TESTED = "b" * 40
_CANONICAL = "c" * 40


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _valid_inputs_doc(**overrides: object) -> dict:
    doc = {
        "format_id": CURRENT_INPUTS_FORMAT_ID_V1,
        "implementation_anchor": _ANCHOR,
        "reconciliation": {"reconciled_at": "2026-08-19T09:00:00-03:00"},
        "historical_evidence": [
            {
                "pr": 244,
                "recorded_tested_sha": _TESTED,
                "canonical_sha": _CANONICAL,
                "suite": {"passed": 2801, "skipped": 4},
                "evidence_class": "recorded_qualification",
                "evidence_ref": {"kind": "git_commit_message", "sha": _CANONICAL},
            }
        ],
    }
    doc.update(overrides)
    return doc


def _always_exists(sha: str) -> bool:
    return is_full_sha(sha)


# --- Non-derivable inputs loader ---------------------------------------


def test_duplicate_json_key_rejected(tmp_path: Path) -> None:
    raw = '{"format_id": "x", "format_id": "y", "implementation_anchor": "a", "reconciliation": {}, "historical_evidence": []}'
    p = _write(tmp_path / "inputs.json", raw)
    with pytest.raises(TargetPackCurrentStateError, match="duplicate JSON key"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_missing_top_level_key_rejected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    del doc["reconciliation"]
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="missing"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_extra_top_level_key_rejected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["unexpected"] = True
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="unexpected"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_bool_rejected_where_int_expected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["suite"]["passed"] = True
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="not a bool"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_bool_rejected_for_pr_field(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["pr"] = True
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="not a bool"):
        load_current_inputs(p, commit_exists=_always_exists)


@pytest.mark.parametrize(
    "bad_sha",
    ["short", "g" * 40, "A" * 40, "a" * 39, "a" * 41, ""],
)
def test_sha_must_be_full_lowercase_40_hex(tmp_path: Path, bad_sha: str) -> None:
    doc = _valid_inputs_doc(implementation_anchor=bad_sha)
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="40-hex"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_commit_existence_checked(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="does not exist"):
        load_current_inputs(p, commit_exists=lambda sha: False)


def test_evidence_ref_sha_must_equal_canonical_sha_for_commit_message_kind(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["evidence_ref"]["sha"] = _TESTED  # wrong: should equal canonical_sha
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="evidence_ref.sha must equal canonical_sha"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_recorded_tested_sha_absent_from_git_does_not_block_load(tmp_path: Path) -> None:
    """M-CURRENT-17 (historical subject retention independence): a merged
    PR's own source branch is routinely deleted post-merge -- discovered via
    real CI, not simulation, when PR #244's tested_sha (a792b23c...) proved
    unreachable in a fresh checkout. `recorded_tested_sha` must therefore
    load cleanly even when the git object database has never heard of it,
    as long as every REACHABLE identity (anchor, canonical_sha,
    evidence_ref.sha) still exists."""

    doc = _valid_inputs_doc()

    def commit_exists(sha: str) -> bool:
        return sha != _TESTED  # every real identity exists; the recorded one does not

    inputs = load_current_inputs(_write(tmp_path / "inputs.json", json.dumps(doc)), commit_exists=commit_exists)
    assert inputs.historical_evidence[0].recorded_tested_sha == _TESTED


def test_canonical_sha_absent_from_git_blocks_load(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()

    def commit_exists(sha: str) -> bool:
        return sha != _CANONICAL

    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="canonical_sha .* does not exist"):
        load_current_inputs(p, commit_exists=commit_exists)


def test_evidence_ref_sha_absent_from_git_blocks_load(tmp_path: Path) -> None:
    """Independent of canonical_sha's own check: uses a non-`git_commit_message`
    kind (which carries no equality constraint) so evidence_ref.sha can be a
    THIRD sha and its own existence check can be isolated."""

    other_sha = "d" * 40
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["evidence_ref"] = {"kind": "external_reference", "sha": other_sha}

    def commit_exists(sha: str) -> bool:
        return sha != other_sha

    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="evidence_ref.sha .* does not exist"):
        load_current_inputs(p, commit_exists=commit_exists)


def test_evidence_ref_required(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    del doc["historical_evidence"][0]["evidence_ref"]
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="missing"):
        load_current_inputs(p, commit_exists=_always_exists)


@pytest.mark.parametrize(
    "bad_ts",
    ["2026-08-19", "2026-08-19T09:00:00", "not-a-timestamp", "2026-08-19 09:00:00-03:00"],
)
def test_reconciled_at_requires_rfc3339_with_explicit_offset(tmp_path: Path, bad_ts: str) -> None:
    doc = _valid_inputs_doc()
    doc["reconciliation"]["reconciled_at"] = bad_ts
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="RFC3339"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_reconciled_at_with_z_offset_accepted(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["reconciliation"]["reconciled_at"] = "2026-08-19T12:00:00Z"
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    inputs = load_current_inputs(p, commit_exists=_always_exists)
    assert inputs.reconciled_at.tzinfo is not None


def test_valid_inputs_load_cleanly(tmp_path: Path) -> None:
    p = _write(tmp_path / "inputs.json", json.dumps(_valid_inputs_doc()))
    inputs = load_current_inputs(p, commit_exists=_always_exists)
    assert inputs.implementation_anchor == _ANCHOR
    assert len(inputs.historical_evidence) == 1
    assert inputs.historical_evidence[0].evidence_ref.kind == "git_commit_message"
    assert inputs.historical_evidence[0].recorded_tested_sha == _TESTED


# --- Normative declared surface -----------------------------------------


def _spec_with_block(declared: list[str], *, format_id: str = NORMATIVE_SURFACE_FORMAT_ID_V1) -> str:
    payload = json.dumps({"format_id": format_id, "declared": declared})
    return (
        "# spec\n## 4. CLI surface\n"
        "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->\n"
        f"```json\n{payload}\n```\n"
        "<!-- END NORMATIVE: target-pack-surface-v1 -->\n"
    )


def test_normative_block_start_marker_must_be_unique() -> None:
    """Two normative blocks in the same document is ambiguous -- the
    extractor must fail loudly rather than silently take the first."""

    text = _spec_with_block(["init", "doctor"]) + _spec_with_block(["init", "doctor"])
    with pytest.raises(TargetPackCurrentStateError, match="unique"):
        extract_declared_surface(text)


def test_normative_block_missing_markers_rejected() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="not found"):
        extract_declared_surface("no markers here")


def test_normative_block_duplicate_json_key_rejected() -> None:
    text = (
        "## 4. CLI surface\n<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->\n"
        '```json\n{"format_id": "x", "format_id": "y", "declared": ["init"]}\n```\n'
        "<!-- END NORMATIVE: target-pack-surface-v1 -->\n"
    )
    with pytest.raises(TargetPackCurrentStateError, match="duplicate JSON key"):
        extract_declared_surface(text)


def test_normative_block_declared_names_must_be_unique() -> None:
    text = _spec_with_block(["init", "init"])
    with pytest.raises(TargetPackCurrentStateError, match="duplicate"):
        extract_declared_surface(text)


def test_normative_block_declared_must_be_non_empty() -> None:
    text = _spec_with_block([])
    with pytest.raises(TargetPackCurrentStateError, match="non-empty"):
        extract_declared_surface(text)


def test_normative_block_wrong_format_id_rejected() -> None:
    text = _spec_with_block(["init"], format_id="wrong.format.id")
    with pytest.raises(TargetPackCurrentStateError, match="format_id"):
        extract_declared_surface(text)


def test_normative_block_extracts_declared_surface() -> None:
    text = _spec_with_block(["init", "doctor", "validate"])
    assert extract_declared_surface(text) == frozenset({"init", "doctor", "validate"})


# --- AST extraction: CLI subcommands + validate authority ---------------


def test_ast_parser_sees_multiline_add_parser_calls() -> None:
    """A line-based scan would under-report this -- proven by this project's
    own anchor, where `validate`'s registration wraps across lines."""

    source = (
        "def build():\n"
        "    sub.add_parser(\n"
        '        "validate",\n'
        '        help="...",\n'
        "    )\n"
        '    sub.add_parser("init", help="x")\n'
    )
    assert extract_cli_subcommands(source) == frozenset({"validate", "init"})


def test_ast_parser_ignores_non_add_parser_calls() -> None:
    source = 'sub.add_parser("init")\nother.other_call("not-a-command")\n'
    assert extract_cli_subcommands(source) == frozenset({"init"})


_VALID_VALIDATE_SOURCE = """
A_CHECK = "a_check"
B_CHECK = "b_check"
A_REASON = "a_reason_v2"
VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (
    A_CHECK,
    B_CHECK,
)
UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (
    (B_CHECK, A_REASON),
)
"""


def test_validate_authority_extraction_totals() -> None:
    authority = extract_validate_authority(_VALID_VALIDATE_SOURCE)
    assert authority.total == 2
    assert authority.locally_evaluable == 1
    assert authority.permanently_unavailable == frozenset({"b_check"})


def test_validate_authority_fails_closed_on_non_name_element() -> None:
    source = _VALID_VALIDATE_SOURCE.replace("    A_CHECK,\n", '    "a_check_literal",\n')
    with pytest.raises(TargetPackCurrentStateError, match="non-Name"):
        extract_validate_authority(source)


def test_validate_authority_fails_closed_on_undefined_constant() -> None:
    source = _VALID_VALIDATE_SOURCE.replace("A_CHECK,\n    B_CHECK,", "A_CHECK,\n    UNDEFINED_CHECK,")
    with pytest.raises(TargetPackCurrentStateError, match="undefined constant"):
        extract_validate_authority(source)


def test_validate_authority_fails_closed_when_order_is_not_a_tuple_literal() -> None:
    source = _VALID_VALIDATE_SOURCE.replace(
        'VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    B_CHECK,\n)',
        "VALIDATE_CHECK_ORDER_V2 = some_function_call()",
    )
    with pytest.raises(TargetPackCurrentStateError, match="not statically representable"):
        extract_validate_authority(source)


def test_validate_authority_fails_closed_on_unavailable_naming_absent_check() -> None:
    source = _VALID_VALIDATE_SOURCE + '\nC_CHECK = "c_check"\n'
    source = source.replace(
        'UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (\n    (B_CHECK, A_REASON),\n)',
        'UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (\n    (B_CHECK, A_REASON),\n    (C_CHECK, A_REASON),\n)',
    )
    with pytest.raises(TargetPackCurrentStateError, match="absent from VALIDATE_CHECK_ORDER_V2"):
        extract_validate_authority(source)


def test_matches_real_anchor_exactly() -> None:
    """The decisive real-data check: this project's own anchor must
    reproduce 17/11/6 and the exact six-name unavailable set."""

    from app.agent_review.target_pack_current_state_v1 import git_commit_exists, read_anchor_blob

    anchor = "d454e8f2d272b9edb011513b4a8f5d4e89ece4c2"
    assert git_commit_exists(REPO_ROOT, anchor)

    cli_source = read_anchor_blob(REPO_ROOT, anchor, "scripts/agent-review-target-pack-v2.py")
    assert extract_cli_subcommands(cli_source) == frozenset({"init", "doctor", "validate"})

    validate_source = read_anchor_blob(REPO_ROOT, anchor, "app/agent_review/target_pack_validate_v2.py")
    authority = extract_validate_authority(validate_source)
    assert authority.total == 17
    assert authority.locally_evaluable == 11
    assert authority.permanently_unavailable == frozenset({
        "upstream_pack_identity", "target_owned_set", "generated_file_set",
        "rollout_capability", "previous_install_lineage", "trusted_check_inventory",
    })


# --- compile_current_state: the full pipeline, with injected readers ----


def _fixture_compile(
    *,
    declared_surface: frozenset[str],
    anchor_cli_source: str,
    anchor_validate_source: str,
    inputs_overrides: dict | None = None,
):
    tmp_inputs = _valid_inputs_doc(**(inputs_overrides or {}))

    from app.agent_review.target_pack_current_state_v1 import (
        CurrentInputsV1, HistoricalEvidenceRecordV1, EvidenceRefV1,
    )

    records = tuple(
        HistoricalEvidenceRecordV1(
            pr=e["pr"], recorded_tested_sha=e["recorded_tested_sha"], canonical_sha=e["canonical_sha"],
            suite_passed=e["suite"]["passed"], suite_skipped=e["suite"]["skipped"],
            evidence_class=e["evidence_class"],
            evidence_ref=EvidenceRefV1(kind=e["evidence_ref"]["kind"], sha=e["evidence_ref"]["sha"]),
        )
        for e in tmp_inputs["historical_evidence"]
    )
    inputs = CurrentInputsV1(
        implementation_anchor=tmp_inputs["implementation_anchor"],
        reconciled_at=datetime.fromisoformat(tmp_inputs["reconciliation"]["reconciled_at"]),
        historical_evidence=records,
    )

    blobs = {
        "scripts/agent-review-target-pack-v2.py": anchor_cli_source,
        "app/agent_review/target_pack_validate_v2.py": anchor_validate_source,
    }

    return compile_current_state(
        inputs=inputs,
        declared_surface=declared_surface,
        read_blob=lambda _anchor, path: blobs[path],
        committed_at=lambda _anchor: datetime(2026, 8, 18, 21, 56, 15, tzinfo=timezone.utc),
    )


_SIMPLE_CLI = 'sub.add_parser("init")\nsub.add_parser("doctor")\nsub.add_parser("validate")\n'
_SIMPLE_VALIDATE = _VALID_VALIDATE_SOURCE


def test_compile_canonical_is_subset_of_declared() -> None:
    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state.canonical == frozenset({"init", "doctor", "validate"})
    assert state.deferred == frozenset({"conformance"})


def test_compile_fails_closed_when_anchor_exposes_undeclared_command() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="canonical_not_subset_declared"):
        _fixture_compile(
            declared_surface=frozenset({"init", "doctor"}),  # missing "validate"
            anchor_cli_source=_SIMPLE_CLI,
            anchor_validate_source=_SIMPLE_VALIDATE,
        )


def test_compile_deferred_equals_declared_minus_canonical() -> None:
    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance", "upgrade"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state.deferred == frozenset({"conformance", "upgrade"})
    assert state.canonical | state.deferred == frozenset({"init", "doctor", "validate", "conformance", "upgrade"})


def test_compile_working_tree_candidate_mutation_does_not_affect_anchor_output() -> None:
    """The decisive anchor-binding property: `read_blob` here always
    returns the SAME anchor content regardless of what a caller might
    separately observe in a candidate working tree -- proving the
    compiler itself never conflates the two, since it has no other way to
    observe a "working tree" at all."""

    state_before = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    # A "candidate" CLI source that exposes conformance too -- irrelevant
    # unless the anchor's OWN blob is what changes.
    candidate_cli = _SIMPLE_CLI + 'sub.add_parser("conformance")\n'
    state_still_at_old_anchor = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI,  # anchor blob unchanged
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state_before.canonical == state_still_at_old_anchor.canonical
    # Only reading a DIFFERENT anchor blob changes canonical:
    state_new_anchor = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=candidate_cli,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state_new_anchor.canonical == frozenset({"init", "doctor", "validate", "conformance"})


def test_compile_validate_inventory_matches_authority_exactly() -> None:
    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state.validate_total == 2
    assert state.validate_locally_evaluable == 1
    assert state.validate_permanently_unavailable == frozenset({"b_check"})


def test_compile_fails_closed_when_reconciled_at_precedes_anchor_committed_at() -> None:
    """Direct unit proof of the temporal invariant: this must fail on the
    PURE compile step itself, not merely be inferable from a `--check`
    drift that a differently-dated regeneration would silently satisfy.
    Caught during this round: an earlier version of this compiler accepted
    a backdated `reconciled_at` with exit 0 on regeneration -- `--check`
    only looked RED because it was comparing against stale committed
    docs, not because the constraint was enforced."""

    with pytest.raises(TargetPackCurrentStateError, match="precedes the implementation anchor"):
        _fixture_compile(
            declared_surface=frozenset({"init", "doctor", "validate"}),
            anchor_cli_source=_SIMPLE_CLI,
            anchor_validate_source=_SIMPLE_VALIDATE,
            inputs_overrides={"reconciliation": {"reconciled_at": "2020-01-01T00:00:00-03:00"}},
        )


def test_compile_preserves_recorded_tested_sha_without_requiring_its_reachability() -> None:
    """`compile_current_state` never calls a reachability check on
    `recorded_tested_sha` -- it is carried through as opaque historical
    metadata, proven here by never wiring a commit-existence callable for it
    at all (only `read_blob`/`committed_at`, neither of which touches
    per-evidence SHAs)."""

    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state.historical_evidence[0].recorded_tested_sha == _TESTED
    assert state.historical_evidence[0].canonical_sha == _CANONICAL


# --- Deterministic serialization -----------------------------------------


def test_compiled_json_is_deterministic() -> None:
    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    a = render_compiled_json(state, source_inputs_path="x.json")
    b = render_compiled_json(state, source_inputs_path="x.json")
    assert a == b
    assert a.endswith("\n")
    parsed = json.loads(a)
    assert parsed == compiled_state_to_json_dict(state, source_inputs_path="x.json")


# --- Generator script integration (real repo) ----------------------------


def _load_generator_module():
    module_name = "generate_target_pack_current_state"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, REPO_ROOT / "scripts" / "generate-target-pack-current-state.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclass field resolution needs this registered first
    spec.loader.exec_module(module)
    return module


def _run_generator(*extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate-target-pack-current-state.py"), *extra_args],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


def test_bootstrap_migration_proof_legacy_synopsis_equals_new_declared_surface() -> None:
    """PASSO 21: proves the redesign changed the representation of
    authority, not the product surface -- `master@d454e8f2` (the pre-D0
    commit this successor's own anchor points at) did NOT yet carry the
    normative `declared[]` block; its own §4 CLI synopsis at that
    exact SHA is read here as the pre-migration ground truth and compared
    to what this successor's structured block now declares. NOT a
    permanent pin: after this successor merges, the structured block is
    the authority and this test's only job is a one-time equality proof."""

    from app.agent_review.target_pack_current_state_v1 import read_anchor_blob

    anchor = "d454e8f2d272b9edb011513b4a8f5d4e89ece4c2"
    legacy_spec = read_anchor_blob(REPO_ROOT, anchor, "docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md")
    section = legacy_spec.split("## 4. CLI surface", 1)[1].split("## 5.", 1)[0]
    block = re.findall(r"```text\n(.*?)```", section, flags=re.DOTALL)[0]
    legacy_names = {
        line.split()[0] for line in block.splitlines() if line.strip() and not line[:1].isspace()
    }

    declared = extract_declared_surface(
        (REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md").read_text(encoding="utf-8")
    )
    assert legacy_names == declared, (
        f"the successor's declared[] {sorted(declared)} does not match the pre-migration §4 synopsis "
        f"{sorted(legacy_names)} at the anchor -- this must be a pure representation change"
    )


def test_generator_check_passes_on_committed_state() -> None:
    result = _run_generator("--check")
    assert result.returncode == 0, result.stderr
    assert "byte-identical" in result.stdout


def test_generator_all_ten_slots_are_registered() -> None:
    module = _load_generator_module()
    assert len(module.VIEW_SLOTS) == 10
    assert len({s.slot_id for s in module.VIEW_SLOTS}) == 10
    for slot in module.VIEW_SLOTS:
        assert slot.slot_id.startswith(module.SLOT_NAMESPACE_PREFIX)
        assert slot.renderer in module.RENDERERS


def test_generator_rejects_unregistered_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_generator_module()
    text = "prefix\n<!-- BEGIN GENERATED: target-pack-current.bogus.slot -->\n<!-- END GENERATED: target-pack-current.bogus.slot -->\nsuffix\n"
    offenders = module._find_unregistered_markers(text, Path("fake.md"), {s.slot_id for s in module.VIEW_SLOTS})
    assert offenders and "bogus.slot" in offenders[0]


def test_generator_missing_begin_marker_rejected() -> None:
    module = _load_generator_module()
    with pytest.raises(module.GeneratorError, match="expected exactly one"):
        module._replace_slot("no markers", "target-pack-current.readme.status", "x", inline=True)


def test_generator_missing_end_marker_rejected() -> None:
    module = _load_generator_module()
    text = "<!-- BEGIN GENERATED: target-pack-current.readme.status -->only begin"
    with pytest.raises(module.GeneratorError, match="expected exactly one"):
        module._replace_slot(text, "target-pack-current.readme.status", "x", inline=True)


def test_generator_outside_block_bytes_preserved() -> None:
    module = _load_generator_module()
    slot_id = "target-pack-current.readme.status"
    text = f"before-text\n<!-- BEGIN GENERATED: {slot_id} -->old<!-- END GENERATED: {slot_id} -->\nafter-text"
    rendered = module._replace_slot(text, slot_id, "new-content", inline=True)
    assert rendered.startswith("before-text\n")
    assert rendered.endswith("\nafter-text")
    assert "new-content" in rendered
    assert "old" not in rendered


def test_generator_inline_slot_rejects_newline_content() -> None:
    module = _load_generator_module()
    slot_id = "target-pack-current.readme.status"
    text = f"<!-- BEGIN GENERATED: {slot_id} --><!-- END GENERATED: {slot_id} -->"
    with pytest.raises(module.GeneratorError, match="must not contain a newline"):
        module._replace_slot(text, slot_id, "line1\nline2", inline=True)


def test_generator_hand_edit_of_compiled_json_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = module_path = REPO_ROOT / "docs" / "generated" / "target-pack-current-state.json"
    original_text = original.read_text(encoding="utf-8")
    try:
        original.write_text(original_text.rstrip("\n") + '\n// hand edit is not valid json anyway\n', encoding="utf-8")
        result = _run_generator("--check")
        assert result.returncode != 0
    finally:
        original.write_text(original_text, encoding="utf-8")


def test_generator_hand_edit_of_markdown_block_detected() -> None:
    readme = REPO_ROOT / "README.md"
    original_text = readme.read_text(encoding="utf-8")
    try:
        mutated = original_text.replace(
            "Canonical on `master`", "Canonical on `master` (HAND EDITED)", 1
        )
        assert mutated != original_text, "expected marker text not found in README.md; update this test"
        readme.write_text(mutated, encoding="utf-8")
        result = _run_generator("--check")
        assert result.returncode != 0
        assert "README.md" in result.stderr
    finally:
        readme.write_text(original_text, encoding="utf-8")

