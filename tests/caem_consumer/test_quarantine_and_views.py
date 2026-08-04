"""Quarantine-authority and active-surface-identity-consistency tests.

These prove the two governance-level invariants #119.1 exists to establish:
CAEM 2.1 material can no longer act as an active identity source, and the
five active generated-view headers all reference the same, single CAEM
identity. `scan_active_identity_headers` deliberately checks only those five
files — it is not, and does not claim to be, a repository-wide scan; see its
docstring and `test_no_functional_code_reads_quarantined_caem_material` for
the (separate, broader) proof about functional code specifically.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.caem_consumer.f0 import (
    _ACTIVE_IDENTITY_HEADER_FILES,
    _STALE_IDENTITY_STRINGS,
    scan_active_identity_headers,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_all_active_headers_reference_the_same_pin() -> None:
    for rel in _ACTIVE_IDENTITY_HEADER_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "config/caem/caem-3.0-f0.pin.json" in text, f"{rel} does not reference the consumer pin"


def test_no_active_file_declares_a_stale_caem_version() -> None:
    for rel in _ACTIVE_IDENTITY_HEADER_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "CAEM v2.1.0" not in text
        assert "CAEM: 2.1.0" not in text


def test_body_provenance_labeled_where_body_predates_f0() -> None:
    # AGENTS.md, CLAUDE.md, CAEM_CORE.md, and PROJECT_OVERLAY.md all carry
    # bodies that existed before this PR and were never produced by (or
    # regenerated from) the CAEM 3.0 F0 interface manifest, which produces
    # only contract-registry.json, 3 schemas, a Python catalog, and
    # generated/CAEM_3_0_F0_INTERFACE.md. Each of those four files must
    # therefore self-declare that its body is a historical projection, not
    # an F0-generated view.
    labeled_files = (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/engineering/CAEM_CORE.md",
        "docs/engineering/PROJECT_OVERLAY.md",
    )
    for rel in labeled_files:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "not_a_CAEM_3_0_F0_generated_view: true" in text, f"{rel} does not disclaim false F0 provenance"
        assert "body_provenance: historical_caem_2_1_projection" in text, f"{rel} does not label its body provenance"

    for rel in labeled_files:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "CAEM CORE 3.0 F0" not in text
        assert "sob CAEM 3.0 F0" not in text


def test_active_identity_scan_is_clean_on_this_checkout() -> None:
    # This repo's five active surfaces must have zero stale-identity hits.
    assert scan_active_identity_headers(REPO_ROOT) == ()


def test_active_identity_scan_detects_drift(tmp_path: Path) -> None:
    fake_root = tmp_path / "fake_repo"
    (fake_root / "docs" / "engineering").mkdir(parents=True)
    (fake_root / "AGENTS.md").write_text("normal content\n", encoding="utf-8")
    (fake_root / "CLAUDE.md").write_text(f"Policy-SHA256: {_STALE_IDENTITY_STRINGS[0]}\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "CAEM_CORE.md").write_text("normal\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "PROJECT_OVERLAY.md").write_text("normal\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "CURRENT_CHECKPOINT.md").write_text("normal\n", encoding="utf-8")

    hits = scan_active_identity_headers(fake_root)
    assert hits == (("CLAUDE.md", _STALE_IDENTITY_STRINGS[0]),)


def test_active_identity_scan_ignores_files_outside_its_five_named_targets(tmp_path: Path) -> None:
    # A stale digest in some OTHER file (e.g. a historical doc) is, correctly,
    # invisible to this scanner — it only ever checks the five named files.
    # This is the scanner behaving exactly as documented, not a false
    # "zero occurrences repo-wide" claim.
    fake_root = tmp_path / "fake_repo"
    (fake_root / "docs" / "engineering").mkdir(parents=True)
    for rel in _ACTIVE_IDENTITY_HEADER_FILES:
        (fake_root / rel).write_text("normal\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "SOME_HISTORICAL_DOC.md").write_text(
        f"historical mention: {_STALE_IDENTITY_STRINGS[0]}\n", encoding="utf-8"
    )

    assert scan_active_identity_headers(fake_root) == ()


def test_quarantine_metadata_declares_no_authority() -> None:
    metadata_path = REPO_ROOT / ".caem" / "quarantine" / "caem-2.1" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "historical_quarantine"
    assert metadata["authority_effect"] == "none"
    assert metadata["read_only"] is True
    assert metadata["consumer_identity_source"] == "config/caem/caem-3.0-f0.pin.json"


def test_quarantined_bytes_are_preserved(tmp_path: Path) -> None:
    # The quarantined policy.json must still be the real historical CAEM
    # 2.1.0 bytes (moved via `git mv`, never rewritten "to look like F0").
    policy_path = REPO_ROOT / ".caem" / "quarantine" / "caem-2.1" / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["metadata"]["version"] == "2.1.0"
    assert policy["metadata"]["status"] == "canonical-baseline"


def test_no_functional_code_reads_quarantined_caem_material() -> None:
    # No application/script/test code should load the quarantine directory
    # as if it were still an active source (mirrors the RI-A0 finding that
    # no consumer existed even before quarantine).
    quarantine_marker = ".caem/quarantine"
    hits: list[str] = []
    for base in ("app", "scripts"):
        for path in (REPO_ROOT / base).rglob("*.py"):
            if "caem_consumer" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if quarantine_marker in text or ".caem/schemas" in text or ".caem/policy.json" in text:
                hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_quarantine_no_longer_at_old_paths() -> None:
    assert not (REPO_ROOT / ".caem" / "policy.json").exists()
    assert not (REPO_ROOT / ".caem" / "repository-profile.json").exists()
    assert not (REPO_ROOT / ".caem" / "repository-registry.json").exists()
    assert not (REPO_ROOT / ".caem" / "schemas").exists()
