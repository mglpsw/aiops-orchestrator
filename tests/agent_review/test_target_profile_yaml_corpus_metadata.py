"""Fail-closed validation of the TargetProfile YAML corpus's own metadata
authority (`#238`).

`tests/agent_review/target_profile_yaml_corpus.py` is the loader for
`tests/agent_review/fixtures/target_profile_yaml/CORPUS.json`. This module
proves, rather than assumes, that the loader actually fails closed.

Round-1 adversarial review of PR #239 (exact HEAD `ff308c9510`) found five
holes in the version of the loader committed at that HEAD, each reproduced
against a temporary corpus before any fix was applied:

- two distinct `case_id`s could declare the SAME `fixture_path`, because
  bijection was decided on a Python `set` of paths -- a lossy projection
  that silently collapsed the duplicate;
- a payload could be swapped out from under a record (delete the file,
  repoint another record's `fixture_path` at a surviving one) while the
  corpus's own record count held steady;
- a `.yamlcase` nested one directory deeper, placed under a
  misspelled/unknown classification directory, or sitting at the fixtures
  root was invisible to disk-side discovery entirely;
- a `legal` record could declare `expected_disposition="refused"` plus a
  non-null `expected_reason_code`, and the legal family test never
  consulted either field;
- `property_family` shared a nullable-or-string loop with `notes`/
  `limitations`, so both `null` and `""` loaded as "no family";
- `mutation_target` coverage was checked by comparing label SETS, so a
  second case reusing an already-covered label left the set unchanged and
  the coverage test stayed green while exercising nothing new.

Every test below marked "(round-1 finding N)" is the committed regression
test for one of these; the corresponding fix in `target_profile_yaml_corpus.py`
is described in that module's own docstring.

Round-2 review found `property_family` was validated for classification
membership but not truth (an `ambiguous` case could declare `stock_parity`).
Round-3 review found that fix (`CLASSIFICATION_PROPERTY_FAMILIES`) was
necessary but not sufficient: two members of the SAME classification's
allowed family set could still be swapped on one case without detection,
since nothing checked which family that specific case's own bytes actually
demonstrate. Investigating this precisely -- generalizing the M1/M2
mutation-discrimination technique to every corpus case, and reading the
`__cause__` chain `_read_unambiguously_v2` produces -- found the finding
was not hypothetical: `duplicate_inside_key_mapping` was genuinely
mislabeled `ambiguous`/`mapping_assignment_collision`; its real failure is
a raw stock `ConstructorError` (an untagged complex key with no scalar
representation), never this authority's own collision guard. It is now
filed under `malformed`/`constructor_failure`. The per-family assertions
below (`test_*_family_is_true_for_the_case`) are the resulting committed,
per-case regression tests; every other corpus case's declared family was
verified true by the same investigation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import app.agent_review.profile_loader_v2 as loader_module
from tests.agent_review.target_profile_yaml_corpus import (
    CLASSIFICATIONS,
    CorpusMetadataError,
    cases,
    load_corpus,
)

_VALID_RECORD = {
    "case_id": "sample_case",
    "classification": "ambiguous",
    "fixture_path": "ambiguous/sample_case.yamlcase",
    "text_encoding": "utf-8",
    "decode_errors": "strict",
    "expected_reason_code": "target_profile_unreadable",
    "property_family": "mapping_assignment_collision",
    "mutation_target": None,
    "origin": {"pr": 236},
    "notes": None,
    "limitations": None,
}


def _write_corpus(root: Path, records: list[dict]) -> Path:
    """Materializes an isolated fixtures_root + CORPUS.json + declared
    .yamlcase files (so bijection holds unless the test wants it broken),
    and returns the CORPUS.json path."""
    for classification in ("legal", "ambiguous", "invalid", "malformed"):
        (root / classification).mkdir(parents=True, exist_ok=True)
    for record in records:
        fixture_path = record.get("fixture_path")
        if isinstance(fixture_path, str):
            full = root / fixture_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("x: 1\n", encoding="utf-8")
    corpus_path = root / "CORPUS.json"
    corpus_path.write_text(json.dumps(records), encoding="utf-8")
    return corpus_path


# -- the real, shipped corpus is itself valid -------------------------------


def test_the_real_corpus_loads_and_validates_cleanly() -> None:
    all_cases = cases()
    assert len(all_cases) == 49
    ids = [c.case_id for c in all_cases]
    assert len(ids) == len(set(ids)), "duplicate case_id in the shipped corpus"


def test_the_four_classifications_partition_the_corpus() -> None:
    """Round-1 finding 10: hard-coded per-class counts (49/11/15/9/14)
    restated the corpus's own composition in a second place -- a case
    moving classification without its count being updated elsewhere would
    go unnoticed. Assert the structural invariant instead: every case
    belongs to exactly one of the four classes, and their union is the
    whole corpus. `len(cases()) == 49` is kept separately, as a deliberate,
    commented anti-loss tripwire -- not a second classification authority
    -- so deleting a record and its fixture together still fails."""
    all_cases = cases()
    by_class = {cl: cases(cl) for cl in CLASSIFICATIONS}
    assert sum(len(v) for v in by_class.values()) == len(all_cases)
    seen: set[str] = set()
    for cl, subset in by_class.items():
        for c in subset:
            assert c.case_id not in seen, f"{c.case_id} counted in more than one classification"
            seen.add(c.case_id)
    assert seen == {c.case_id for c in all_cases}
    # anti-loss tripwire, not a second classification authority:
    assert len(all_cases) == 49


# -- round-3: each case's declared property_family must be TRUE, not merely --
# -- a member of the set its classification allows -----------------------------
#
# Round 2 coupled `property_family` to `classification`
# (`CLASSIFICATION_PROPERTY_FAMILIES`), which closed "any string" down to "a
# family this classification can demonstrate" -- but round-3 review found
# that within one classification's allowed set, a case could still be
# mislabeled with a DIFFERENT member of that same set and still load, since
# nothing checked which family THIS case's own bytes actually demonstrate.
#
# Investigating this precisely (patching each collision-point guard to stock
# in isolation, and reading the `__cause__` chain `_read_unambiguously_v2`
# itself produces) found the finding was not hypothetical: one shipped case,
# `duplicate_inside_key_mapping`, was genuinely mislabeled `ambiguous`/
# `mapping_assignment_collision` -- its real failure is a raw stock
# `ConstructorError` (an untagged complex key with no scalar
# representation), never `AmbiguousProfileDocumentV2`. It is now filed under
# `malformed`/`constructor_failure`, where the same investigation confirms
# it belongs. Every other corpus case's declared family was verified true.


def _read_or_none(case) -> tuple[object | None, BaseException | None]:
    """Returns (value, None) if `_read_unambiguously_v2` succeeds, or
    (None, exception) if it raises."""
    try:
        return loader_module._read_unambiguously_v2(case.text), None
    except loader_module.TargetProfileLoadErrorV2 as exc:
        return None, exc


def _collision_point_1_alone_accepts(text: str) -> bool:
    original = loader_module._CollisionRefusingSafeLoaderV2.construct_mapping
    loader_module._CollisionRefusingSafeLoaderV2.construct_mapping = yaml.SafeLoader.construct_mapping
    try:
        loader_module._read_unambiguously_v2(text)
        return True
    except loader_module.TargetProfileLoadErrorV2:
        return False
    finally:
        loader_module._CollisionRefusingSafeLoaderV2.construct_mapping = original


def _collision_point_2_alone_accepts(text: str) -> bool:
    original = loader_module._CollisionRefusingSafeLoaderV2.construct_scalar
    loader_module._CollisionRefusingSafeLoaderV2.construct_scalar = yaml.SafeLoader.construct_scalar
    try:
        loader_module._read_unambiguously_v2(text)
        return True
    except loader_module.TargetProfileLoadErrorV2:
        return False
    finally:
        loader_module._CollisionRefusingSafeLoaderV2.construct_scalar = original


def _by_family(classification: str, family: str) -> list:
    return [c for c in cases(classification) if c.property_family == family]


_MAPPING_COLLISION_CASES = _by_family("ambiguous", "mapping_assignment_collision")
_VALUE_TAG_CASES = _by_family("ambiguous", "value_tag_multiple_candidates")
_CONSTRUCTOR_FAILURE_CASES = _by_family("malformed", "constructor_failure")
_NON_MAPPING_DOCUMENT_CASES = _by_family("malformed", "non_mapping_document")
_MERGE_KEY_UNSUPPORTED_CASES = _by_family("invalid", "merge_key_unsupported")
_CONTRACT_VALIDATION_CASES = _by_family("invalid", "contract_validation")


@pytest.mark.parametrize("case", _MAPPING_COLLISION_CASES, ids=[c.case_id for c in _MAPPING_COLLISION_CASES])
def test_mapping_assignment_collision_family_is_true_for_the_case(case) -> None:
    assert _collision_point_1_alone_accepts(case.text), (
        f"{case.case_id}: declared mapping_assignment_collision, but bypassing "
        f"collision point 1 alone does not make the document acceptable"
    )


@pytest.mark.parametrize("case", _VALUE_TAG_CASES, ids=[c.case_id for c in _VALUE_TAG_CASES])
def test_value_tag_multiple_candidates_family_is_true_for_the_case(case) -> None:
    assert _collision_point_2_alone_accepts(case.text), (
        f"{case.case_id}: declared value_tag_multiple_candidates, but bypassing "
        f"collision point 2 alone does not make the document acceptable"
    )


@pytest.mark.parametrize("case", _CONSTRUCTOR_FAILURE_CASES, ids=[c.case_id for c in _CONSTRUCTOR_FAILURE_CASES])
def test_constructor_failure_family_is_true_for_the_case(case) -> None:
    _, exc = _read_or_none(case)
    assert exc is not None, f"{case.case_id}: declared constructor_failure but did not raise"
    assert not isinstance(exc.__cause__, loader_module.AmbiguousProfileDocumentV2), (
        f"{case.case_id}: declared constructor_failure, but the failure is actually "
        f"this authority's own collision guard (AmbiguousProfileDocumentV2), not a "
        f"raw PyYAML constructor failure"
    )


@pytest.mark.parametrize("case", _NON_MAPPING_DOCUMENT_CASES, ids=[c.case_id for c in _NON_MAPPING_DOCUMENT_CASES])
def test_non_mapping_document_family_is_true_for_the_case(case) -> None:
    value, exc = _read_or_none(case)
    assert exc is None, f"{case.case_id}: declared non_mapping_document but raised {exc!r}"
    assert not isinstance(value, dict), (
        f"{case.case_id}: declared non_mapping_document, but the read value is a dict"
    )


@pytest.mark.parametrize(
    "case", _MERGE_KEY_UNSUPPORTED_CASES, ids=[c.case_id for c in _MERGE_KEY_UNSUPPORTED_CASES]
)
def test_merge_key_unsupported_family_is_true_for_the_case(case) -> None:
    assert loader_module._document_uses_merge_v2(case.text) is True, (
        f"{case.case_id}: declared merge_key_unsupported, but "
        f"_document_uses_merge_v2 does not flag it"
    )


@pytest.mark.parametrize("case", _CONTRACT_VALIDATION_CASES, ids=[c.case_id for c in _CONTRACT_VALIDATION_CASES])
def test_contract_validation_family_is_true_for_the_case(case) -> None:
    assert loader_module._document_uses_merge_v2(case.text) is False, (
        f"{case.case_id}: declared contract_validation, but a merge key is present"
    )
    value, exc = _read_or_none(case)
    assert exc is None, (
        f"{case.case_id}: declared contract_validation, but the reading itself "
        f"raised {exc!r} -- refusal must come from contract validation, not the "
        f"YAML-authority reading"
    )


def test_every_ambiguous_and_malformed_and_invalid_case_has_a_family_assertion() -> None:
    """Fails closed on drift: if a new `PROPERTY_FAMILIES` member is ever
    added without a corresponding family-truth test above, this test -- not
    silent gap -- is what catches it."""
    covered_families = {
        "mapping_assignment_collision",
        "value_tag_multiple_candidates",
        "constructor_failure",
        "non_mapping_document",
        "merge_key_unsupported",
        "contract_validation",
        "stock_parity",  # covered by test_family_legal_documents_read_exactly_as_stock_safeloader
    }
    from tests.agent_review.target_profile_yaml_corpus import PROPERTY_FAMILIES

    assert covered_families == PROPERTY_FAMILIES


# -- JSON-level: duplicate object keys must never reach corpus validation ---


def test_duplicate_json_object_key_in_a_record_is_refused(tmp_path: Path) -> None:
    corpus_path = tmp_path / "CORPUS.json"
    # A literal duplicate key at the JSON level -- strict_json_loads must
    # refuse this before corpus-level validation ever runs.
    corpus_path.write_text(
        '[{"case_id": "x", "case_id": "y", "classification": "legal"}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        load_corpus(corpus_path=corpus_path)


def test_non_array_top_level_is_refused(tmp_path: Path) -> None:
    corpus_path = tmp_path / "CORPUS.json"
    corpus_path.write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(CorpusMetadataError, match="top level must be a JSON array"):
        load_corpus(corpus_path=corpus_path)


# -- record-shape fail-closed checks -----------------------------------------


def test_duplicate_case_id_across_records_is_refused(tmp_path: Path) -> None:
    r1 = dict(_VALID_RECORD)
    r2 = dict(_VALID_RECORD)  # same case_id, distinct JSON objects
    corpus_path = _write_corpus(tmp_path, [r1, r2])
    with pytest.raises(CorpusMetadataError, match="duplicate case_id"):
        load_corpus(corpus_path=corpus_path)


def test_unknown_classification_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, classification="not_a_real_classification")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown classification"):
        load_corpus(corpus_path=corpus_path)


def test_unknown_decode_errors_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, decode_errors="ignore")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown decode_errors"):
        load_corpus(corpus_path=corpus_path)


def test_a_record_carrying_the_removed_entry_point_field_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 3, closed structurally: `entry_point` and
    `expected_disposition` are no longer part of the metadata contract at
    all -- `classification` is the sole authority, and both are derived
    `@property` views on `CorpusCase`. A record attempting to carry either
    key back in is rejected as an unknown field, so the contradiction
    round 1 found (a `legal` record declaring `refused` plus a reason
    code) is now unrepresentable rather than merely detected."""
    record = dict(_VALID_RECORD, entry_point="load_target_profile_text_v2")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown="):
        load_corpus(corpus_path=corpus_path)


def test_a_record_carrying_the_removed_expected_disposition_field_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, expected_disposition="refused")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown="):
        load_corpus(corpus_path=corpus_path)


def test_unknown_field_in_record_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, unexpected_field="nope")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown="):
        load_corpus(corpus_path=corpus_path)


def test_missing_field_in_record_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD)
    del record["fixture_path"]
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="missing="):
        load_corpus(corpus_path=corpus_path)


def test_expected_reason_code_present_on_a_legal_case_is_refused(tmp_path: Path) -> None:
    """`expected_disposition` no longer exists as a field to contradict;
    the coupling is now expressed purely through `classification`."""
    record = dict(
        _VALID_RECORD,
        classification="legal",
        fixture_path="legal/sample_case.yamlcase",
        expected_reason_code="target_profile_unreadable",  # must be null for legal
    )
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="classification=legal must have a null"):
        load_corpus(corpus_path=corpus_path)


def test_expected_reason_code_null_on_a_non_legal_case_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, expected_reason_code=None)
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="must have a non-empty expected_reason_code"):
        load_corpus(corpus_path=corpus_path)


def test_fixture_path_outside_its_own_classification_directory_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, fixture_path="invalid/sample_case.yamlcase")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="does not live under"):
        load_corpus(corpus_path=corpus_path)


def test_fixture_path_not_ending_in_yamlcase_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, fixture_path="ambiguous/sample_case.yaml")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match=r"must end with \.yamlcase"):
        load_corpus(corpus_path=corpus_path)


def test_property_family_null_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 4: `property_family` shared a nullable loop with
    `notes`/`limitations`, so `None` loaded as "no family", silently
    admitting a case with no usable coverage evidence."""
    record = dict(_VALID_RECORD, property_family=None)
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="property_family must be"):
        load_corpus(corpus_path=corpus_path)


def test_property_family_empty_string_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 4, the other admitted value: `""` passed the same
    nullable-or-string check `None` did."""
    record = dict(_VALID_RECORD, property_family="")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="property_family must be"):
        load_corpus(corpus_path=corpus_path)


def test_property_family_off_vocabulary_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, property_family="not_a_real_family")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="property_family must be"):
        load_corpus(corpus_path=corpus_path)


def test_property_family_valid_but_wrong_for_classification_is_refused(tmp_path: Path) -> None:
    """Round-2 finding: `property_family` was validated against the full
    vocabulary regardless of `classification`, so an `ambiguous` case could
    declare `stock_parity` -- a family that only describes `legal` cases --
    and load anyway. Vocabulary membership alone checks a value's
    spelling, not its truth."""
    record = dict(_VALID_RECORD, classification="ambiguous", property_family="stock_parity")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="property_family must be one of"):
        load_corpus(corpus_path=corpus_path)


def test_mutation_target_off_vocabulary_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, mutation_target="not_a_real_target")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="mutation_target must be"):
        load_corpus(corpus_path=corpus_path)


def test_two_cases_declaring_the_same_mutation_target_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 5: coverage was checked by comparing label SETS, so
    a second case reusing an already-declared `mutation_target` left the
    set of declared labels unchanged and the coverage test stayed green
    while exercising nothing new. `mutation_target` now has a precise
    semantic -- the sole exemplar of that target -- enforced exactly-once
    at load time, before any test ever runs."""
    r1 = dict(_VALID_RECORD, case_id="case_a", fixture_path="ambiguous/case_a.yamlcase",
              mutation_target="collision_point_1")
    r2 = dict(_VALID_RECORD, case_id="case_b", fixture_path="ambiguous/case_b.yamlcase",
              mutation_target="collision_point_1")
    corpus_path = _write_corpus(tmp_path, [r1, r2])
    with pytest.raises(CorpusMetadataError, match="declared by both"):
        load_corpus(corpus_path=corpus_path)


# -- bijection: fixture_path uniqueness, disk discovery, and the two-way check --


def test_a_record_with_no_fixture_file_on_disk_is_refused(tmp_path: Path) -> None:
    corpus_path = _write_corpus(tmp_path, [])
    # write the record without materializing the fixture file
    record = dict(_VALID_RECORD)
    corpus_path.write_text(json.dumps([record]), encoding="utf-8")
    with pytest.raises(CorpusMetadataError, match="records with no fixture file on disk"):
        load_corpus(corpus_path=corpus_path)


def test_an_orphan_fixture_file_with_no_record_is_refused(tmp_path: Path) -> None:
    corpus_path = _write_corpus(tmp_path, [_VALID_RECORD])
    # an extra fixture file that no record declares
    orphan = tmp_path / "ambiguous" / "nobody_declares_me.yamlcase"
    orphan.write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(CorpusMetadataError, match="fixture files with no CORPUS.json record"):
        load_corpus(corpus_path=corpus_path)


def test_two_case_ids_declaring_the_same_fixture_path_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 1, reproduced: `{r.fixture_path for r in records}`
    is a Python set, so two distinct case_ids sharing one fixture_path
    collapsed to a single entry and the bijection check passed with the
    duplicate never observed. `fixture_path` uniqueness is now checked
    explicitly -- the same treatment `case_id` already got -- BEFORE any
    set is formed."""
    r1 = dict(_VALID_RECORD, case_id="case_a")
    r2 = dict(_VALID_RECORD, case_id="case_b")  # same fixture_path as case_a
    corpus_path = _write_corpus(tmp_path, [r1, r2])
    with pytest.raises(CorpusMetadataError, match="duplicate fixture_path"):
        load_corpus(corpus_path=corpus_path)


def test_swap_and_delete_preserving_record_count_is_refused(tmp_path: Path) -> None:
    """Round-1 finding 1, the sharper variant: a payload can be deleted
    and a surviving record repointed at another record's fixture_path,
    with the corpus's own record count held steady throughout -- silently
    dropping the removed payload's coverage. Caught by the same
    fixture_path-uniqueness check as the simpler duplicate above."""
    r1 = dict(_VALID_RECORD, case_id="case_a", fixture_path="ambiguous/case_a.yamlcase")
    r2 = dict(_VALID_RECORD, case_id="case_b", fixture_path="ambiguous/case_b.yamlcase")
    corpus_path = _write_corpus(tmp_path, [r1, r2])
    (tmp_path / "ambiguous" / "case_b.yamlcase").unlink()
    r2["fixture_path"] = "ambiguous/case_a.yamlcase"  # repointed at case_a's surviving payload
    corpus_path.write_text(json.dumps([r1, r2]), encoding="utf-8")
    with pytest.raises(CorpusMetadataError, match="duplicate fixture_path"):
        load_corpus(corpus_path=corpus_path)


@pytest.mark.parametrize(
    "label,relative_path",
    [
        ("nested one directory deeper", "ambiguous/nested/ghost.yamlcase"),
        ("unknown/misspelled classification directory", "typo_classification/ghost.yamlcase"),
        ("fixtures root, no classification directory", "ghost.yamlcase"),
    ],
)
def test_an_orphan_yamlcase_outside_the_recognized_layout_is_refused(
    tmp_path: Path, label: str, relative_path: str
) -> None:
    """Round-1 finding 2: disk-side discovery globbed only direct children
    of the four known classification directories, so a `.yamlcase` nested
    deeper, in a misspelled directory, or at the fixtures root was
    invisible to the bijection check in EITHER direction -- it could carry
    unreviewed content with zero test coverage and no error. Discovery now
    walks the whole tree and fails closed on any layout it does not
    recognize, by name."""
    corpus_path = _write_corpus(tmp_path, [_VALID_RECORD])
    ghost = tmp_path / relative_path
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(CorpusMetadataError, match="invalid layout"):
        load_corpus(corpus_path=corpus_path)
