"""Tests for the #119.2 AgentReview/ProjectOps reuse manifest loader and view."""

from __future__ import annotations

import json
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
