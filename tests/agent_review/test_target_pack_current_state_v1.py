"""`#203-D0` post-`STOP_REDESIGN_2` -- tests for the anchor-state projection
compiler (`app.agent_review.target_pack_current_state_v1`) and its generator
script (`scripts/generate-target-pack-current-state.py`).

Three test classes:

- unit tests of the compiler with INJECTED fixture readers (no real git
  commits needed), covering the defect classes that killed three successive
  architectures -- subject, role, polarity, set identity, evidence binding,
  active-subject extraction, and authority escalation;
- the algebra of the projection itself, scoped to compositions THIS compiler
  performs (universal git order laws belong to CAEM's Identity Relation
  Algebra, not here);
- integration tests of the generator against the real repository: the closed
  slot registry and its retired-namespace rule, golden renderer outputs, and
  byte-identical `--check`.

Guard discipline (frozen): exact structural shape and golden renderer output
are load-bearing; the retired-phrase regression is supplemental; generic
substring/keyword semantic scanners are forbidden -- they both miss
equivalents ("official on master") and false-RED on legitimate prose.
"""

from __future__ import annotations

import hashlib
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
    ANCHOR_INPUTS_FORMAT_ID_V1,
    ANCHOR_STATE_FORMAT_ID_V1,
    ANCHOR_STATE_GENERATOR_ID_V1,
    COMMIT_MESSAGE_EVIDENCE_KIND_V1,
    EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_V1,
    NORMATIVE_SURFACE_FORMAT_ID_V1,
    AnchorInputsV1,
    CommitMessageEvidenceRecordV1,
    EvidenceRefV1,
    NormativeSurfaceV1,
    TargetPackAnchorStateError,
    anchor_state_to_json_dict,
    canonical_normative_digest,
    compile_anchor_state,
    extract_cli_subcommands,
    extract_declared_surface,
    extract_normative_surface,
    extract_full_suite_counts,
    extract_validate_inventory,
    git_commit_exists,
    git_is_ancestor,
    is_full_sha,
    load_anchor_inputs,
    read_anchor_blob,
    render_anchor_state_json,
    verify_anchor_coherence,
)

_ANCHOR = "a" * 40
_EVIDENCE = "c" * 40
_OTHER = "d" * 40

_REAL_ANCHOR = "d454e8f2d272b9edb011513b4a8f5d4e89ece4c2"
_VALID_MESSAGE = "some commit summary\n\nFull suite: 2801 passed, 4 skipped.\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _valid_inputs_doc(**overrides: object) -> dict:
    doc = {
        "format_id": ANCHOR_INPUTS_FORMAT_ID_V1,
        "implementation_anchor": _ANCHOR,
        "reconciliation": {"reconciled_at": "2026-08-19T21:00:00-03:00"},
        "commit_message_evidence": [
            {
                "kind": COMMIT_MESSAGE_EVIDENCE_KIND_V1,
                "commit_sha": _EVIDENCE,
                "evidence_ref": {"kind": EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_V1, "sha": _EVIDENCE},
            }
        ],
    }
    doc.update(overrides)
    return doc


def _always_exists(sha: str) -> bool:
    return is_full_sha(sha)


# --- Inputs loader (structural only) ------------------------------------


def test_duplicate_json_key_rejected(tmp_path: Path) -> None:
    raw = '{"format_id": "x", "format_id": "y", "implementation_anchor": "a", "reconciliation": {}, "commit_message_evidence": []}'
    with pytest.raises(TargetPackAnchorStateError, match="duplicate JSON key"):
        load_anchor_inputs(_write(tmp_path / "i.json", raw), commit_exists=_always_exists)


def test_missing_top_level_key_rejected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    del doc["reconciliation"]
    with pytest.raises(TargetPackAnchorStateError, match="missing"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_extra_top_level_key_rejected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["unexpected"] = True
    with pytest.raises(TargetPackAnchorStateError, match="unexpected"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_retired_inputs_format_id_rejected(tmp_path: Path) -> None:
    """The old contract is retired, not versioned forward: an inputs file
    still declaring `…target-pack-current-inputs.v1` must not load."""

    doc = _valid_inputs_doc(format_id="aiops.agent-review.target-pack-current-inputs.v1")
    with pytest.raises(TargetPackAnchorStateError, match="format_id mismatch"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


@pytest.mark.parametrize("bad_sha", ["short", "g" * 40, "A" * 40, "a" * 39, "a" * 41, ""])
def test_sha_must_be_full_lowercase_40_hex(tmp_path: Path, bad_sha: str) -> None:
    doc = _valid_inputs_doc(implementation_anchor=bad_sha)
    with pytest.raises(TargetPackAnchorStateError, match="40-hex"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_anchor_commit_existence_checked(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    with pytest.raises(TargetPackAnchorStateError, match="does not exist"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=lambda sha: False)


def test_evidence_kind_rejected_if_unsupported(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["commit_message_evidence"][0]["kind"] = "c2_canonical_commit_qualification_v1"
    with pytest.raises(TargetPackAnchorStateError, match="not a supported evidence kind"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_evidence_ref_kind_rejected_if_unsupported(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["commit_message_evidence"][0]["evidence_ref"]["kind"] = "git_commit_message_c2_qualification_v1"
    with pytest.raises(TargetPackAnchorStateError, match="not a supported evidence_ref kind"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


@pytest.mark.parametrize("retired_key", ["suite", "pr", "canonical_sha", "recorded_tested_sha", "evidence_class"])
def test_retired_evidence_keys_rejected(tmp_path: Path, retired_key: str) -> None:
    """The closed shape leaves no field in which a claim the compiler never
    derives could be hand-declared -- the overclaim is unrepresentable by
    structure, not policed by vocabulary."""

    doc = _valid_inputs_doc()
    doc["commit_message_evidence"][0][retired_key] = "whatever"
    with pytest.raises(TargetPackAnchorStateError, match="unexpected"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_evidence_ref_sha_must_equal_commit_sha(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["commit_message_evidence"][0]["evidence_ref"]["sha"] = _OTHER
    with pytest.raises(TargetPackAnchorStateError, match="must equal commit_sha"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_evidence_commit_existence_checked(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    with pytest.raises(TargetPackAnchorStateError, match="commit_sha .* does not exist"):
        load_anchor_inputs(
            _write(tmp_path / "i.json", json.dumps(doc)), commit_exists=lambda sha: sha != _EVIDENCE
        )


@pytest.mark.parametrize(
    "bad_ts", ["2026-08-19", "2026-08-19T09:00:00", "not-a-timestamp", "2026-08-19 09:00:00-03:00"]
)
def test_reconciled_at_requires_rfc3339_with_explicit_offset(tmp_path: Path, bad_ts: str) -> None:
    doc = _valid_inputs_doc()
    doc["reconciliation"]["reconciled_at"] = bad_ts
    with pytest.raises(TargetPackAnchorStateError, match="RFC3339"):
        load_anchor_inputs(_write(tmp_path / "i.json", json.dumps(doc)), commit_exists=_always_exists)


def test_valid_inputs_load_cleanly(tmp_path: Path) -> None:
    inputs = load_anchor_inputs(
        _write(tmp_path / "i.json", json.dumps(_valid_inputs_doc())), commit_exists=_always_exists
    )
    assert inputs.implementation_anchor == _ANCHOR
    assert len(inputs.commit_message_evidence) == 1
    record = inputs.commit_message_evidence[0]
    assert record.kind == COMMIT_MESSAGE_EVIDENCE_KIND_V1
    assert record.commit_sha == _EVIDENCE
    assert record.evidence_ref.sha == _EVIDENCE


# --- Normative surface + its content digest ------------------------------


def _spec_with_block(payload_text: str) -> str:
    return (
        "# spec\n## 4. CLI surface\n"
        "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->\n"
        f"```json\n{payload_text}\n```\n"
        "<!-- END NORMATIVE: target-pack-surface-v1 -->\n"
    )


def _spec_with_declared(declared: list[str], *, format_id: str = NORMATIVE_SURFACE_FORMAT_ID_V1) -> str:
    return _spec_with_block(json.dumps({"format_id": format_id, "declared": declared}))


def test_normative_block_start_marker_must_be_unique() -> None:
    text = _spec_with_declared(["init", "doctor"]) + _spec_with_declared(["init", "doctor"])
    with pytest.raises(TargetPackAnchorStateError, match="unique"):
        extract_normative_surface(text)


def test_normative_block_missing_markers_rejected() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="not found"):
        extract_normative_surface("no markers here")


def test_normative_block_end_marker_preceding_begin_marker_rejected() -> None:
    """Both markers unique but reversed. A naive
    `split(BEGIN)[1].split(END)[0]` never finds END in the tail and silently
    returns everything to EOF, accepting the later unrelated fenced block."""

    text = (
        "<!-- END NORMATIVE: target-pack-surface-v1 -->\n"
        "unrelated\n"
        "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->\n"
        f'```json\n{{"format_id": "{NORMATIVE_SURFACE_FORMAT_ID_V1}", "declared": ["init"]}}\n```\n'
    )
    with pytest.raises(TargetPackAnchorStateError, match="precedes"):
        extract_normative_surface(text)


def test_normative_block_duplicate_json_key_rejected() -> None:
    text = _spec_with_block('{"format_id": "x", "format_id": "y", "declared": ["init"]}')
    with pytest.raises(TargetPackAnchorStateError, match="duplicate JSON key"):
        extract_normative_surface(text)


def test_normative_block_declared_names_must_be_unique() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="duplicate"):
        extract_normative_surface(_spec_with_declared(["init", "init"]))


def test_normative_block_declared_must_be_non_empty() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="non-empty"):
        extract_normative_surface(_spec_with_declared([]))


def test_normative_block_wrong_format_id_rejected() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="format_id"):
        extract_normative_surface(_spec_with_declared(["init"], format_id="wrong.format.id"))


def test_normative_block_extracts_declared_surface_and_digest() -> None:
    surface = extract_normative_surface(_spec_with_declared(["init", "doctor", "validate"]))
    assert surface.declared == frozenset({"init", "doctor", "validate"})
    assert surface.format_id == NORMATIVE_SURFACE_FORMAT_ID_V1
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", surface.content_sha256)


def test_digest_is_computed_from_the_parsed_object_not_hand_declared() -> None:
    declared = frozenset({"init", "doctor"})
    expected_canonical = json.dumps(
        {"format_id": NORMATIVE_SURFACE_FORMAT_ID_V1, "declared": sorted(declared)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    expected = "sha256:" + hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
    assert canonical_normative_digest(declared, format_id=NORMATIVE_SURFACE_FORMAT_ID_V1) == expected


def test_digest_invariant_under_semantically_equivalent_formatting() -> None:
    """Equivalence invariance: whitespace, key order and element order are
    presentation, not meaning -- `declared` is a set. Digesting raw bytes
    would contradict this; digesting the whole spec file would churn on every
    unrelated documentation edit."""

    compact = _spec_with_block('{"format_id":"%s","declared":["init","doctor","validate"]}' % NORMATIVE_SURFACE_FORMAT_ID_V1)
    reordered = _spec_with_block(
        '{\n  "declared": [\n    "validate",\n    "init",\n    "doctor"\n  ],\n'
        f'  "format_id": "{NORMATIVE_SURFACE_FORMAT_ID_V1}"\n}}'
    )
    a, b = extract_normative_surface(compact), extract_normative_surface(reordered)
    assert a.declared == b.declared
    assert a.content_sha256 == b.content_sha256


def test_declared_surface_projection_agrees_with_the_typed_extractor() -> None:
    """`extract_declared_surface` is a thin projection of
    `extract_normative_surface`, never a second parser -- so the name set the
    architecture tests consume and the digest identifying it always come from
    one parse and cannot drift apart."""

    text = _spec_with_declared(["init", "doctor", "validate"])
    assert extract_declared_surface(text) == extract_normative_surface(text).declared


def test_digest_changes_on_material_declared_change() -> None:
    a = extract_normative_surface(_spec_with_declared(["init", "doctor"]))
    b = extract_normative_surface(_spec_with_declared(["init", "doctor", "validate"]))
    assert a.content_sha256 != b.content_sha256


# --- Active top-level argparse extraction --------------------------------


def _wrap_parse_args(body: str) -> str:
    return (
        "def _parse_args(argv):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    sub = parser.add_subparsers(dest='command', required=True)\n"
        f"{body}"
        "    return parser.parse_args(argv)\n"
    )


def test_ast_parser_sees_multiline_add_parser_calls() -> None:
    source = _wrap_parse_args(
        "    validate_parser = sub.add_parser(\n"
        '        "validate",\n'
        '        help="...",\n'
        "    )\n"
        '    init_parser = sub.add_parser("init", help="x")\n'
    )
    assert extract_cli_subcommands(source) == frozenset({"validate", "init"})


def test_ast_parser_recognizes_bare_add_parser_call_without_assignment() -> None:
    assert extract_cli_subcommands(_wrap_parse_args('    sub.add_parser("init")\n')) == frozenset({"init"})


def test_ast_parser_ignores_add_parser_call_in_unused_helper_function() -> None:
    source = (
        "def _unused_helper():\n"
        '    return sub.add_parser("destroy")\n\n'
        + _wrap_parse_args('    init_parser = sub.add_parser("init")\n')
    )
    assert extract_cli_subcommands(source) == frozenset({"init"})


def test_ast_parser_ignores_call_on_different_subparsers_variable() -> None:
    source = _wrap_parse_args(
        '    init_parser = sub.add_parser("init")\n'
        '    other_parser = other.add_parser("not-a-command")\n'
    )
    assert extract_cli_subcommands(source) == frozenset({"init"})


def test_ast_parser_fails_closed_on_nested_conditional_construction() -> None:
    source = _wrap_parse_args(
        '    init_parser = sub.add_parser("init")\n'
        "    if False:\n"
        '        sub.add_parser("destroy")\n'
    )
    with pytest.raises(TargetPackAnchorStateError, match="nested/conditional/helper"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_on_duplicate_top_level_name() -> None:
    source = _wrap_parse_args('    init_parser = sub.add_parser("init")\n    sub.add_parser("init")\n')
    with pytest.raises(TargetPackAnchorStateError, match="duplicate top-level subcommand"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_top_level_name_not_string_literal() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="not a string literal"):
        extract_cli_subcommands(_wrap_parse_args("    sub.add_parser(NAME_VAR)\n"))


def test_ast_parser_fails_closed_when_no_parse_args_function() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="exactly one top-level _parse_args"):
        extract_cli_subcommands('sub.add_parser("init")\n')


def test_ast_parser_fails_closed_when_parse_args_not_unique() -> None:
    source = _wrap_parse_args('    sub.add_parser("init")\n') + "\n\n" + _wrap_parse_args('    sub.add_parser("doctor")\n')
    with pytest.raises(TargetPackAnchorStateError, match="exactly one top-level _parse_args"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_no_add_subparsers_assignment() -> None:
    source = (
        "def _parse_args(argv):\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--foo")\n'
        "    return parser.parse_args(argv)\n"
    )
    with pytest.raises(TargetPackAnchorStateError, match="add_subparsers"):
        extract_cli_subcommands(source)


# --- Validate inventory at the anchor ------------------------------------

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


def test_validate_inventory_totals() -> None:
    inv = extract_validate_inventory(_VALID_VALIDATE_SOURCE)
    assert (inv.total, inv.locally_evaluable) == (2, 1)
    assert inv.unvalidated_capabilities == frozenset({"b_check"})


def test_validate_inventory_fails_closed_on_non_name_element() -> None:
    source = _VALID_VALIDATE_SOURCE.replace("    A_CHECK,\n", '    "a_check_literal",\n')
    with pytest.raises(TargetPackAnchorStateError, match="non-Name"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_on_undefined_constant() -> None:
    source = _VALID_VALIDATE_SOURCE.replace("A_CHECK,\n    B_CHECK,", "A_CHECK,\n    UNDEFINED_CHECK,")
    with pytest.raises(TargetPackAnchorStateError, match="undefined constant"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_when_order_is_not_a_tuple_literal() -> None:
    source = _VALID_VALIDATE_SOURCE.replace(
        "VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    B_CHECK,\n)",
        "VALIDATE_CHECK_ORDER_V2 = some_function_call()",
    )
    with pytest.raises(TargetPackAnchorStateError, match="not statically representable"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_on_unvalidated_naming_absent_check() -> None:
    source = _VALID_VALIDATE_SOURCE + '\nC_CHECK = "c_check"\n'
    source = source.replace(
        "UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (\n    (B_CHECK, A_REASON),\n)",
        "UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (\n    (B_CHECK, A_REASON),\n    (C_CHECK, A_REASON),\n)",
    )
    with pytest.raises(TargetPackAnchorStateError, match="absent from VALIDATE_CHECK_ORDER_V2"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_on_duplicate_check_name() -> None:
    source = _VALID_VALIDATE_SOURCE.replace(
        "VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    B_CHECK,\n)",
        "VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    A_CHECK,\n)",
    )
    with pytest.raises(TargetPackAnchorStateError, match="duplicate check name"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_on_duplicate_module_assignment() -> None:
    source = _VALID_VALIDATE_SOURCE + "\nVALIDATE_CHECK_ORDER_V2 = (A_CHECK,)\n"
    with pytest.raises(TargetPackAnchorStateError, match="module-level assignments"):
        extract_validate_inventory(source)


def test_validate_inventory_fails_closed_on_duplicate_referenced_constant() -> None:
    source = _VALID_VALIDATE_SOURCE.replace('A_CHECK = "a_check"\n', 'A_CHECK = "a_check"\nA_CHECK = "a_check_v2"\n')
    with pytest.raises(TargetPackAnchorStateError, match="assigned more than once"):
        extract_validate_inventory(source)


def test_matches_real_anchor_exactly() -> None:
    """The decisive real-data check against this project's own anchor."""

    assert git_commit_exists(REPO_ROOT, _REAL_ANCHOR)

    cli_source = read_anchor_blob(REPO_ROOT, _REAL_ANCHOR, "scripts/agent-review-target-pack-v2.py")
    assert extract_cli_subcommands(cli_source) == frozenset({"init", "doctor", "validate"})

    inv = extract_validate_inventory(
        read_anchor_blob(REPO_ROOT, _REAL_ANCHOR, "app/agent_review/target_pack_validate_v2.py")
    )
    assert (inv.total, inv.locally_evaluable) == (17, 11)
    assert inv.unvalidated_capabilities == frozenset({
        "upstream_pack_identity", "target_owned_set", "generated_file_set",
        "rollout_capability", "previous_install_lineage", "trusted_check_inventory",
    })


# --- Anchor coherence (no ref, no remote, no forge) ----------------------


def test_coherence_fails_closed_when_anchor_missing() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="anchor_not_found"):
        verify_anchor_coherence(anchor=_ANCHOR, commit_exists=lambda s: False, is_ancestor=lambda a, d: True)


def test_coherence_fails_closed_when_anchor_off_checkout_lineage() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="anchor_not_in_checkout_lineage"):
        verify_anchor_coherence(anchor=_ANCHOR, commit_exists=lambda s: True, is_ancestor=lambda a, d: False)


def test_coherence_classifies_missing_and_off_lineage_distinctly() -> None:
    """`merge-base --is-ancestor` exits non-zero for BOTH an unknown object
    and a genuine non-ancestor; conflating them would report an
    environment/input failure as a truth verdict about the projection."""

    with pytest.raises(TargetPackAnchorStateError) as missing:
        verify_anchor_coherence(anchor=_ANCHOR, commit_exists=lambda s: False, is_ancestor=lambda a, d: False)
    with pytest.raises(TargetPackAnchorStateError) as off_lineage:
        verify_anchor_coherence(anchor=_ANCHOR, commit_exists=lambda s: True, is_ancestor=lambda a, d: False)
    assert "anchor_not_found" in str(missing.value)
    assert "anchor_not_in_checkout_lineage" in str(off_lineage.value)


def test_coherence_passes_against_the_real_repository() -> None:
    verify_anchor_coherence(  # must not raise
        anchor=_REAL_ANCHOR,
        commit_exists=lambda s: git_commit_exists(REPO_ROOT, s),
        is_ancestor=lambda a, d: git_is_ancestor(REPO_ROOT, a, d),
    )


def test_no_remote_or_ref_discovery_surface_exists() -> None:
    """No-authority-escalation, established by DEPENDENCY SURFACE rather than
    by scanning text: the withdrawn canonical-ref machinery is simply absent
    from the module, so no code path can consult a remote-tracking ref and
    re-derive a forge claim. Paired with the isolated-clone proof (which runs
    `--check` in a repository with zero remotes), this is what makes
    `CommitExists(A) ⇏ ForgeCanonical(A)` structural."""

    import app.agent_review.target_pack_current_state_v1 as mod

    for withdrawn in (
        "CANONICAL_REF_V1", "AUTHORITY_BEARING_PATHS_V1", "git_ref_exists",
        "verify_anchor_freshness", "RefExistsChecker",
    ):
        assert not hasattr(mod, withdrawn), f"{withdrawn} must not survive the redesign"


# --- Commit-message evidence ---------------------------------------------


def test_extract_full_suite_counts_derives_numbers() -> None:
    assert extract_full_suite_counts("x\nFull suite: 2801 passed, 4 skipped.\ny") == (2801, 4)


def test_extract_full_suite_counts_fails_closed_when_absent() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="exactly one"):
        extract_full_suite_counts("nothing relevant here")


def test_extract_full_suite_counts_fails_closed_when_ambiguous() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="exactly one"):
        extract_full_suite_counts("Full suite: 1 passed, 0 skipped.\nFull suite: 2 passed, 1 skipped.\n")


# --- compile_anchor_state, with injected readers -------------------------

_SIMPLE_CLI = _wrap_parse_args(
    '    init_parser = sub.add_parser("init")\n'
    '    doctor_parser = sub.add_parser("doctor")\n'
    '    validate_parser = sub.add_parser("validate")\n'
)
_CLI_WITH_CONFORMANCE = _wrap_parse_args(
    '    init_parser = sub.add_parser("init")\n'
    '    doctor_parser = sub.add_parser("doctor")\n'
    '    validate_parser = sub.add_parser("validate")\n'
    '    conformance_parser = sub.add_parser("conformance")\n'
)


def _fixture_compile(
    *,
    declared: frozenset[str],
    anchor_cli_source: str = _SIMPLE_CLI,
    anchor_validate_source: str = _VALID_VALIDATE_SOURCE,
    inputs_overrides: dict | None = None,
    commit_messages: dict[str, str] | None = None,
    ancestry: set[tuple[str, str]] | None = None,
):
    doc = _valid_inputs_doc(**(inputs_overrides or {}))
    records = tuple(
        CommitMessageEvidenceRecordV1(
            kind=e["kind"], commit_sha=e["commit_sha"],
            evidence_ref=EvidenceRefV1(kind=e["evidence_ref"]["kind"], sha=e["evidence_ref"]["sha"]),
        )
        for e in doc["commit_message_evidence"]
    )
    inputs = AnchorInputsV1(
        implementation_anchor=doc["implementation_anchor"],
        reconciled_at=datetime.fromisoformat(doc["reconciliation"]["reconciled_at"]),
        commit_message_evidence=records,
    )
    surface = NormativeSurfaceV1(
        format_id=NORMATIVE_SURFACE_FORMAT_ID_V1,
        declared=declared,
        content_sha256=canonical_normative_digest(declared, format_id=NORMATIVE_SURFACE_FORMAT_ID_V1),
    )
    blobs = {
        "scripts/agent-review-target-pack-v2.py": anchor_cli_source,
        "app/agent_review/target_pack_validate_v2.py": anchor_validate_source,
    }
    messages = {_EVIDENCE: _VALID_MESSAGE, **(commit_messages or {})}
    # Default: every evidence commit is an ancestor of the anchor (today's
    # real case is equality, which git's own `--is-ancestor` treats as true).
    edges = ancestry if ancestry is not None else {(r.commit_sha, doc["implementation_anchor"]) for r in records}

    return compile_anchor_state(
        inputs=inputs,
        normative_surface=surface,
        normative_surface_source_path="docs/checkpoints/SPEC.md",
        read_blob=lambda _a, path: blobs[path],
        committed_at=lambda _a: datetime(2026, 8, 18, 21, 56, 15, tzinfo=timezone.utc),
        commit_message=lambda sha: messages[sha],
        is_ancestor=lambda a, d: (a, d) in edges,
    )


def test_compile_exposed_is_subset_of_declared() -> None:
    state = _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance"}))
    assert state.exposed_at_anchor == frozenset({"init", "doctor", "validate"})
    assert state.declared_not_exposed_at_anchor == frozenset({"conformance"})


def test_compile_fails_closed_when_anchor_exposes_undeclared_command() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="exposed_not_subset_declared"):
        _fixture_compile(declared=frozenset({"init", "doctor"}))


def test_compile_working_tree_candidate_cannot_affect_anchor_output() -> None:
    """The anchor-binding property: only reading a DIFFERENT anchor blob
    changes the exposure set. The compiler has no way to observe a working
    tree at all."""

    declared = frozenset({"init", "doctor", "validate", "conformance"})
    a = _fixture_compile(declared=declared, anchor_cli_source=_SIMPLE_CLI)
    b = _fixture_compile(declared=declared, anchor_cli_source=_SIMPLE_CLI)
    assert a.exposed_at_anchor == b.exposed_at_anchor
    c = _fixture_compile(declared=declared, anchor_cli_source=_CLI_WITH_CONFORMANCE)
    assert c.exposed_at_anchor == declared


def test_compile_fails_closed_when_reconciled_at_precedes_anchor_committed_at() -> None:
    with pytest.raises(TargetPackAnchorStateError, match="precedes the implementation anchor"):
        _fixture_compile(
            declared=frozenset({"init", "doctor", "validate"}),
            inputs_overrides={"reconciliation": {"reconciled_at": "2020-01-01T00:00:00-03:00"}},
        )


def test_compile_fails_closed_when_evidence_commit_outside_anchor_history() -> None:
    """Closes the Round-2 finding: an evidence commit merely existing is not
    enough. What IS provable offline is membership in the anchor's own
    history -- not membership in canonical/forge history, which this compiler
    deliberately never asserts."""

    with pytest.raises(TargetPackAnchorStateError, match="evidence_commit_not_in_anchor_history"):
        _fixture_compile(declared=frozenset({"init", "doctor", "validate"}), ancestry=set())


def test_compile_derives_suite_counts_from_the_commit_message() -> None:
    state = _fixture_compile(
        declared=frozenset({"init", "doctor", "validate"}),
        commit_messages={_EVIDENCE: "x\nFull suite: 2801 passed, 4 skipped.\n"},
    )
    assert (state.commit_message_evidence[0].suite_passed, state.commit_message_evidence[0].suite_skipped) == (2801, 4)


def test_compile_suite_counts_track_the_message_mechanically() -> None:
    state = _fixture_compile(
        declared=frozenset({"init", "doctor", "validate"}),
        commit_messages={_EVIDENCE: "x\nFull suite: 2802 passed, 5 skipped.\n"},
    )
    assert (state.commit_message_evidence[0].suite_passed, state.commit_message_evidence[0].suite_skipped) == (2802, 5)


def test_compile_fails_closed_when_message_lacks_or_repeats_the_statement() -> None:
    for message in ("no statement here\n", "Full suite: 1 passed, 0 skipped.\nFull suite: 2 passed, 1 skipped.\n"):
        with pytest.raises(TargetPackAnchorStateError, match="exactly one"):
            _fixture_compile(
                declared=frozenset({"init", "doctor", "validate"}), commit_messages={_EVIDENCE: message}
            )


# --- Projection algebra (only compositions this compiler performs) -------


def test_algebra_lineage_reflexivity_on_the_real_repository() -> None:
    """`A ⪯ A`. Load-bearing because today's real evidence record has
    `commit_sha == implementation_anchor`, so the single ancestry predicate
    must accept equality."""

    assert git_is_ancestor(REPO_ROOT, _REAL_ANCHOR, _REAL_ANCHOR)


def test_algebra_concrete_lineage_composition_on_the_real_repository() -> None:
    """The exact chain this pipeline composes: `C ⪯ A` (evidence binding) and
    `A ⪯ HEAD` (anchor coherence). Universal order laws of the git DAG are
    Git's properties, not this compiler's, and belong to CAEM's Identity
    Relation Algebra rather than here."""

    assert git_is_ancestor(REPO_ROOT, _REAL_ANCHOR, _REAL_ANCHOR)   # C ⪯ A, equality case
    assert git_is_ancestor(REPO_ROOT, _REAL_ANCHOR, "HEAD")          # A ⪯ HEAD


def test_algebra_strict_ancestor_evidence_case_on_the_real_repository() -> None:
    """`C ≺ A` on real data: the same single predicate that accepts the
    equality case must accept a strictly earlier commit, so an evidence
    record may name any commit inside the anchor's own history."""

    strict_ancestor = "2f512d20802cf4f60f3d0b0b36d97a2b2fd224da"
    assert git_commit_exists(REPO_ROOT, strict_ancestor)
    assert strict_ancestor != _REAL_ANCHOR
    assert git_is_ancestor(REPO_ROOT, strict_ancestor, _REAL_ANCHOR)


def test_algebra_partition_coverage_and_difference() -> None:
    declared = frozenset({"init", "doctor", "validate", "conformance", "upgrade"})
    state = _fixture_compile(declared=declared)
    exposed, not_exposed = state.exposed_at_anchor, state.declared_not_exposed_at_anchor
    assert exposed & not_exposed == frozenset()          # disjoint
    assert exposed | not_exposed == declared             # coverage
    assert not_exposed == declared - exposed             # exact difference


def test_algebra_epoch_separation_orders_without_forcing_equality() -> None:
    """Anchor commit time and reconciliation time are distinct epochs of
    distinct propositions; only ordering is constrained."""

    state = _fixture_compile(declared=frozenset({"init", "doctor", "validate"}))
    assert state.anchor_committed_at != state.reconciled_at
    assert state.reconciled_at >= state.anchor_committed_at


def test_algebra_projection_discrimination() -> None:
    """A material change in either operand changes its own projection."""

    base = _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance"}))
    wider = _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance", "upgrade"}))
    assert base.normative_surface.content_sha256 != wider.normative_surface.content_sha256
    assert base.declared_not_exposed_at_anchor != wider.declared_not_exposed_at_anchor

    moved = _fixture_compile(
        declared=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_CLI_WITH_CONFORMANCE,
    )
    assert moved.exposed_at_anchor != base.exposed_at_anchor


# --- Serialized shape: load-bearing structural anti-recurrence -----------


def _fixture_json() -> dict:
    state = _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance"}))
    return anchor_state_to_json_dict(state, source_inputs_path="x.json")


def test_serialized_format_and_producer_identity() -> None:
    doc = _fixture_json()
    assert doc["format_id"] == ANCHOR_STATE_FORMAT_ID_V1 == "aiops.agent-review.target-pack-anchor-state.v1"
    assert doc["generated"]["generator"] == ANCHOR_STATE_GENERATOR_ID_V1 == "target-pack-anchor-state-v1"


def test_serialized_surface_shape_is_exact() -> None:
    """Load-bearing: with the key set closed there is simply no field in
    which `canonical`, `deferred`, `master` or `current` could be written."""

    assert set(_fixture_json()["state"]["surface"].keys()) == {
        "declared", "exposed_at_anchor", "declared_not_exposed_at_anchor",
    }


def test_serialized_evidence_shape_is_exact() -> None:
    record = _fixture_json()["state"]["commit_message_evidence"][0]
    assert set(record.keys()) == {"kind", "commit_sha", "evidence_ref", "suite"}
    assert set(record["evidence_ref"].keys()) == {"kind", "sha"}
    assert set(record["suite"].keys()) == {"passed", "skipped"}


def test_serialized_normative_surface_provenance_shape_is_exact() -> None:
    prov = _fixture_json()["state"]["normative_surface"]
    assert set(prov.keys()) == {"format_id", "source_path", "content_sha256"}
    assert prov["format_id"] == NORMATIVE_SURFACE_FORMAT_ID_V1
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", prov["content_sha256"])


def test_serialized_state_top_level_shape_is_exact() -> None:
    assert set(_fixture_json()["state"].keys()) == {
        "normative_surface", "surface", "validate_inventory_at_anchor", "temporal", "commit_message_evidence",
    }


def test_serialized_validate_inventory_shape_is_exact() -> None:
    """`permanently_unavailable` is withdrawn: the anchor's constants prove
    only that these dimensions are unvalidated by THIS implementation, never
    that they are impossible for any future target-pack version."""

    inv = _fixture_json()["state"]["validate_inventory_at_anchor"]
    assert set(inv.keys()) == {"total", "locally_evaluable", "unvalidated_capabilities"}


def test_serialized_json_is_deterministic() -> None:
    state = _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance"}))
    a = render_anchor_state_json(state, source_inputs_path="x.json")
    assert a == render_anchor_state_json(state, source_inputs_path="x.json")
    assert a.endswith("\n")
    assert json.loads(a) == anchor_state_to_json_dict(state, source_inputs_path="x.json")


# --- Generator integration (real repository) -----------------------------


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


# --- Golden renderer outputs: load-bearing claim strength ----------------


def _golden_state():
    return _fixture_compile(declared=frozenset({"init", "doctor", "validate", "conformance"}))


def test_golden_status_renderer_output() -> None:
    module = _load_generator_module()
    assert module._render_status(_golden_state()) == (
        f"Exposed by the target-pack CLI at implementation anchor `{_ANCHOR}`: "
        "`doctor`, `init`, `validate`. Declared in the normative surface but not exposed at that "
        "anchor: `conformance`."
    )


def test_golden_lifecycle_prose_renderer_output() -> None:
    module = _load_generator_module()
    assert module._render_lifecycle_prose(_golden_state()) == (
        f"`doctor`, `init`, `validate` are exposed by the target-pack CLI at implementation anchor `{_ANCHOR}`.\n"
        "The remaining declared subcommands are not exposed at that anchor."
    )


def test_golden_evidence_renderer_output() -> None:
    module = _load_generator_module()
    assert module._render_evidence(_golden_state()) == (
        f"Commit-message evidence at `{_EVIDENCE}` records: full suite 2801 passed, 4 skipped."
    )


def test_golden_inventory_renderer_output() -> None:
    module = _load_generator_module()
    assert module._render_inventory(_golden_state()) == (
        f"**Check inventory at implementation anchor `{_ANCHOR}`:** 2 total dimensions, "
        "1 locally evaluable when applicable, 1 reported `unavailable` because this validate "
        "implementation cannot establish them from the target alone: `b_check`."
    )


def test_retired_phrase_is_absent_from_every_renderer() -> None:
    """SUPPLEMENTAL regression only -- the golden tests above are the
    load-bearing semantic proof. A substring guard alone would both miss an
    equivalent overclaim ("official on master") and false-RED on legitimate
    historical prose, which is why it is not the primary defence."""

    module = _load_generator_module()
    state = _golden_state()
    for renderer in module.RENDERERS.values():
        assert "Canonical on `master`" not in renderer(state)


# --- Closed slot registry, including retired-namespace population --------


def test_generator_registers_exactly_ten_anchor_slots() -> None:
    module = _load_generator_module()
    assert module.SLOT_NAMESPACE_PREFIX == "target-pack-anchor."
    assert module.RETIRED_SLOT_NAMESPACE_PREFIX == "target-pack-current."
    assert len(module.VIEW_SLOTS) == 10
    assert len({s.slot_id for s in module.VIEW_SLOTS}) == 10
    for slot in module.VIEW_SLOTS:
        assert slot.slot_id.startswith(module.SLOT_NAMESPACE_PREFIX)
        assert slot.renderer in module.RENDERERS


def test_retired_slot_namespace_population_is_zero() -> None:
    """Migration invariant. Once SLOT_NAMESPACE_PREFIX moved, the global scan
    stops recognising the old prefix, so a residual old marker sitting beside
    a correct new one would otherwise be invisible. This is the retirement
    rule for ONE exact typed namespace, not inference from vocabulary."""

    module = _load_generator_module()
    residual = [
        str(p) for p in module._tracked_markdown_files()
        if p.exists() and f"GENERATED: {module.RETIRED_SLOT_NAMESPACE_PREFIX}" in p.read_text(encoding="utf-8")
    ]
    assert residual == []


def test_generator_missing_begin_or_end_marker_rejected() -> None:
    module = _load_generator_module()
    with pytest.raises(module.GeneratorError, match="expected exactly one"):
        module._replace_slot("no markers", "target-pack-anchor.readme.status", "x", inline=True)
    with pytest.raises(module.GeneratorError, match="expected exactly one"):
        module._replace_slot(
            "<!-- BEGIN GENERATED: target-pack-anchor.readme.status -->only begin",
            "target-pack-anchor.readme.status", "x", inline=True,
        )


def test_generator_outside_block_bytes_preserved() -> None:
    module = _load_generator_module()
    slot_id = "target-pack-anchor.readme.status"
    text = f"before\n<!-- BEGIN GENERATED: {slot_id} -->old<!-- END GENERATED: {slot_id} -->\nafter"
    rendered = module._replace_slot(text, slot_id, "new", inline=True)
    assert rendered.startswith("before\n") and rendered.endswith("\nafter")
    assert "new" in rendered and "old" not in rendered


def test_generator_inline_slot_rejects_newline_content() -> None:
    module = _load_generator_module()
    slot_id = "target-pack-anchor.readme.status"
    text = f"<!-- BEGIN GENERATED: {slot_id} --><!-- END GENERATED: {slot_id} -->"
    with pytest.raises(module.GeneratorError, match="must not contain a newline"):
        module._replace_slot(text, slot_id, "a\nb", inline=True)


def test_generator_check_passes_on_committed_state() -> None:
    result = _run_generator("--check")
    assert result.returncode == 0, result.stderr
    assert "byte-identical" in result.stdout


def test_generator_hand_edit_of_artifact_json_detected() -> None:
    artifact = REPO_ROOT / "docs" / "generated" / "target-pack-current-state.json"
    original = artifact.read_text(encoding="utf-8")
    try:
        artifact.write_text(original.rstrip("\n") + "\n// not valid json anyway\n", encoding="utf-8")
        assert _run_generator("--check").returncode != 0
    finally:
        artifact.write_text(original, encoding="utf-8")


def test_generator_hand_edit_of_markdown_block_detected() -> None:
    readme = REPO_ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    try:
        mutated = original.replace(
            "Exposed by the target-pack CLI", "Exposed by the target-pack CLI (HAND EDITED)", 1
        )
        assert mutated != original, "expected marker text not found in README.md; update this test"
        readme.write_text(mutated, encoding="utf-8")
        result = _run_generator("--check")
        assert result.returncode != 0
        assert "README.md" in result.stderr
    finally:
        readme.write_text(original, encoding="utf-8")


@pytest.mark.parametrize(
    ("marker_slot", "expected_stderr"),
    [
        ("target-pack-anchor.bogus.unregistered", "unregistered marker"),
        ("target-pack-anchor.readme.status", "registered to"),
        ("target-pack-current.readme.status", "retired slot namespace"),
    ],
)
def test_generator_global_registry_rejects_stray_markers(marker_slot: str, expected_stderr: str) -> None:
    """All three cases live in a tracked Markdown file that `VIEW_SLOTS`
    never opens, so only the repository-wide scan can see them: an unknown
    slot, a known slot copied outside its registered path, and a residual
    marker in the retired namespace."""

    changelog = REPO_ROOT / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    try:
        changelog.write_text(
            original
            + f"\n<!-- BEGIN GENERATED: {marker_slot} -->\nx\n<!-- END GENERATED: {marker_slot} -->\n",
            encoding="utf-8",
        )
        result = _run_generator("--check")
        assert result.returncode != 0
        assert expected_stderr in result.stderr
        assert "CHANGELOG.md" in result.stderr
    finally:
        changelog.write_text(original, encoding="utf-8")


def test_bootstrap_migration_proof_legacy_synopsis_equals_declared_surface() -> None:
    """One-time proof that the redesign changed the REPRESENTATION of
    authority, not the product surface: `master@d454e8f2` did not yet carry
    the normative block, so its own §4 CLI synopsis is read at that exact SHA
    as pre-migration ground truth. Not a permanent pin."""

    legacy_spec = read_anchor_blob(
        REPO_ROOT, _REAL_ANCHOR, "docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md"
    )
    section = legacy_spec.split("## 4. CLI surface", 1)[1].split("## 5.", 1)[0]
    block = re.findall(r"```text\n(.*?)```", section, flags=re.DOTALL)[0]
    legacy_names = {line.split()[0] for line in block.splitlines() if line.strip() and not line[:1].isspace()}

    surface = extract_normative_surface(
        (REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md").read_text(encoding="utf-8")
    )
    assert legacy_names == surface.declared
