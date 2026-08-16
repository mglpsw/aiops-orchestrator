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
values, a fixture with no metadata record, a metadata record with no
fixture file, a duplicate fixture path, or a duplicate mutation exemplar
all raise ``CorpusMetadataError`` instead of silently ignoring the problem.

Round-1 adversarial review of `#238`/PR #239 (exact HEAD `ff308c9510`)
found this module itself had not applied its own thesis -- ONE FACT, ONE
HOME -- consistently:

- ``entry_point``/``expected_disposition`` were stored in ``CORPUS.json``
  ALONGSIDE ``classification``, three declarations of one fact with no
  check forcing them to agree; a `legal` record could declare `refused`
  plus a reason code and load anyway (the family test never consulted
  either field). Fixed by DELETING the two fields -- ``classification`` is
  now the only authority, and ``entry_point``/``expected_disposition`` are
  derived ``@property`` views on ``CorpusCase``. A record still carrying
  either key is now rejected as an unknown field, not merely a mismatched
  one.
- bijection was decided on ``{record.fixture_path for record in records}``,
  a Python ``set`` -- a lossy projection that let two distinct ``case_id``s
  declare the SAME ``fixture_path`` and pass, and that let a payload be
  swapped out from under a record while the corpus's own record count held
  steady. The exact bug class this authority's own ADR documents
  (`json.dumps` manufacturing a duplicate via a lossy projection). Fixed by
  checking ``fixture_path`` uniqueness explicitly, the same way ``case_id``
  already was, BEFORE any set is formed.
- disk discovery globbed only direct children of the four known
  classification directories, so a `.yamlcase` nested one level deeper, in
  a misspelled directory, or sitting at the fixtures root was invisible to
  both directions of the bijection check and could carry unreviewed
  content with zero test coverage. Fixed by discovering the whole tree
  (``rglob``) and then failing closed on any path whose shape does not
  match ``<classification>/<filename>.yamlcase``.
- ``mutation_target`` coverage was checked by comparing SETS of declared
  labels against a hard-coded set of labels the mutation tests claimed to
  cover -- proving only that each label occurs somewhere, not that the
  specific case declaring it is the one actually exercised. A second case
  silently reusing an already-covered label left the set unchanged and the
  coverage test green while exercising nothing new. Fixed by giving
  ``mutation_target`` a precise semantic (the case is the SOLE exemplar of
  that mutation) enforced exactly-once, with ``mutation_case(target)`` as
  the single lookup the mutation tests consume -- no parallel table.
- ``property_family`` shared a nullable-or-string loop with ``notes``/
  ``limitations``, so ``None`` and ``""`` both loaded as "no family",
  silently admitting a case that contributes no usable coverage evidence.
  Fixed by validating it separately: non-empty AND a member of a closed,
  test-only vocabulary.
- a redundant ``sys.path.insert`` duplicated what pytest's own rootdir
  insertion already provides; removed.

Round-2 review of the round-1 fix (exact HEAD `105b032310`) found one more
instance of the same class: ``property_family`` was validated against the
full ``PROPERTY_FAMILIES`` vocabulary regardless of ``classification``, so
an `ambiguous` case could declare `stock_parity` -- a family that only
describes `legal` cases -- and load anyway; vocabulary membership alone
checks a value's spelling, not its truth. Fixed by
``CLASSIFICATION_PROPERTY_FAMILIES``, coupling ``property_family`` to
``classification`` the same way ``entry_point``/``expected_disposition``
already are, with a module-load-time assertion that the mapping partitions
``PROPERTY_FAMILIES`` exactly (no family orphaned, none shared across two
classifications).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.common.strict_json import strict_json_loads

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "target_profile_yaml"
CORPUS_PATH = FIXTURES_ROOT / "CORPUS.json"

CLASSIFICATIONS = frozenset({"legal", "ambiguous", "invalid", "malformed"})
TEXT_ENCODINGS = frozenset({"utf-8"})
DECODE_ERRORS = frozenset({"strict", "surrogatepass"})

# The closed vocabulary of mutation-discrimination exemplars. Each member
# must resolve to EXACTLY ONE corpus case (`mutation_case`, below) -- see
# tests/agent_review/test_profile_loader_v2_mutation_discrimination.py.
MUTATION_TARGETS = frozenset({"collision_point_1", "collision_point_2", "merge_bypass"})

# The closed, test-only vocabulary of empirical property families the ADR
# describes. Not re-enumerated in the ADR itself -- this module is the one
# home for the list; the ADR points here instead of copying it.
PROPERTY_FAMILIES = frozenset(
    {
        "mapping_assignment_collision",
        "value_tag_multiple_candidates",
        "merge_key_unsupported",
        "constructor_failure",
        "non_mapping_document",
        "contract_validation",
        "stock_parity",
    }
)

# Round-2 review found that validating `property_family` against
# PROPERTY_FAMILIES alone checks only its SPELLING, not its truth: an
# `ambiguous` case could declare `stock_parity` (a family that only
# describes `legal` cases) and still load, because vocabulary membership
# says nothing about which family a given classification can actually
# demonstrate. This couples the two, the same way classification already
# determines `entry_point`/`expected_disposition`.
CLASSIFICATION_PROPERTY_FAMILIES: dict[str, frozenset[str]] = {
    "legal": frozenset({"stock_parity"}),
    "ambiguous": frozenset({"mapping_assignment_collision", "value_tag_multiple_candidates"}),
    "invalid": frozenset({"merge_key_unsupported", "contract_validation"}),
    "malformed": frozenset({"constructor_failure", "non_mapping_document"}),
}
assert set(CLASSIFICATION_PROPERTY_FAMILIES) == CLASSIFICATIONS
assert frozenset.union(*CLASSIFICATION_PROPERTY_FAMILIES.values()) == PROPERTY_FAMILIES
assert sum(len(v) for v in CLASSIFICATION_PROPERTY_FAMILIES.values()) == len(PROPERTY_FAMILIES), (
    "CLASSIFICATION_PROPERTY_FAMILIES must partition PROPERTY_FAMILIES -- a family "
    "shared by two classifications would silently widen what property_family is "
    "checked against for both"
)

# `classification` is the only authority for these two facts -- they are
# never stored in CORPUS.json (a record carrying either key is rejected as
# an unknown field by `_validate_record`, below).
_LEGAL_ENTRY_POINT = "_read_unambiguously_v2"
_NON_LEGAL_ENTRY_POINT = "load_target_profile_text_v2"
_LEGAL_DISPOSITION = "accepted_parity_with_stock"
_NON_LEGAL_DISPOSITION = "refused"

_REQUIRED_FIELDS = frozenset(
    {
        "case_id",
        "classification",
        "fixture_path",
        "text_encoding",
        "decode_errors",
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
    expected_reason_code: str | None
    property_family: str
    mutation_target: str | None
    origin: dict
    notes: str | None
    limitations: str | None
    fixtures_root: Path = FIXTURES_ROOT

    @property
    def entry_point(self) -> str:
        """Derived from `classification` -- never stored. `legal` is a
        YAML-authority reading-level property, never a contract-level one,
        so it is always read through `_read_unambiguously_v2`; every other
        classification goes through the full `load_target_profile_text_v2`
        pipeline."""
        return _LEGAL_ENTRY_POINT if self.classification == "legal" else _NON_LEGAL_ENTRY_POINT

    @property
    def expected_disposition(self) -> str:
        """Derived from `classification` -- never stored."""
        return _LEGAL_DISPOSITION if self.classification == "legal" else _NON_LEGAL_DISPOSITION

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

    # `expected_disposition` is DERIVED from `classification` (see
    # CorpusCase.expected_disposition); the reason code is still an
    # independently observed outcome, since it may legitimately evolve
    # without changing the taxonomy -- exactly how `recursive_alias_value`
    # moved from `malformed` to `invalid` under step-4 observation without
    # touching this rule.
    expected_reason_code = record["expected_reason_code"]
    if classification == "legal":
        if expected_reason_code is not None:
            raise CorpusMetadataError(
                f"{case_id}: classification=legal must have a null "
                f"expected_reason_code, got {expected_reason_code!r}"
            )
    else:
        if not isinstance(expected_reason_code, str) or not expected_reason_code:
            raise CorpusMetadataError(
                f"{case_id}: classification={classification!r} must have a "
                f"non-empty expected_reason_code"
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
    if mutation_target is not None:
        if not isinstance(mutation_target, str) or mutation_target not in MUTATION_TARGETS:
            raise CorpusMetadataError(
                f"{case_id}: mutation_target must be null or a member of "
                f"MUTATION_TARGETS, got {mutation_target!r}"
            )

    property_family = record["property_family"]
    allowed_families = CLASSIFICATION_PROPERTY_FAMILIES[classification]
    if not isinstance(property_family, str) or property_family not in allowed_families:
        raise CorpusMetadataError(
            f"{case_id}: property_family must be one of {sorted(allowed_families)} "
            f"for classification={classification!r}, got {property_family!r}"
        )

    origin = record["origin"]
    if not isinstance(origin, dict):
        raise CorpusMetadataError(f"{case_id}: origin must be an object")

    for field_name in ("notes", "limitations"):
        value = record[field_name]
        if value is not None and not isinstance(value, str):
            raise CorpusMetadataError(f"{case_id}: {field_name} must be a string or null")

    return CorpusCase(
        case_id=case_id,
        classification=classification,
        fixture_path=fixture_path,
        text_encoding=text_encoding,
        decode_errors=decode_errors,
        expected_reason_code=expected_reason_code,
        property_family=property_family,
        mutation_target=mutation_target,
        origin=origin,
        notes=record["notes"],
        limitations=record["limitations"],
        fixtures_root=fixtures_root,
    )


def _discover_fixture_files(fixtures_root: Path) -> dict[str, Path]:
    """Discover EVERY `.yamlcase` under `fixtures_root`, not just direct
    children of the four known classification directories -- a file nested
    deeper, in a misspelled directory, or sitting at the root is observed
    here and then rejected explicitly by shape, rather than silently never
    being looked at."""
    found: dict[str, Path] = {}
    for path in fixtures_root.rglob("*.yamlcase"):
        rel = path.relative_to(fixtures_root)
        found[rel.as_posix()] = path
    return found


def _validate_fixture_layout(relative_paths: set[str]) -> None:
    """Fail closed on any discovered `.yamlcase` whose relative path is not
    exactly `<classification>/<filename>.yamlcase` under a recognized
    classification -- nested directories, unknown/misspelled classification
    names, and root-level files all raise by name instead of being
    silently excluded from the bijection check."""
    for rel in sorted(relative_paths):
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in CLASSIFICATIONS:
            raise CorpusMetadataError(
                f"fixture file has an invalid layout (expected "
                f"<classification>/<filename>.yamlcase under one of "
                f"{sorted(CLASSIFICATIONS)}): {rel!r}"
            )


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

    # `fixture_path` uniqueness is checked explicitly, BEFORE any set is
    # formed -- the same treatment `case_id` gets above. A set comparison
    # alone would let two distinct case_ids declare the same fixture_path
    # and silently collapse the duplicate, the exact class of bug
    # `json.dumps` manufactured in the loader this corpus documents.
    seen_paths: set[str] = set()
    for record in records:
        if record.fixture_path in seen_paths:
            raise CorpusMetadataError(
                f"duplicate fixture_path in CORPUS.json: {record.fixture_path!r} "
                f"(case_id={record.case_id!r})"
            )
        seen_paths.add(record.fixture_path)

    # `mutation_target` exactly-once: each non-null value must have a
    # single declaring case, so `mutation_case(target)` is unambiguous.
    seen_mutation_targets: dict[str, str] = {}
    for record in records:
        if record.mutation_target is None:
            continue
        if record.mutation_target in seen_mutation_targets:
            raise CorpusMetadataError(
                f"mutation_target {record.mutation_target!r} declared by both "
                f"{seen_mutation_targets[record.mutation_target]!r} and "
                f"{record.case_id!r} -- each target must have exactly one exemplar"
            )
        seen_mutation_targets[record.mutation_target] = record.case_id

    # Bijection: every fixture file has exactly one record, and vice versa.
    # Disk-side discovery walks the WHOLE tree (not just direct children of
    # the four known directories) and fails closed on any unrecognized
    # layout before the bijection comparison runs.
    disk_paths_by_rel = _discover_fixture_files(fixtures_root)
    _validate_fixture_layout(set(disk_paths_by_rel))

    declared_paths = seen_paths
    disk_paths = set(disk_paths_by_rel)

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


def mutation_case(mutation_target: str) -> CorpusCase:
    """The single corpus case declaring `mutation_target` as its
    `mutation_target`. `CORPUS.json` is the only authority for which case
    this is -- `load_corpus`'s exactly-once check guarantees the result is
    unambiguous; no second table of exemplars exists anywhere else."""
    if mutation_target not in MUTATION_TARGETS:
        raise KeyError(f"unknown mutation_target: {mutation_target!r}")
    for c in _all_cases():
        if c.mutation_target == mutation_target:
            return c
    raise KeyError(f"no corpus case declares mutation_target={mutation_target!r}")
