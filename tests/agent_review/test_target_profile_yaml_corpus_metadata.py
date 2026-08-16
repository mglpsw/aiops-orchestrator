"""Fail-closed validation of the TargetProfile YAML corpus's own metadata
authority (`#238`).

`tests/agent_review/target_profile_yaml_corpus.py` is the loader for
`tests/agent_review/fixtures/target_profile_yaml/CORPUS.json`. This module
proves, rather than assumes, that the loader actually fails closed: a
metadata record with an unknown/missing field, a bad enum value, a
misclassified `legal` entry_point, an orphan fixture, an orphan record, a
duplicate `case_id`, or a duplicate JSON object key must all raise
`CorpusMetadataError` (or, for the JSON-level duplicate key, the
`app.common.strict_json` `ValueError` it already raises) -- never be
silently admitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.agent_review.target_profile_yaml_corpus import (
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
    "entry_point": "load_target_profile_text_v2",
    "expected_disposition": "refused",
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


def test_the_real_corpus_has_the_expected_per_classification_counts() -> None:
    assert len(cases("legal")) == 11
    assert len(cases("ambiguous")) == 15
    assert len(cases("invalid")) == 9
    assert len(cases("malformed")) == 14


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


def test_unknown_entry_point_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, entry_point="some_other_function")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="unknown entry_point"):
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


def test_expected_reason_code_present_on_an_accepted_case_is_refused(tmp_path: Path) -> None:
    record = dict(
        _VALID_RECORD,
        classification="legal",
        fixture_path="legal/sample_case.yamlcase",
        entry_point="_read_unambiguously_v2",
        expected_disposition="accepted_parity_with_stock",
        expected_reason_code="target_profile_unreadable",  # must be null
    )
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="must have a null expected_reason_code"):
        load_corpus(corpus_path=corpus_path)


def test_expected_reason_code_null_on_a_refused_case_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, expected_reason_code=None)
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="must have a non-empty expected_reason_code"):
        load_corpus(corpus_path=corpus_path)


def test_a_legal_case_declaring_the_contract_entry_point_is_refused(tmp_path: Path) -> None:
    """D14: `legal` is a YAML-authority reading-level property and must
    always be read through `_read_unambiguously_v2`, never through
    `load_target_profile_text_v2` (which would silently import contract
    validation into a language corpus)."""
    record = dict(
        _VALID_RECORD,
        classification="legal",
        fixture_path="legal/sample_case.yamlcase",
        entry_point="load_target_profile_text_v2",
        expected_disposition="accepted_parity_with_stock",
        expected_reason_code=None,
    )
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="classification=legal must use entry_point"):
        load_corpus(corpus_path=corpus_path)


def test_a_non_legal_case_declaring_the_reading_only_entry_point_is_refused(tmp_path: Path) -> None:
    record = dict(_VALID_RECORD, entry_point="_read_unambiguously_v2")
    corpus_path = _write_corpus(tmp_path, [record])
    with pytest.raises(CorpusMetadataError, match="must use entry_point=load_target_profile_text_v2"):
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


# -- bijection ----------------------------------------------------------------


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
