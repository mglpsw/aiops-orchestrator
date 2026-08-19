"""`#203-D0` successor -- tests for the anchor-bound CURRENT-truth compiler
(`app.agent_review.target_pack_current_state_v1`) and its generator script
(`scripts/generate-target-pack-current-state.py`).

Two test classes:

- unit tests of the compiler module, using INJECTED fixture readers (no real
  git commits needed) to exercise the mutation classes that killed prior
  designs and Round 1 of this one -- subject, role, polarity, set-identity,
  evidence binding, canonical-subject provenance, active-subject extraction,
  evidence-claim derivability, static-authority totality;
- integration tests of the generator script against the real repository,
  proving the closed slot registry (including its GLOBAL, repo-wide scope),
  byte-identical `--check`, and that a hand edit to either the compiled JSON
  or a generated Markdown block is detected.
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
    EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_C2_QUALIFICATION_V1,
    HISTORICAL_EVIDENCE_KIND_C2_CANONICAL_COMMIT_QUALIFICATION_V1,
    NORMATIVE_SURFACE_FORMAT_ID_V1,
    CurrentInputsV1,
    EvidenceRefV1,
    HistoricalEvidenceRecordV1,
    TargetPackCurrentStateError,
    compile_current_state,
    compiled_state_to_json_dict,
    extract_c2_qualification,
    extract_cli_subcommands,
    extract_declared_surface,
    extract_validate_authority,
    git_is_ancestor,
    git_ref_exists,
    is_full_sha,
    load_current_inputs,
    read_anchor_blob,
    render_compiled_json,
    verify_anchor_freshness,
)

_ANCHOR = "a" * 40
_CANONICAL = "c" * 40
_OTHER_SHA = "d" * 40

_VALID_QUALIFICATION_MESSAGE = "some commit summary\n\nFull suite: 2801 passed, 4 skipped.\n"


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
                "kind": HISTORICAL_EVIDENCE_KIND_C2_CANONICAL_COMMIT_QUALIFICATION_V1,
                "canonical_sha": _CANONICAL,
                "evidence_ref": {
                    "kind": EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_C2_QUALIFICATION_V1,
                    "sha": _CANONICAL,
                },
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


def test_evidence_kind_rejected_if_unsupported(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["kind"] = "bogus_kind"
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="not a supported historical evidence kind"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_evidence_ref_kind_rejected_if_unsupported(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["evidence_ref"]["kind"] = "bogus_ref_kind"
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="not a supported evidence_ref kind"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_evidence_extra_key_suite_rejected(tmp_path: Path) -> None:
    """Mutation: hand-declaring suite counts in the input must be rejected
    outright -- the closed schema no longer has room for a claim the
    compiler doesn't itself derive from the canonical commit message."""

    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["suite"] = {"passed": 9999, "skipped": 0}
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="unexpected"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_evidence_extra_key_pr_rejected(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["pr"] = 244
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="unexpected"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_evidence_ref_sha_must_equal_canonical_sha(tmp_path: Path) -> None:
    doc = _valid_inputs_doc()
    doc["historical_evidence"][0]["evidence_ref"]["sha"] = _OTHER_SHA
    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="evidence_ref.sha must equal canonical_sha"):
        load_current_inputs(p, commit_exists=_always_exists)


def test_canonical_sha_absent_from_git_blocks_load(tmp_path: Path) -> None:
    """Also covers evidence_ref.sha's own reachability: the closed schema
    forces evidence_ref.sha == canonical_sha, so there is no longer a
    distinguishable "evidence_ref absent but canonical present" case --
    both checks fire on the same identity."""

    doc = _valid_inputs_doc()

    def commit_exists(sha: str) -> bool:
        return sha != _CANONICAL

    p = _write(tmp_path / "inputs.json", json.dumps(doc))
    with pytest.raises(TargetPackCurrentStateError, match="canonical_sha .* does not exist"):
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
    record = inputs.historical_evidence[0]
    assert record.kind == HISTORICAL_EVIDENCE_KIND_C2_CANONICAL_COMMIT_QUALIFICATION_V1
    assert record.canonical_sha == _CANONICAL
    assert record.evidence_ref.kind == EVIDENCE_REF_KIND_GIT_COMMIT_MESSAGE_C2_QUALIFICATION_V1
    assert record.evidence_ref.sha == _CANONICAL


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


def test_normative_block_end_marker_preceding_begin_marker_rejected() -> None:
    """Both markers unique and present, but END precedes BEGIN. A naive
    `split(BEGIN)[1].split(END)[0]` never finds END in the tail (it's
    earlier in the document) and silently returns everything to EOF --
    happily accepting the later, unrelated fenced JSON block below as the
    normative surface. Must fail closed instead."""

    text = (
        "<!-- END NORMATIVE: target-pack-surface-v1 -->\n"
        "some unrelated text\n"
        "<!-- BEGIN NORMATIVE: target-pack-surface-v1 -->\n"
        f'```json\n{{"format_id": "{NORMATIVE_SURFACE_FORMAT_ID_V1}", "declared": ["init"]}}\n```\n'
    )
    with pytest.raises(TargetPackCurrentStateError, match="precedes"):
        extract_declared_surface(text)


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


# --- AST extraction: active top-level CLI subcommands + validate authority


def _wrap_parse_args(body: str) -> str:
    return (
        "def _parse_args(argv):\n"
        "    parser = argparse.ArgumentParser()\n"
        "    sub = parser.add_subparsers(dest='command', required=True)\n"
        f"{body}"
        "    return parser.parse_args(argv)\n"
    )


def test_ast_parser_sees_multiline_add_parser_calls() -> None:
    """A line-based scan would under-report this -- proven by this project's
    own anchor, where `validate`'s registration wraps across lines."""

    source = _wrap_parse_args(
        "    validate_parser = sub.add_parser(\n"
        '        "validate",\n'
        '        help="...",\n'
        "    )\n"
        '    init_parser = sub.add_parser("init", help="x")\n'
    )
    assert extract_cli_subcommands(source) == frozenset({"validate", "init"})


def test_ast_parser_recognizes_bare_add_parser_call_without_assignment() -> None:
    source = _wrap_parse_args('    sub.add_parser("init")\n')
    assert extract_cli_subcommands(source) == frozenset({"init"})


def test_ast_parser_ignores_add_parser_call_in_unused_helper_function() -> None:
    """A call sitting in a DIFFERENT function entirely (never invoked from
    `_parse_args`) must have no effect on canonical -- it isn't part of
    `_parse_args`'s own construction, so it is neither classified nor
    flagged as a totality violation."""

    source = (
        "def _unused_helper():\n"
        '    return sub.add_parser("destroy")\n'
        "\n"
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
    """A registration on the RECOGNIZED subparsers variable, but not as a
    direct top-level statement of `_parse_args`, must fail closed rather
    than be silently skipped or silently included -- guessing execution
    semantics for `if False:` (or any other control flow) is out of scope
    for a static compiler."""

    source = _wrap_parse_args(
        '    init_parser = sub.add_parser("init")\n'
        "    if False:\n"
        '        sub.add_parser("destroy")\n'
    )
    with pytest.raises(TargetPackCurrentStateError, match="nested/conditional/helper"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_on_duplicate_top_level_name() -> None:
    source = _wrap_parse_args(
        '    init_parser = sub.add_parser("init")\n'
        '    sub.add_parser("init")\n'
    )
    with pytest.raises(TargetPackCurrentStateError, match="duplicate top-level subcommand"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_top_level_name_not_string_literal() -> None:
    source = _wrap_parse_args("    sub.add_parser(NAME_VAR)\n")
    with pytest.raises(TargetPackCurrentStateError, match="not a string literal"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_no_parse_args_function() -> None:
    source = 'sub.add_parser("init")\n'
    with pytest.raises(TargetPackCurrentStateError, match="exactly one top-level _parse_args"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_parse_args_not_unique() -> None:
    source = _wrap_parse_args('    sub.add_parser("init")\n') + "\n\n" + _wrap_parse_args('    sub.add_parser("doctor")\n')
    with pytest.raises(TargetPackCurrentStateError, match="exactly one top-level _parse_args"):
        extract_cli_subcommands(source)


def test_ast_parser_fails_closed_when_no_add_subparsers_assignment() -> None:
    source = (
        "def _parse_args(argv):\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--foo")\n'
        "    return parser.parse_args(argv)\n"
    )
    with pytest.raises(TargetPackCurrentStateError, match="add_subparsers"):
        extract_cli_subcommands(source)


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


def test_validate_authority_fails_closed_on_duplicate_check_name() -> None:
    source = _VALID_VALIDATE_SOURCE.replace(
        "VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    B_CHECK,\n)",
        "VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (\n    A_CHECK,\n    A_CHECK,\n)",
    )
    with pytest.raises(TargetPackCurrentStateError, match="duplicate check name"):
        extract_validate_authority(source)


def test_validate_authority_fails_closed_on_duplicate_module_assignment_of_check_order() -> None:
    source = _VALID_VALIDATE_SOURCE + "\nVALIDATE_CHECK_ORDER_V2 = (A_CHECK,)\n"
    with pytest.raises(TargetPackCurrentStateError, match="module-level assignments"):
        extract_validate_authority(source)


def test_validate_authority_fails_closed_on_duplicate_referenced_constant_symbol() -> None:
    source = _VALID_VALIDATE_SOURCE.replace(
        'A_CHECK = "a_check"\n', 'A_CHECK = "a_check"\nA_CHECK = "a_check_v2"\n',
    )
    with pytest.raises(TargetPackCurrentStateError, match="assigned more than once"):
        extract_validate_authority(source)


def test_matches_real_anchor_exactly() -> None:
    """The decisive real-data check: this project's own anchor must
    reproduce 17/11/6 and the exact six-name unavailable set, and the
    active-top-level-only extractor must still yield exactly the three
    real shipped subcommands."""

    from app.agent_review.target_pack_current_state_v1 import git_commit_exists

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


# --- Anchor freshness (a gate, checked separately from compile_current_state)


def test_freshness_fails_closed_when_canonical_ref_missing() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="CURRENT_STATE_STALE"):
        verify_anchor_freshness(
            anchor=_ANCHOR, canonical_ref="refs/remotes/origin/master",
            canonical_ref_exists=lambda ref: False,
            is_ancestor=lambda a, r: True,
            read_blob_at_ref=lambda ref, path: "same",
        )


def test_freshness_fails_closed_when_anchor_not_ancestor_of_canonical_ref() -> None:
    """The P1 case: an off-branch or fetched PR-head commit passes
    `commit_exists` (it's a real object) but is not on the canonical
    branch at all -- freshness must reject it explicitly rather than
    trusting mere object existence."""

    with pytest.raises(TargetPackCurrentStateError, match="CURRENT_STATE_STALE"):
        verify_anchor_freshness(
            anchor=_ANCHOR, canonical_ref="refs/remotes/origin/master",
            canonical_ref_exists=lambda ref: True,
            is_ancestor=lambda a, r: False,
            read_blob_at_ref=lambda ref, path: "same",
        )


def test_freshness_fails_closed_when_an_authority_blob_differs() -> None:
    def read_blob(ref: str, path: str) -> str:
        return "anchor-version" if ref == _ANCHOR else "canonical-version"

    with pytest.raises(TargetPackCurrentStateError, match="CURRENT_STATE_STALE"):
        verify_anchor_freshness(
            anchor=_ANCHOR, canonical_ref="refs/remotes/origin/master",
            canonical_ref_exists=lambda ref: True,
            is_ancestor=lambda a, r: True,
            read_blob_at_ref=read_blob,
        )


def test_freshness_passes_when_ancestor_and_authority_blobs_identical() -> None:
    """A later docs/tooling-only descendant commit on the canonical ref must
    NOT invalidate freshness -- only the two named authority paths are
    compared, so an unrelated file changing between anchor and canonical
    ref is invisible here, by design."""

    verify_anchor_freshness(  # must not raise
        anchor=_ANCHOR, canonical_ref="refs/remotes/origin/master",
        canonical_ref_exists=lambda ref: True,
        is_ancestor=lambda a, r: True,
        read_blob_at_ref=lambda ref, path: "identical-content",
    )


def test_freshness_passes_against_real_repo_for_current_anchor() -> None:
    from app.agent_review.target_pack_current_state_v1 import AUTHORITY_BEARING_PATHS_V1, CANONICAL_REF_V1

    anchor = "d454e8f2d272b9edb011513b4a8f5d4e89ece4c2"
    verify_anchor_freshness(  # must not raise against the real repo
        anchor=anchor, canonical_ref=CANONICAL_REF_V1,
        canonical_ref_exists=lambda ref: git_ref_exists(REPO_ROOT, ref),
        is_ancestor=lambda a, r: git_is_ancestor(REPO_ROOT, a, r),
        read_blob_at_ref=lambda ref, path: read_anchor_blob(REPO_ROOT, ref, path),
        authority_paths=AUTHORITY_BEARING_PATHS_V1,
    )


# --- Evidence provenance: derived, not hand-declared ---------------------


def test_extract_c2_qualification_derives_counts() -> None:
    assert extract_c2_qualification("prefix\nFull suite: 2801 passed, 4 skipped.\nsuffix") == (2801, 4)


def test_extract_c2_qualification_fails_closed_when_absent() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="expected exactly one"):
        extract_c2_qualification("nothing relevant in this message")


def test_extract_c2_qualification_fails_closed_when_ambiguous() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="expected exactly one"):
        extract_c2_qualification("Full suite: 1 passed, 0 skipped.\nFull suite: 2 passed, 1 skipped.\n")


# --- compile_current_state: the full pipeline, with injected readers ----


def _fixture_compile(
    *,
    declared_surface: frozenset[str],
    anchor_cli_source: str,
    anchor_validate_source: str,
    inputs_overrides: dict | None = None,
    commit_messages: dict[str, str] | None = None,
):
    tmp_inputs = _valid_inputs_doc(**(inputs_overrides or {}))

    records = tuple(
        HistoricalEvidenceRecordV1(
            kind=e["kind"], canonical_sha=e["canonical_sha"],
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
    messages = {_CANONICAL: _VALID_QUALIFICATION_MESSAGE, **(commit_messages or {})}

    return compile_current_state(
        inputs=inputs,
        declared_surface=declared_surface,
        read_blob=lambda _anchor, path: blobs[path],
        committed_at=lambda _anchor: datetime(2026, 8, 18, 21, 56, 15, tzinfo=timezone.utc),
        commit_message=lambda sha: messages[sha],
    )


_SIMPLE_CLI = _wrap_parse_args(
    '    init_parser = sub.add_parser("init")\n'
    '    doctor_parser = sub.add_parser("doctor")\n'
    '    validate_parser = sub.add_parser("validate")\n'
)
_SIMPLE_CLI_WITH_CONFORMANCE = _wrap_parse_args(
    '    init_parser = sub.add_parser("init")\n'
    '    doctor_parser = sub.add_parser("doctor")\n'
    '    validate_parser = sub.add_parser("validate")\n'
    '    conformance_parser = sub.add_parser("conformance")\n'
)
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
    state_still_at_old_anchor = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI,  # anchor blob unchanged
        anchor_validate_source=_SIMPLE_VALIDATE,
    )
    assert state_before.canonical == state_still_at_old_anchor.canonical
    # Only reading a DIFFERENT anchor blob changes canonical:
    state_new_anchor = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate", "conformance"}),
        anchor_cli_source=_SIMPLE_CLI_WITH_CONFORMANCE,
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
    drift that a differently-dated regeneration would silently satisfy."""

    with pytest.raises(TargetPackCurrentStateError, match="precedes the implementation anchor"):
        _fixture_compile(
            declared_surface=frozenset({"init", "doctor", "validate"}),
            anchor_cli_source=_SIMPLE_CLI,
            anchor_validate_source=_SIMPLE_VALIDATE,
            inputs_overrides={"reconciliation": {"reconciled_at": "2020-01-01T00:00:00-03:00"}},
        )


def test_compile_derives_suite_counts_from_canonical_commit_message() -> None:
    """The counts are never read from the input JSON -- only from the
    canonical commit's own message, mechanically."""

    state = _fixture_compile(
        declared_surface=frozenset({"init", "doctor", "validate"}),
        anchor_cli_source=_SIMPLE_CLI,
        anchor_validate_source=_SIMPLE_VALIDATE,
        commit_messages={_CANONICAL: "prefix\nFull suite: 2801 passed, 4 skipped.\nsuffix\n"},
    )
    assert state.historical_evidence[0].suite_passed == 2801
    assert state.historical_evidence[0].suite_skipped == 4


def test_compile_fails_closed_when_canonical_message_lacks_qualification_statement() -> None:
    """Mutation: point evidence_ref at a reachable commit whose message
    lacks the C2 qualification grammar entirely -- must fail closed, not
    silently publish zero/absent counts."""

    with pytest.raises(TargetPackCurrentStateError, match="expected exactly one"):
        _fixture_compile(
            declared_surface=frozenset({"init", "doctor", "validate"}),
            anchor_cli_source=_SIMPLE_CLI,
            anchor_validate_source=_SIMPLE_VALIDATE,
            commit_messages={_CANONICAL: "no qualification statement in this message\n"},
        )


def test_compile_fails_closed_on_ambiguous_duplicate_qualification_statements() -> None:
    with pytest.raises(TargetPackCurrentStateError, match="expected exactly one"):
        _fixture_compile(
            declared_surface=frozenset({"init", "doctor", "validate"}),
            anchor_cli_source=_SIMPLE_CLI,
            anchor_validate_source=_SIMPLE_VALIDATE,
            commit_messages={
                _CANONICAL: "Full suite: 2801 passed, 4 skipped.\nFull suite: 2802 passed, 5 skipped.\n"
            },
        )


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


def test_generator_hand_edit_of_compiled_json_detected() -> None:
    original = REPO_ROOT / "docs" / "generated" / "target-pack-current-state.json"
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


def test_generator_rejects_unregistered_marker_in_untracked_registered_file() -> None:
    """Part D / Grant F: an unknown `target-pack-current.*` marker placed in
    a tracked Markdown file that VIEW_SLOTS never opens (only the 5
    registered paths are read by the per-slot loop) must still be caught --
    proving the registry is closed GLOBALLY across every tracked Markdown
    file, not merely within the files it already renders."""

    changelog = REPO_ROOT / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    try:
        mutated = (
            original
            + "\n<!-- BEGIN GENERATED: target-pack-current.bogus.unregistered -->\n"
            + "<!-- END GENERATED: target-pack-current.bogus.unregistered -->\n"
        )
        changelog.write_text(mutated, encoding="utf-8")
        result = _run_generator("--check")
        assert result.returncode != 0
        assert "unregistered marker" in result.stderr
        assert "CHANGELOG.md" in result.stderr
    finally:
        changelog.write_text(original, encoding="utf-8")


def test_generator_rejects_known_slot_marker_copied_into_wrong_file() -> None:
    """A KNOWN slot_id's markers copied into a file other than its one
    registered path is an unowned duplicate that a per-registered-path-only
    scan would never notice (that path's own marker count stays exactly
    one)."""

    changelog = REPO_ROOT / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    try:
        mutated = (
            original
            + "\n<!-- BEGIN GENERATED: target-pack-current.readme.status -->\n"
            + "copied content\n"
            + "<!-- END GENERATED: target-pack-current.readme.status -->\n"
        )
        changelog.write_text(mutated, encoding="utf-8")
        result = _run_generator("--check")
        assert result.returncode != 0
        assert "registered to" in result.stderr
    finally:
        changelog.write_text(original, encoding="utf-8")
