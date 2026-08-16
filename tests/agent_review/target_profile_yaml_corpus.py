"""Test-only loader/validator for the TargetProfile YAML corpus (`#238`).

`CORPUS.json` is the single metadata authority for
`tests/agent_review/fixtures/target_profile_yaml/`. It is a top-level JSON
ARRAY, never an object keyed by ``case_id`` -- an object would let the
corpus's own metadata collapse a duplicate key exactly the way this corpus
exists to prove the loader under test no longer does. It is parsed by the
repository's existing ``app.common.strict_json.strict_json_loads``, which
already refuses duplicate JSON object keys and non-finite numbers -- reused,
not re-derived.

Every failure here is fail-closed: unknown/missing fields, unknown enum
values, a `legal` case not read through the reading-level entry point, a
fixture with no metadata record, or a metadata record with no fixture file
all raise ``CorpusMetadataError`` instead of silently ignoring the problem.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.common.strict_json import strict_json_loads  # noqa: E402

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "target_profile_yaml"
CORPUS_PATH = FIXTURES_ROOT / "CORPUS.json"

CLASSIFICATIONS = frozenset({"legal", "ambiguous", "invalid", "malformed"})
TEXT_ENCODINGS = frozenset({"utf-8"})
DECODE_ERRORS = frozenset({"strict", "surrogatepass"})
ENTRY_POINTS = frozenset({"load_target_profile_text_v2", "_read_unambiguously_v2"})
DISPOSITIONS = frozenset({"refused", "accepted_parity_with_stock"})

_REQUIRED_FIELDS = frozenset(
    {
        "case_id",
        "classification",
        "fixture_path",
        "text_encoding",
        "decode_errors",
        "entry_point",
        "expected_disposition",
        "expected_reason_code",
        "property_family",
        "mutation_target",
        "origin",
        "notes",
        "limitations",
    }
)


class CorpusMetadataError(ValueError):
    """Raised for any CORPUS.json record or corpus/fixture-tree mismatch
    that does not satisfy the metadata contract. Fail-closed: a case this
    module cannot validate is never silently admitted."""


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    classification: str
    fixture_path: str
    text_encoding: str
    decode_errors: str
    entry_point: str
    expected_disposition: str
    expected_reason_code: str | None
    property_family: str
    mutation_target: str | None
    origin: dict
    notes: str | None
    limitations: str | None
    fixtures_root: Path = FIXTURES_ROOT

    @property
    def payload_bytes(self) -> bytes:
        return (self.fixtures_root / self.fixture_path).read_bytes()

    @property
    def text(self) -> str:
        return self.payload_bytes.decode(self.text_encoding, errors=self.decode_errors)


def _validate_record(record: object, fixtures_root: Path) -> CorpusCase:
    if not isinstance(record, dict):
        raise CorpusMetadataError(f"corpus record is not an object: {record!r}")

    actual_fields = set(record.keys())
    if actual_fields != _REQUIRED_FIELDS:
        missing = _REQUIRED_FIELDS - actual_fields
        unknown = actual_fields - _REQUIRED_FIELDS
        raise CorpusMetadataError(
            f"corpus record {record.get('case_id', '<no case_id>')!r}: "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )

    case_id = record["case_id"]
    if not isinstance(case_id, str) or not case_id:
        raise CorpusMetadataError(f"invalid case_id: {case_id!r}")

    classification = record["classification"]
    if classification not in CLASSIFICATIONS:
        raise CorpusMetadataError(f"{case_id}: unknown classification {classification!r}")

    text_encoding = record["text_encoding"]
    if text_encoding not in TEXT_ENCODINGS:
        raise CorpusMetadataError(f"{case_id}: unknown text_encoding {text_encoding!r}")

    decode_errors = record["decode_errors"]
    if decode_errors not in DECODE_ERRORS:
        raise CorpusMetadataError(f"{case_id}: unknown decode_errors {decode_errors!r}")

    entry_point = record["entry_point"]
    if entry_point not in ENTRY_POINTS:
        raise CorpusMetadataError(f"{case_id}: unknown entry_point {entry_point!r}")

    # D14: `legal` is a YAML-authority reading-level property, never a
    # contract-level one, so it must always be read through
    # `_read_unambiguously_v2`, never through `load_target_profile_text_v2`.
    if classification == "legal" and entry_point != "_read_unambiguously_v2":
        raise CorpusMetadataError(
            f"{case_id}: classification=legal must use entry_point="
            f"_read_unambiguously_v2, got {entry_point!r}"
        )
    if classification != "legal" and entry_point != "load_target_profile_text_v2":
        raise CorpusMetadataError(
            f"{case_id}: classification={classification!r} must use entry_point="
            f"load_target_profile_text_v2, got {entry_point!r}"
        )

    expected_disposition = record["expected_disposition"]
    if expected_disposition not in DISPOSITIONS:
        raise CorpusMetadataError(f"{case_id}: unknown expected_disposition {expected_disposition!r}")

    expected_reason_code = record["expected_reason_code"]
    if expected_disposition == "accepted_parity_with_stock":
        if expected_reason_code is not None:
            raise CorpusMetadataError(
                f"{case_id}: accepted_parity_with_stock must have a null "
                f"expected_reason_code, got {expected_reason_code!r}"
            )
    else:
        if not isinstance(expected_reason_code, str) or not expected_reason_code:
            raise CorpusMetadataError(
                f"{case_id}: refused case must have a non-empty expected_reason_code"
            )

    fixture_path = record["fixture_path"]
    if not isinstance(fixture_path, str) or not fixture_path.endswith(".yamlcase"):
        raise CorpusMetadataError(f"{case_id}: fixture_path must end with .yamlcase, got {fixture_path!r}")
    if not fixture_path.startswith(f"{classification}/"):
        raise CorpusMetadataError(
            f"{case_id}: fixture_path {fixture_path!r} does not live under "
            f"its own classification directory {classification!r}/"
        )

    mutation_target = record["mutation_target"]
    if mutation_target is not None and not isinstance(mutation_target, str):
        raise CorpusMetadataError(f"{case_id}: mutation_target must be a string or null")

    origin = record["origin"]
    if not isinstance(origin, dict):
        raise CorpusMetadataError(f"{case_id}: origin must be an object")

    for field_name in ("property_family", "notes", "limitations"):
        value = record[field_name]
        if value is not None and not isinstance(value, str):
            raise CorpusMetadataError(f"{case_id}: {field_name} must be a string or null")

    return CorpusCase(
        case_id=case_id,
        classification=classification,
        fixture_path=fixture_path,
        text_encoding=text_encoding,
        decode_errors=decode_errors,
        entry_point=entry_point,
        expected_disposition=expected_disposition,
        expected_reason_code=expected_reason_code,
        property_family=record["property_family"],
        mutation_target=mutation_target,
        origin=origin,
        notes=record["notes"],
        limitations=record["limitations"],
        fixtures_root=fixtures_root,
    )


def _discover_fixture_files(fixtures_root: Path) -> set[str]:
    found = set()
    for classification in sorted(CLASSIFICATIONS):
        directory = fixtures_root / classification
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yamlcase")):
            found.add(f"{classification}/{path.name}")
    return found


def load_corpus(corpus_path: Path = CORPUS_PATH) -> tuple[CorpusCase, ...]:
    """Load and validate a corpus. `corpus_path`'s own parent directory is
    always treated as the fixtures root (matching the real layout, where
    `CORPUS.json` lives inside `fixtures/target_profile_yaml/` alongside
    `legal/`, `ambiguous/`, `invalid/`, `malformed/`), so a test can point
    this at an isolated temporary corpus and get isolated fixture
    resolution and bijection checking for free."""
    fixtures_root = corpus_path.parent
    raw = corpus_path.read_bytes()
    data = strict_json_loads(raw)

    if not isinstance(data, list):
        raise CorpusMetadataError(f"{corpus_path}: top level must be a JSON array, got {type(data).__name__}")

    records = [_validate_record(item, fixtures_root) for item in data]

    seen_ids: set[str] = set()
    for record in records:
        if record.case_id in seen_ids:
            raise CorpusMetadataError(f"duplicate case_id in CORPUS.json: {record.case_id!r}")
        seen_ids.add(record.case_id)

    # Bijection: every fixture file has exactly one record, and vice versa.
    declared_paths = {record.fixture_path for record in records}
    disk_paths = _discover_fixture_files(fixtures_root)

    orphan_records = declared_paths - disk_paths
    if orphan_records:
        raise CorpusMetadataError(f"CORPUS.json records with no fixture file on disk: {sorted(orphan_records)}")

    orphan_files = disk_paths - declared_paths
    if orphan_files:
        raise CorpusMetadataError(f"fixture files with no CORPUS.json record: {sorted(orphan_files)}")

    return tuple(records)


_ALL_CASES: tuple[CorpusCase, ...] | None = None


def _all_cases() -> tuple[CorpusCase, ...]:
    global _ALL_CASES
    if _ALL_CASES is None:
        _ALL_CASES = load_corpus()
    return _ALL_CASES


def cases(classification: str | None = None) -> tuple[CorpusCase, ...]:
    all_cases = _all_cases()
    if classification is None:
        return all_cases
    if classification not in CLASSIFICATIONS:
        raise CorpusMetadataError(f"unknown classification filter: {classification!r}")
    return tuple(c for c in all_cases if c.classification == classification)


def case(case_id: str) -> CorpusCase:
    for c in _all_cases():
        if c.case_id == case_id:
            return c
    raise KeyError(f"no corpus case with case_id={case_id!r}")
