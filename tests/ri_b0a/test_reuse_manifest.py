"""Tests for the #119.2 AgentReview/ProjectOps reuse manifest loader and view."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from app.ri_b0a import REUSE_STATES, load_reuse_manifest, render_reuse_view
from app.ri_b0a.reuse_manifest import ReuseManifestError, ReuseManifestLoadResult, ReuseManifestEntry

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST_PATH = REPO_ROOT / "config" / "ri" / "ri-b0a-2-reuse-manifest.json"
REAL_VIEW_PATH = REPO_ROOT / "docs" / "generated" / "RI_B0A_2_REUSE_REFERENCE.md"


def _valid_doc(**overrides: object) -> dict:
    doc = {
        "contract_id": "aiops.ri-b0a-2-reuse-manifest.v1",
        "schema_version": 1,
        "generated_from": "fixture",
        "states": sorted(REUSE_STATES),
        "entries": [
            {
                "contract_id": "fixture.contract.v1",
                "owner": "fixture owner",
                "state": "reuse",
                "notes": "fixture notes",
                "ri_b0_role": "fixture role",
                "source_path": None,
            }
        ],
    }
    doc.update(overrides)
    return doc


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ── real manifest: the actual delivered artifact ─────────────────────────


def test_real_manifest_loads_ok() -> None:
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    assert len(result.entries) >= 10  # 10 AgentReview contracts + 1 ProjectOps track entry


def test_real_manifest_covers_all_ten_agentreview_v2_schema_files() -> None:
    schema_dir = REPO_ROOT / "schemas" / "agent-review" / "v2"
    real_files = {f"schemas/agent-review/v2/{p.name}" for p in schema_dir.glob("*.schema.json")}
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    mapped_paths = {e.source_path for e in result.entries if e.source_path is not None}
    assert mapped_paths == real_files


def test_real_manifest_covers_every_real_schema_id_literal_not_just_files() -> None:
    """File-based coverage alone can miss a contract embedded in another
    file's own $defs (e.g. ChunkReviewResultV2's `agent-review.chunk-
    response.v2`, which shares a file with its enclosing envelope schema).
    This test cross-checks against every `schema_id: Literal[...]`
    declaration in app/agent_review/*.py directly, closing exactly the gap
    an independent Codex review found in an earlier draft (the manifest
    omitted agent-review.chunk-response.v2 entirely)."""
    agent_review_dir = REPO_ROOT / "app" / "agent_review"
    real_schema_ids: set[str] = set()
    for py_file in agent_review_dir.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        real_schema_ids.update(re.findall(r'schema_id:\s*Literal\["([a-z0-9.-]+)"\]', text))
    assert len(real_schema_ids) >= 10  # sanity: the regex itself still finds real declarations

    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    mapped_contract_ids = {e.contract_id for e in result.entries}
    assert real_schema_ids <= mapped_contract_ids, (
        f"schema_id literals missing from the manifest: {real_schema_ids - mapped_contract_ids}"
    )


def test_real_manifest_uses_every_declared_state_at_least_once() -> None:
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    assert {e.state for e in result.entries} == REUSE_STATES


def test_real_manifest_contract_ids_are_unique() -> None:
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    ids = [e.contract_id for e in result.entries]
    assert len(ids) == len(set(ids))


def test_real_generated_view_is_byte_identical_to_a_fresh_render() -> None:
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    rendered = render_reuse_view(result)
    assert REAL_VIEW_PATH.read_text(encoding="utf-8") == rendered


def test_rendering_twice_from_the_same_manifest_is_byte_identical() -> None:
    result = load_reuse_manifest(REAL_MANIFEST_PATH, repo_root=REPO_ROOT)
    assert result.ok, result.errors
    assert render_reuse_view(result) == render_reuse_view(result)


# ── totality / fail-closed: malformed input never raises ────────────────


def test_missing_file_is_total_and_fail_closed(tmp_path: Path) -> None:
    result = load_reuse_manifest(tmp_path / "does-not-exist.json", repo_root=tmp_path)  # must not raise
    assert not result.ok
    assert result.errors


def test_invalid_json_is_total_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    result = load_reuse_manifest(path, repo_root=tmp_path)  # must not raise
    assert not result.ok


def test_root_not_object_is_total_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_reuse_manifest(path, repo_root=tmp_path)  # must not raise
    assert not result.ok


def test_wrong_contract_id_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_doc(contract_id="wrong.id"))
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_doc(schema_version=2))
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_unexpected_top_level_key_is_rejected(tmp_path: Path) -> None:
    """Correction, found by an independent Codex review of an earlier
    draft: the loader checked only for MISSING top-level keys, so a
    manifest with an extra root field (e.g. injected by a bad merge or a
    tampered revision) silently passed. Confirmed genuinely red against
    the pre-fix loader before this test was added."""
    doc = _valid_doc()
    doc["unexpected_top_level"] = {"silently": "ignored"}
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_states_list_not_matching_enum_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_doc(states=["reuse", "reference"]))
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_empty_entries_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_doc(entries=[]))
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_unknown_state_value_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"][0]["state"] = "totally_fine_actually"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_duplicate_contract_ids_are_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"].append(dict(doc["entries"][0]))
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_entry_missing_required_key_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    del doc["entries"][0]["ri_b0_role"]
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_entry_with_unexpected_extra_key_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"][0]["injected_field"] = "surprise"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_source_path_pointing_outside_repo_root_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"][0]["source_path"] = "../../etc/passwd"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_source_path_absolute_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"][0]["source_path"] = "/etc/passwd"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_source_path_that_does_not_exist_is_rejected(tmp_path: Path) -> None:
    doc = _valid_doc()
    doc["entries"][0]["source_path"] = "nonexistent/file.json"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_source_path_that_does_exist_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "real.json").write_text("{}", encoding="utf-8")
    doc = _valid_doc()
    doc["entries"][0]["source_path"] = "real.json"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert result.ok, result.errors
    assert result.entries[0].source_path == "real.json"


def test_fabricated_contract_id_pointing_at_unrelated_real_agentreview_schema_is_rejected(
    tmp_path: Path,
) -> None:
    """Correction, found by an independent Codex review of an earlier
    draft: a typoed/fabricated contract_id whose source_path happens to
    point at an existing, unrelated schemas/agent-review/v2/ file
    previously passed (ok=True), because the loader only checked that the
    path existed, never that the file actually establishes that
    contract_id. Confirmed genuinely red against the pre-fix loader via a
    direct repro before this test was added."""
    (tmp_path / "schemas" / "agent-review" / "v2").mkdir(parents=True)
    (tmp_path / "schemas" / "agent-review" / "v2" / "agent-review.review-readiness.v2.schema.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "app" / "agent_review").mkdir(parents=True)
    (tmp_path / "app" / "agent_review" / "x.py").write_text(
        'schema_id: Literal["agent-review.review-readiness.v2"]', encoding="utf-8"
    )
    doc = _valid_doc()
    doc["entries"][0]["contract_id"] = "agent-review.does-not-exist.v2"
    doc["entries"][0]["source_path"] = "schemas/agent-review/v2/agent-review.review-readiness.v2.schema.json"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_swapped_source_paths_between_two_valid_contract_ids_are_rejected() -> None:
    """Correction, found by an independent Codex review: two contract_ids
    can each be individually real (both members of
    real_agent_review_schema_ids), yet have their source_path values
    SWAPPED between each other -- the earlier membership-only check
    passed this silently. Confirmed genuinely red against the pre-fix
    loader via a direct repro (swapping agent-review.run.v2's and
    agent-review.evidence-bundle.v2's source_path in the real manifest)
    before this fix was made."""
    doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry_a = next(e for e in doc["entries"] if e["contract_id"] == "agent-review.run.v2")
    entry_b = next(e for e in doc["entries"] if e["contract_id"] == "agent-review.evidence-bundle.v2")
    entry_a["source_path"], entry_b["source_path"] = entry_b["source_path"], entry_a["source_path"]

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok


def test_known_contract_id_pointed_at_unrelated_real_repo_file_is_rejected() -> None:
    """Correction, found by an independent Codex review: the canonical-
    filename check was gated on source_path's OWN prefix
    (`schemas/agent-review/v2/`), so pointing a KNOWN AgentReview
    contract_id at any other existing repo file entirely (outside that
    prefix, e.g. CHANGELOG.md) bypassed the check altogether and returned
    ok=True. Confirmed genuinely red against the pre-fix loader via a
    direct repro before this fix was made. The check must be driven by
    contract_id membership, which the caller cannot spoof by changing
    source_path."""
    doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["entries"] if e["contract_id"] == "agent-review.run.v2")
    entry["source_path"] = "CHANGELOG.md"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok


def test_known_contract_id_with_null_source_path_is_rejected() -> None:
    """A known AgentReview contract_id must always carry its real
    source_path -- source_path: null would silently drop the provenance
    binding for a contract this manifest claims to map."""
    doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["entries"] if e["contract_id"] == "agent-review.run.v2")
    entry["source_path"] = None

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok


def test_fabricated_agent_review_id_with_null_source_path_is_rejected() -> None:
    """Correction, found by an independent Codex review (confirmation
    round): a fabricated agent-review.* contract_id with source_path:
    null bypassed every earlier check, since those were all gated on some
    property of the SUPPLIED source_path (its prefix, its existence) --
    which a null source_path trivially has none of. Confirmed genuinely
    red against the pre-fix loader (three prior rounds' worth of checks,
    all still bypassable this way) via a direct repro before this fix was
    made. The fix gates purely on contract_id's own "agent-review."
    namespace prefix, checked unconditionally regardless of source_path."""
    doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    doc["entries"].append(
        {
            "contract_id": "agent-review.fabricated.v2",
            "owner": "x",
            "state": "reuse",
            "notes": "x",
            "ri_b0_role": "x",
            "source_path": None,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok


def test_loader_never_reads_referenced_schema_file_contents(tmp_path: Path) -> None:
    """The manifest references existing contracts by path/ID; it must never
    copy or depend on a referenced file's own content -- only that the file
    exists. Proven here by pointing source_path at a file with garbage,
    non-JSON content: load must still succeed, since only existence is
    checked, never a body read of the referenced artifact."""
    (tmp_path / "not-even-json.schema.json").write_text("this is not JSON at all {{{", encoding="utf-8")
    doc = _valid_doc()
    doc["entries"][0]["source_path"] = "not-even-json.schema.json"
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert result.ok, result.errors


def test_render_view_on_failed_load_raises_reuse_manifest_error() -> None:
    failed = ReuseManifestLoadResult(ok=False, errors=("boom",))
    with pytest.raises(ReuseManifestError):
        render_reuse_view(failed)


# ── post-merge conformance audit (master@18a0232): completeness, strict-int,
#    and duplicate-key rejection ──────────────────────────────────────────


def test_omitting_a_real_agentreview_entry_entirely_is_rejected() -> None:
    """Correction, found by an independent Codex review (post-merge
    conformance audit): every per-entry check only validated entries that
    were PRESENT; deleting an entire real agent-review.* entry (as opposed
    to corrupting one) passed silently, defeating this manifest's whole
    purpose of classifying every real AgentReview contract. Confirmed
    genuinely red against the merged master@18a0232 loader via a direct
    importlib-loaded reproduction before this fix was made."""
    doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    doc["entries"] = [e for e in doc["entries"] if e["contract_id"] != "agent-review.run.v2"]

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok


def test_boolean_schema_version_is_rejected_not_silently_accepted(tmp_path: Path) -> None:
    """Correction, found by an independent Codex review: `True == 1` in
    Python, so `schema_version: true` was silently accepted as version 1.
    Confirmed genuinely red against the merged master@18a0232 loader via a
    direct importlib-loaded reproduction before this fix was made."""
    doc = _valid_doc(schema_version=True)
    path = _write(tmp_path, doc)
    result = load_reuse_manifest(path, repo_root=tmp_path)
    assert not result.ok


def test_duplicate_top_level_json_key_is_rejected() -> None:
    """Correction, found by an independent Codex review: plain
    `json.loads` silently keeps the LAST value for a duplicate key.
    Reproduced with a manifest containing two conflicting `entries`
    arrays (a single-fixture-entry array, then the real 12-entry array) --
    the pre-fix loader silently used the last one and returned ok=True,
    never surfacing that the document contained two different, disagreeing
    entries blocks (e.g. from a bad merge). Confirmed genuinely red against
    the merged master@18a0232 loader via a direct reproduction before this
    fix was made."""
    real_doc = json.loads(REAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    fake_entries = [
        {
            "contract_id": "fixture.only.v1",
            "owner": "x",
            "state": "reuse",
            "notes": "x",
            "ri_b0_role": "x",
            "source_path": None,
        }
    ]
    raw = (
        '{"contract_id":"aiops.ri-b0a-2-reuse-manifest.v1","schema_version":1,'
        '"states":["reuse","reference","not_applicable","future_adapter"],'
        '"generated_from":"x",'
        f'"entries":{json.dumps(fake_entries)},'
        f'"entries":{json.dumps(real_doc["entries"])}}}'
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(raw, encoding="utf-8")
        result = load_reuse_manifest(path, repo_root=REPO_ROOT)
    assert not result.ok
