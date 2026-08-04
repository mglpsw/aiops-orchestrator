"""Quarantine-authority and generated-view-consistency tests.

These prove the two governance-level invariants #119.1 exists to establish:
CAEM 2.1 material can no longer act as an active identity source, and every
active header in this repository references the same, single CAEM identity.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.caem_consumer.f0 import (
    _ACTIVE_IDENTITY_HEADER_FILES,
    _STALE_IDENTITY_STRINGS,
    scan_for_stale_caem_identity,
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


def test_generated_view_drift_is_detected(tmp_path: Path) -> None:
    # A clean repo checkout (this one) must have zero stale-identity hits.
    assert scan_for_stale_caem_identity(REPO_ROOT) == ()

    # Simulate drift: an active header re-declaring a legacy digest.
    fake_root = tmp_path / "fake_repo"
    (fake_root / "docs" / "engineering").mkdir(parents=True)
    (fake_root / "AGENTS.md").write_text("normal content\n", encoding="utf-8")
    (fake_root / "CLAUDE.md").write_text(f"Policy-SHA256: {_STALE_IDENTITY_STRINGS[0]}\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "CAEM_CORE.md").write_text("normal\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "PROJECT_OVERLAY.md").write_text("normal\n", encoding="utf-8")
    (fake_root / "docs" / "engineering" / "CURRENT_CHECKPOINT.md").write_text("normal\n", encoding="utf-8")

    hits = scan_for_stale_caem_identity(fake_root)
    assert hits == (("CLAUDE.md", _STALE_IDENTITY_STRINGS[0]),)


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
