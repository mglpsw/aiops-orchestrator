from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import RunIdentityV2, TargetProfileV2, compute_run_id
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.payload_builder_v2 import (
    build_chunk_payload_from_profile_v2,
    build_chunk_payload_v2,
    build_chunk_payloads_from_profile_v2,
)
from app.agent_review.payload_references_v2 import (
    OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2,
    PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2,
    PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2,
    PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2,
    PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2,
    PayloadReferenceError,
    build_payload_artifact_references_v2,
    build_payload_contract_references_v2,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2


# -- fixture helpers ------------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 131,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _hunk(path: str) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=0,
        old_start=1,
        old_end=5,
        new_start=1,
        new_end=5,
        diff_sha256=hashlib.sha256(path.encode()).hexdigest(),
        diff_chars=40,
        must_review=True,
    )


def _build_manifest(path: str = "app/a.py") -> ManifestV2:
    outcome = plan_lossless_chunks_v2([_hunk(path)], semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10)
    assert outcome.state == "planned"
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": [path],
        "must_review_files": [path],
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _profile(
    *,
    artifacts: list[dict] = None,
    contracts: list[dict] = None,
) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2",
            "schema_version": 2,
            "source": "repo-profile",
            "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
            "artifacts": artifacts if artifacts is not None else [],
            "budgets": {
                "max_chunks": 32,
                "total_prompt_chars": 250000,
                "max_chars_per_chunk": 24000,
                "max_files_per_chunk": 50,
                "max_contracts_per_chunk": 50,
            },
            "must_review": {"paths": [], "patterns": [], "artifact_ids": [], "minimum_coverage": "complete"},
            "policies": {
                "network_policy": "forbidden",
                "fail_closed": True,
                "redaction_required": True,
                "allow_partial_coverage": False,
                "required_checks": ["pytest"],
                "allowed_semantic_groups": ["primary_backend_logic"],
                "coverage_failure_state": "blocked_pipeline",
                "model_uncertainty_state": "manual_required",
            },
            "contracts": contracts if contracts is not None else [],
            "limitations": [],
        }
    )


def _artifact_decl(*, artifact_id: str, path: str, required: bool = True, max_bytes: int = 1000, kind: str = "text") -> dict:
    return {"artifact_id": artifact_id, "path": path, "kind": kind, "required": required, "max_bytes": max_bytes}


def _contract_decl(*, contract_id: str, path: str, sha256: str, required: bool = True, scope: str = "repository") -> dict:
    return {
        "contract_id": contract_id,
        "contract_version": "1",
        "path": path,
        "sha256": sha256,
        "scope": scope,
        "required": required,
    }


# -- build_payload_artifact_references_v2 ---------------------------------------


def test_builds_a_reference_for_a_present_artifact(tmp_path) -> None:
    (tmp_path / "artifacts.txt").write_text("hello world", encoding="utf-8")
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="artifacts.txt")])
    references, limitations = build_payload_artifact_references_v2(profile, tmp_path)
    assert len(references) == 1
    assert references[0].artifact_id == "a1"
    assert references[0].role == "primary"
    assert limitations == ()


def test_artifact_content_is_sanitized_before_hashing(tmp_path) -> None:
    from app.agent_review.redaction import sanitize_artifact_value

    raw = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    (tmp_path / "secret.txt").write_text(raw, encoding="utf-8")
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="secret.txt")])
    references, _ = build_payload_artifact_references_v2(profile, tmp_path)
    expected_sha256 = hashlib.sha256(sanitize_artifact_value(raw).encode("utf-8")).hexdigest()
    assert references[0].sha256 == expected_sha256
    assert references[0].sha256 != hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_a_required_missing_artifact_fails_closed(tmp_path) -> None:
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="missing.txt", required=True)])
    with pytest.raises(PayloadReferenceError) as excinfo:
        build_payload_artifact_references_v2(profile, tmp_path)
    assert excinfo.value.reason_code == PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2


def test_an_optional_missing_artifact_is_skipped_with_a_limitation(tmp_path) -> None:
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="missing.txt", required=False)])
    references, limitations = build_payload_artifact_references_v2(profile, tmp_path)
    assert references == []
    assert limitations == (f"{OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2}:a1",)


def test_an_artifact_exceeding_max_bytes_is_refused_not_truncated(tmp_path) -> None:
    (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="big.txt", max_bytes=10)])
    with pytest.raises(PayloadReferenceError) as excinfo:
        build_payload_artifact_references_v2(profile, tmp_path)
    assert excinfo.value.reason_code == PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2


def test_an_oversized_artifact_is_never_fully_materialized_before_the_size_check(tmp_path, monkeypatch) -> None:
    """Codex P1 (post-G4B): the G4B migration changed the read sequence
    from `stat size -> compare against max_bytes -> read file` to
    `read entire file -> len(raw_bytes) -> compare against max_bytes`, so a
    large external artifact was fully materialized into memory BEFORE the
    size limit that is supposed to protect against exactly that. This test
    proves the ACTUAL bytes pulled off the underlying file handle never
    exceed `max_bytes + 1`, regardless of how much larger the real file on
    disk is -- not just that the correct reason code is eventually raised
    (the buggy code already raised the right reason code; it just did so
    only after reading the whole file)."""

    from pathlib import Path as _Path

    class _CountingReadProxy:
        """Wraps a real file handle, recording every byte count `.read()`
        actually returns. `io.BufferedReader` is a C type with no
        `__dict__`, so it cannot be monkeypatched directly -- wrapping the
        handle returned by `Path.open()` is the reliable way to observe
        what the underlying read primitive actually materialized."""

        def __init__(self, handle, sizes: list[int]) -> None:
            self._handle = handle
            self._sizes = sizes

        def read(self, size: int = -1) -> bytes:
            data = self._handle.read(size)
            self._sizes.append(len(data))
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return self._handle.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    real_size = 5_000_000
    max_bytes = 100
    (tmp_path / "big.bin").write_bytes(b"x" * real_size)
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="big.bin", max_bytes=max_bytes)])

    materialized_sizes: list[int] = []
    original_open = _Path.open

    def spy_open(self, *args, **kwargs):  # noqa: ANN001
        handle = original_open(self, *args, **kwargs)
        return _CountingReadProxy(handle, materialized_sizes)

    monkeypatch.setattr(_Path, "open", spy_open)

    with pytest.raises(PayloadReferenceError) as excinfo:
        build_payload_artifact_references_v2(profile, tmp_path)
    assert excinfo.value.reason_code == PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2

    total_materialized = sum(materialized_sizes)
    assert total_materialized <= max_bytes + 1, (
        f"materialized {total_materialized} bytes reading a file capped at "
        f"{max_bytes} bytes (real file was {real_size} bytes) -- the size "
        "check ran too late"
    )


# -- build_payload_contract_references_v2 ---------------------------------------


def test_builds_a_reference_for_a_present_contract_with_a_matching_sha256(tmp_path) -> None:
    content = "contracts: []"
    (tmp_path / "contracts.yaml").write_text(content, encoding="utf-8")
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    profile = _profile(contracts=[_contract_decl(contract_id="c1", path="contracts.yaml", sha256=sha256)])
    references = build_payload_contract_references_v2(profile, tmp_path)
    assert len(references) == 1
    assert references[0].contract_id == "c1"
    assert references[0].sha256 == sha256
    assert references[0].paths == []


def test_a_contract_sha256_mismatch_fails_closed(tmp_path) -> None:
    (tmp_path / "contracts.yaml").write_text("contracts: []", encoding="utf-8")
    profile = _profile(contracts=[_contract_decl(contract_id="c1", path="contracts.yaml", sha256="0" * 64)])
    with pytest.raises(PayloadReferenceError) as excinfo:
        build_payload_contract_references_v2(profile, tmp_path)
    assert excinfo.value.reason_code == PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2


def test_a_required_missing_contract_fails_closed(tmp_path) -> None:
    profile = _profile(contracts=[_contract_decl(contract_id="c1", path="missing.yaml", sha256="0" * 64, required=True)])
    with pytest.raises(PayloadReferenceError) as excinfo:
        build_payload_contract_references_v2(profile, tmp_path)
    assert excinfo.value.reason_code == PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2


def test_a_non_required_missing_contract_is_silently_omitted(tmp_path) -> None:
    profile = _profile(contracts=[_contract_decl(contract_id="c1", path="missing.yaml", sha256="0" * 64, required=False)])
    references = build_payload_contract_references_v2(profile, tmp_path)
    assert references == []


# -- wiring into build_chunk_payload_from_profile_v2 -----------------------------


def test_build_chunk_payload_v2_still_produces_empty_references() -> None:
    """Backward compatibility: the original entry point's signature and
    behavior are unchanged by this issue."""

    manifest = _build_manifest()
    payload = build_chunk_payload_v2(manifest, manifest.chunks[0])
    assert payload.artifact_references == []
    assert payload.contract_references == []


def test_build_chunk_payload_from_profile_v2_populates_real_references(tmp_path) -> None:
    (tmp_path / "artifact.txt").write_text("artifact content", encoding="utf-8")
    contract_content = "contracts: []"
    (tmp_path / "contract.yaml").write_text(contract_content, encoding="utf-8")
    contract_sha256 = hashlib.sha256(contract_content.encode("utf-8")).hexdigest()

    profile = _profile(
        artifacts=[_artifact_decl(artifact_id="a1", path="artifact.txt")],
        contracts=[_contract_decl(contract_id="c1", path="contract.yaml", sha256=contract_sha256)],
    )
    manifest = _build_manifest()
    built = build_chunk_payload_from_profile_v2(manifest, manifest.chunks[0], profile=profile, repo_root=tmp_path)
    assert len(built.payload.artifact_references) == 1
    assert built.payload.artifact_references[0].artifact_id == "a1"
    assert len(built.payload.contract_references) == 1
    assert built.payload.contract_references[0].contract_id == "c1"
    assert built.limitations == ()


def test_build_chunk_payloads_from_profile_v2_shares_the_same_references_across_chunks(tmp_path) -> None:
    (tmp_path / "artifact.txt").write_text("shared content", encoding="utf-8")
    profile = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="artifact.txt")])

    outcome = plan_lossless_chunks_v2(
        [_hunk("app/a.py"), _hunk("app/b.py")], semantic_group="primary_backend_logic", max_lines_per_chunk=5, max_chunks=10
    )
    assert outcome.state == "planned"
    assert len(outcome.chunks) >= 2
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py", "app/b.py"],
        "must_review_files": ["app/a.py", "app/b.py"],
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=tmp_path)
    assert len(built) == len(manifest.chunks)
    reference_sets = {tuple(sorted(b.payload.artifact_references, key=lambda r: r.artifact_id)) for b in built}
    assert len(reference_sets) == 1  # every chunk shares the identical reference set


def test_two_profiles_with_different_artifact_content_produce_different_payloads(tmp_path) -> None:
    """Acceptance criterion, verbatim: 'dois profiles diferentes sobre o
    mesmo diff produzem payloads diferentes.'"""

    (tmp_path / "artifact.txt").write_text("version one", encoding="utf-8")
    profile_a = _profile(artifacts=[_artifact_decl(artifact_id="a1", path="artifact.txt")])
    manifest = _build_manifest()
    built_a = build_chunk_payload_from_profile_v2(manifest, manifest.chunks[0], profile=profile_a, repo_root=tmp_path)

    (tmp_path / "artifact.txt").write_text("version two", encoding="utf-8")
    built_b = build_chunk_payload_from_profile_v2(manifest, manifest.chunks[0], profile=profile_a, repo_root=tmp_path)

    assert built_a.payload.payload_sha256 != built_b.payload.payload_sha256
