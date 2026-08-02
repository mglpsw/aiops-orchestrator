from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.payload_set_v2 import (
    PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2,
    PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2,
    PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2,
    PayloadSetBindingError,
    PayloadSetEntryV2,
    PayloadSetV2,
    bind_payload_set_to_manifest_v2,
    compute_payload_set_sha256_v2,
    verify_payload_set_sha256_v2,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2


# -- fixture helpers, matching the pattern already used by test_run_fragment_coverage_v2.py --


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 105,
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


def _hunk(path: str, *, index: int = 0, start: int = 1, end: int = 10) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=index,
        old_start=start,
        old_end=end,
        new_start=start,
        new_end=end,
        diff_sha256=hashlib.sha256(f"{path}:{index}:{start}:{end}".encode()).hexdigest(),
        diff_chars=100,
        must_review=True,
    )


def _build_manifest(
    hunks: list[HunkInputV2],
    *,
    expected_files: list[str],
    max_chunks: int = 10,
    max_lines_per_chunk: int = 100,
) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        hunks,
        semantic_group="primary_backend_logic",
        max_lines_per_chunk=max_lines_per_chunk,
        max_chunks=max_chunks,
    )
    assert outcome.state == "planned", "test setup requires a planned outcome"

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files,
        "must_review_files": [],
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": max_chunks,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _payload_sha256(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _payload_set_material(*, run_id: str, manifest_hash: str, entries: list[PayloadSetEntryV2]) -> dict[str, object]:
    return {
        "schema_id": "agent-review.payload-set.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payload-set-v2",
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "payloads": entries,
    }


def _payload_set(*, run_id: str, manifest_hash: str, entries: list[PayloadSetEntryV2]) -> PayloadSetV2:
    material = _payload_set_material(run_id=run_id, manifest_hash=manifest_hash, entries=entries)
    sha = compute_payload_set_sha256_v2({**material, "payloads": [entry.model_dump(mode="json") for entry in entries]})
    return PayloadSetV2(**material, payload_set_sha256=sha)


def _single_chunk_manifest() -> ManifestV2:
    return _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])


def _two_chunk_manifest() -> ManifestV2:
    return _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")],
        expected_files=["app/a.py", "app/b.py"],
        max_chunks=2,
        max_lines_per_chunk=10,
    )


# -- PayloadSetEntryV2 / PayloadSetMaterialV2 internal validity ---------------


def test_material_requires_at_least_one_payload_entry() -> None:
    with pytest.raises(ValidationError):
        _payload_set_material_model([])


def _payload_set_material_model(entries: list[PayloadSetEntryV2]):
    from app.agent_review.payload_set_v2 import PayloadSetMaterialV2

    return PayloadSetMaterialV2.model_validate(
        _payload_set_material(run_id="a" * 64, manifest_hash="b" * 64, entries=entries)
    )


def test_material_rejects_duplicate_chunk_ids() -> None:
    entry_a = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    entry_b = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("b"))
    with pytest.raises(ValidationError):
        _payload_set_material_model([entry_a, entry_b])


def test_payload_set_rejects_a_wrong_payload_set_sha256() -> None:
    entry = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    material = _payload_set_material(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry])
    with pytest.raises(ValidationError):
        PayloadSetV2(**material, payload_set_sha256="0" * 64)


def test_payload_set_sha256_is_independent_of_entry_order() -> None:
    entry_a = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    entry_b = PayloadSetEntryV2(chunk_id="chunk-1", payload_sha256=_payload_sha256("b"))
    forward = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry_a, entry_b])
    backward = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry_b, entry_a])
    assert forward.payload_set_sha256 == backward.payload_set_sha256


def test_payload_set_sha256_is_deterministic() -> None:
    entry = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    first = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry])
    second = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry])
    assert first.payload_set_sha256 == second.payload_set_sha256


def test_payload_set_sha256_changes_when_a_payload_sha256_changes() -> None:
    entry_a = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    entry_b = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("b"))
    first = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry_a])
    second = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry_b])
    assert first.payload_set_sha256 != second.payload_set_sha256


# -- verify_payload_set_sha256_v2 ---------------------------------------------


def test_verify_accepts_a_correctly_constructed_payload_set() -> None:
    entry = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry])
    verify_payload_set_sha256_v2(payload_set)  # must not raise


def test_verify_rejects_a_payload_set_that_bypassed_construction_validation() -> None:
    """Mirrors contracts_v2's test_model_copy_cannot_bypass_payload_or_response_hash_verification:
    model_copy bypasses model_validator, so this is the one path that can
    produce an in-memory object whose payload_set_sha256 no longer matches
    its own material -- exactly what verify_payload_set_sha256_v2 exists to
    catch on read, not just at construction."""

    entry = PayloadSetEntryV2(chunk_id="chunk-0", payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id="a" * 64, manifest_hash="b" * 64, entries=[entry])
    tampered = payload_set.model_copy(update={"payload_set_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        verify_payload_set_sha256_v2(tampered)


# -- bind_payload_set_to_manifest_v2 ------------------------------------------


def test_bind_accepts_a_payload_set_matching_its_manifest() -> None:
    manifest = _single_chunk_manifest()
    (chunk,) = manifest.chunks
    entry = PayloadSetEntryV2(chunk_id=chunk.chunk_id, payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, entries=[entry])
    bind_payload_set_to_manifest_v2(payload_set, manifest)  # must not raise


def test_bind_rejects_a_run_id_mismatch() -> None:
    manifest = _single_chunk_manifest()
    (chunk,) = manifest.chunks
    entry = PayloadSetEntryV2(chunk_id=chunk.chunk_id, payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id="9" * 64, manifest_hash=manifest.identity.manifest_hash, entries=[entry])
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_manifest_v2(payload_set, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2


def test_bind_rejects_a_manifest_hash_mismatch() -> None:
    manifest = _single_chunk_manifest()
    (chunk,) = manifest.chunks
    entry = PayloadSetEntryV2(chunk_id=chunk.chunk_id, payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id=manifest.run_id, manifest_hash="9" * 64, entries=[entry])
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_manifest_v2(payload_set, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2


def test_bind_rejects_a_payload_set_missing_a_manifest_chunk() -> None:
    manifest = _two_chunk_manifest()
    assert len(manifest.chunks) == 2
    only_first = manifest.chunks[0]
    entry = PayloadSetEntryV2(chunk_id=only_first.chunk_id, payload_sha256=_payload_sha256("a"))
    payload_set = _payload_set(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, entries=[entry])
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_manifest_v2(payload_set, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2


def test_bind_rejects_a_payload_set_with_an_extra_unknown_chunk() -> None:
    manifest = _single_chunk_manifest()
    (chunk,) = manifest.chunks
    entry = PayloadSetEntryV2(chunk_id=chunk.chunk_id, payload_sha256=_payload_sha256("a"))
    extra = PayloadSetEntryV2(chunk_id="chunk-does-not-exist", payload_sha256=_payload_sha256("b"))
    payload_set = _payload_set(
        run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, entries=[entry, extra]
    )
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_manifest_v2(payload_set, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2


def test_bind_accepts_a_payload_set_matching_a_two_chunk_manifest_exactly() -> None:
    manifest = _two_chunk_manifest()
    entries = [
        PayloadSetEntryV2(chunk_id=chunk.chunk_id, payload_sha256=_payload_sha256(chunk.chunk_id))
        for chunk in manifest.chunks
    ]
    payload_set = _payload_set(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, entries=entries)
    bind_payload_set_to_manifest_v2(payload_set, manifest)  # must not raise
