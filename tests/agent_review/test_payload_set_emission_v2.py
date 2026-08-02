from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
from app.agent_review.payload_set_emission_v2 import (
    PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2,
    PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2,
    PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2,
    PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2,
    PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2,
    bind_payload_set_to_payloads_v2,
    emit_payload_set_v2,
)
from app.agent_review.payload_set_v2 import (
    PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2,
    PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2,
    PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2,
    PayloadSetBindingError,
    PayloadSetEntryV2,
    compute_payload_set_sha256_v2,
    verify_payload_set_sha256_v2,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2


# -- fixture helpers ------------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 132,
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


def _build_manifest(paths: list[str], *, max_chunks: int = 10, max_lines_per_chunk: int = 5) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        [_hunk(path) for path in paths],
        semantic_group="primary_backend_logic",
        max_lines_per_chunk=max_lines_per_chunk,
        max_chunks=max_chunks,
    )
    assert outcome.state == "planned"
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": paths,
        "must_review_files": paths,
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": max_chunks,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _real_payloads(manifest: ManifestV2) -> list:
    return [built.payload for built in build_chunk_payloads_v2(manifest)]


# -- emit_payload_set_v2 --------------------------------------------------------


def test_emits_a_valid_payload_set_from_a_real_manifest_and_payloads() -> None:
    manifest = _build_manifest(["app/a.py", "app/b.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    assert payload_set.run_id == manifest.run_id
    assert payload_set.manifest_hash == manifest.identity.manifest_hash
    assert {entry.chunk_id for entry in payload_set.payloads} == {chunk.chunk_id for chunk in manifest.chunks}
    verify_payload_set_sha256_v2(payload_set)  # must not raise


def test_emitted_payload_set_is_byte_reproducible_and_order_independent() -> None:
    manifest = _build_manifest(["app/a.py", "app/b.py", "app/c.py"], max_lines_per_chunk=5, max_chunks=10)
    payloads = _real_payloads(manifest)
    forward = emit_payload_set_v2(manifest, payloads)
    backward = emit_payload_set_v2(manifest, list(reversed(payloads)))
    assert forward.payload_set_sha256 == backward.payload_set_sha256


def test_two_different_manifests_produce_different_payload_sets() -> None:
    manifest_a = _build_manifest(["app/a.py"])
    manifest_b = _build_manifest(["app/b.py"])
    payload_set_a = emit_payload_set_v2(manifest_a, _real_payloads(manifest_a))
    payload_set_b = emit_payload_set_v2(manifest_b, _real_payloads(manifest_b))
    assert payload_set_a.payload_set_sha256 != payload_set_b.payload_set_sha256


# -- bind_payload_set_to_payloads_v2: one regression per cross-validation clause -


def test_bind_rejects_a_run_id_mismatch() -> None:
    manifest = _build_manifest(["app/a.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    tampered = payload_set.model_copy(update={"run_id": "9" * 64})
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(tampered, payloads, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2


def test_bind_rejects_a_manifest_hash_mismatch() -> None:
    manifest = _build_manifest(["app/a.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    tampered = payload_set.model_copy(update={"manifest_hash": "9" * 64})
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(tampered, payloads, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2


def test_bind_rejects_an_entry_extra_beyond_the_manifests_chunks() -> None:
    manifest = _build_manifest(["app/a.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    extra_entry = PayloadSetEntryV2(chunk_id="chunk-does-not-exist", payload_sha256="e" * 64)
    tampered_material = {**payload_set.model_dump(exclude={"payload_set_sha256"}), "payloads": [*payload_set.payloads, extra_entry]}
    sha256 = compute_payload_set_sha256_v2({**tampered_material, "payloads": [e.model_dump(mode="json") for e in tampered_material["payloads"]]})
    from app.agent_review.payload_set_v2 import PayloadSetV2

    tampered = PayloadSetV2(**tampered_material, payload_set_sha256=sha256)
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(tampered, payloads, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2


def test_bind_rejects_an_entry_with_no_matching_payload_supplied() -> None:
    manifest = _build_manifest(["app/a.py", "app/b.py"], max_lines_per_chunk=5, max_chunks=10)
    payloads = _real_payloads(manifest)
    assert len(payloads) >= 2
    payload_set = emit_payload_set_v2(manifest, payloads)
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(payload_set, payloads[:-1], manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2


def test_bind_rejects_an_extra_unattested_payload() -> None:
    manifest = _build_manifest(["app/a.py", "app/b.py"], max_lines_per_chunk=5, max_chunks=10)
    payloads = _real_payloads(manifest)
    assert len(payloads) >= 2
    single_entry_manifest = _build_manifest(["app/a.py"])
    single_payloads = _real_payloads(single_entry_manifest)
    payload_set = emit_payload_set_v2(single_entry_manifest, single_payloads)
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(payload_set, payloads, single_entry_manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2


def test_bind_rejects_a_tampered_payload_caught_by_verify_payload_sha256_v2() -> None:
    manifest = _build_manifest(["app/a.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    tampered_payload = payloads[0].model_copy(update={"payload_sha256": "9" * 64})
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(payload_set, [tampered_payload], manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2


def test_bind_rejects_an_entry_payload_sha256_that_does_not_match_the_real_payload() -> None:
    manifest = _build_manifest(["app/a.py"])
    payloads = _real_payloads(manifest)
    payload_set = emit_payload_set_v2(manifest, payloads)
    tampered_entry = PayloadSetEntryV2(chunk_id=payload_set.payloads[0].chunk_id, payload_sha256="e" * 64)
    tampered_material = {**payload_set.model_dump(exclude={"payload_set_sha256"}), "payloads": [tampered_entry]}
    sha256 = compute_payload_set_sha256_v2({**tampered_material, "payloads": [tampered_entry.model_dump(mode="json")]})
    from app.agent_review.payload_set_v2 import PayloadSetV2

    tampered_set = PayloadSetV2(**tampered_material, payload_set_sha256=sha256)
    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(tampered_set, payloads, manifest)
    assert excinfo.value.reason_code == PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2


def test_bind_rejects_a_foreign_run_payload_smuggled_via_a_coincidental_chunk_id_collision() -> None:
    """The one genuinely reachable "incoherent" clause (see the module
    docstring): plan_lossless_chunks_v2's default chunk_id numbering
    ("chunk-0000", ...) is not globally unique across different runs --
    two independent single-chunk manifests built the same way produce the
    IDENTICAL chunk_id, confirmed directly below. A payload that is
    genuinely valid on its own (verify_payload_sha256_v2 passes) but was
    built for manifest_b can therefore be smuggled into manifest_a's
    payload set via that coincidental collision, with the entry's
    payload_sha256 honestly declared as the foreign payload's real hash --
    every other clause (chunk-set match, tamper check, entry-hash match)
    passes. run_id incoherence is exactly what catches this."""

    manifest_a = _build_manifest(["app/a.py"])
    manifest_b = _build_manifest(["app/b.py"])
    assert manifest_a.chunks[0].chunk_id == manifest_b.chunks[0].chunk_id
    assert manifest_a.run_id != manifest_b.run_id

    foreign_payload = _real_payloads(manifest_b)[0]  # genuinely valid, but for manifest_b's run
    forged_entry = PayloadSetEntryV2(chunk_id=foreign_payload.chunk_id, payload_sha256=foreign_payload.payload_sha256)
    material = {
        "schema_id": "agent-review.payload-set.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payload-set-v2",
        "run_id": manifest_a.run_id,
        "manifest_hash": manifest_a.identity.manifest_hash,
        "payloads": [forged_entry],
    }
    sha256 = compute_payload_set_sha256_v2({**material, "payloads": [forged_entry.model_dump(mode="json")]})
    from app.agent_review.payload_set_v2 import PayloadSetV2

    forged_set = PayloadSetV2(**material, payload_set_sha256=sha256)

    with pytest.raises(PayloadSetBindingError) as excinfo:
        bind_payload_set_to_payloads_v2(forged_set, [foreign_payload], manifest_a)
    assert excinfo.value.reason_code == PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2
