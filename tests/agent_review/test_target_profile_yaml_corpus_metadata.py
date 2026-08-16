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
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
