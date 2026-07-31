from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestDegradationV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)


def _identity(**overrides: object) -> RunIdentityV2:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 84,
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
    return RunIdentityV2.model_validate(raw)


def _material_kwargs(
    *,
    fragments: list[FragmentV2],
    chunks: list[ManifestChunkV2],
    degradation_causes: list[ManifestDegradationV2] | None = None,
    expected_files: list[str] | None = None,
    must_review_files: list[str] | None = None,
    max_chunks: int = 10,
) -> dict[str, object]:
    return {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files or ["app/service.py"],
        "must_review_files": must_review_files if must_review_files is not None else ["app/service.py"],
        "fragments": fragments,
        "chunks": chunks,
        "max_chunks": max_chunks,
        "degradation_causes": degradation_causes or [],
    }


def _fragment(
    *, path: str = "app/service.py", start: int = 1, end: int = 10, required: bool = True
) -> FragmentV2:
    old_range = LineRangeV2(start=start, end=end)
    new_range = LineRangeV2(start=start, end=end)
    diff_sha256 = "e" * 64
    fragment_id = compute_fragment_id_v2(
        path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha256
    )
    return FragmentV2(
        fragment_id=fragment_id,
        path=path,
        old_range=old_range,
        new_range=new_range,
        hunk_indexes=[0],
        diff_chars=100,
        diff_sha256=diff_sha256,
        coverage_required=required,
    )


def _manifest(
    *,
    identity: RunIdentityV2 | None = None,
    fragments: list[FragmentV2] | None = None,
    chunks: list[ManifestChunkV2] | None = None,
    degradation_causes: list[ManifestDegradationV2] | None = None,
    expected_files: list[str] | None = None,
    must_review_files: list[str] | None = None,
    max_chunks: int = 10,
) -> ManifestV2:
    """Builds a ManifestV2 the way a real caller must: material first, then
    its hash, then identity/run_id -- never a placeholder manifest_hash,
    since ManifestV2 now proves identity.manifest_hash matches the actual
    material hash (see the module docstring on the circularity this
    breaks)."""

    fragment = fragments[0] if fragments else _fragment()
    fragments = fragments if fragments is not None else [fragment]
    chunks = (
        chunks
        if chunks is not None
        else [
            ManifestChunkV2(
                chunk_id="chunk-0000",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment.fragment_id],
                payload_sha256=None,
            )
        ]
    )
    material_kwargs = _material_kwargs(
        fragments=fragments,
        chunks=chunks,
        degradation_causes=degradation_causes,
        expected_files=expected_files,
        must_review_files=must_review_files,
        max_chunks=max_chunks,
    )
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = identity or _identity(manifest_hash=manifest_hash)
    return ManifestV2(
        **material_kwargs,
        run_id=compute_run_id(identity),
        identity=identity,
    )


# -- fragment_id ---------------------------------------------------------


def test_fragment_id_is_the_documented_preimage() -> None:
    fragment = _fragment()
    recomputed = compute_fragment_id_v2(
        path=fragment.path,
        old_range=fragment.old_range,
        new_range=fragment.new_range,
        diff_sha256=fragment.diff_sha256,
    )
    assert fragment.fragment_id == recomputed


def test_fragment_rejects_a_stale_fragment_id() -> None:
    fragment = _fragment()
    with pytest.raises(ValidationError):
        FragmentV2.model_validate(
            {**fragment.model_dump(mode="json"), "fragment_id": "f" * 64}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "app/other.py"),
        ("diff_sha256", "f" * 64),
    ],
)
def test_fragment_id_changes_when_material_identity_changes(field: str, value: object) -> None:
    fragment = _fragment()
    other_path = value if field == "path" else fragment.path
    other_diff = value if field == "diff_sha256" else fragment.diff_sha256
    other_id = compute_fragment_id_v2(
        path=other_path,
        old_range=fragment.old_range,
        new_range=fragment.new_range,
        diff_sha256=other_diff,
    )
    assert other_id != fragment.fragment_id


def test_line_range_rejects_an_inverted_range() -> None:
    with pytest.raises(ValidationError):
        LineRangeV2(start=10, end=5)


# -- manifest structural invariants ---------------------------------------


def test_manifest_accepts_a_fully_covered_single_chunk() -> None:
    manifest = _manifest()
    assert manifest.chunks[0].fragment_ids == [manifest.fragments[0].fragment_id]


def test_manifest_rejects_a_run_id_not_matching_identity() -> None:
    identity = _identity()
    with pytest.raises(ValidationError):
        ManifestV2(
            schema_id="agent-review.manifest.v2",
            schema_version=2,
            source="aiops-review-plan-chunks-v2",
            run_id="0" * 64,
            identity=identity,
            expected_files=["app/service.py"],
            must_review_files=["app/service.py"],
            fragments=[_fragment()],
            chunks=[],
            max_chunks=10,
            degradation_causes=[],
        )


def test_manifest_rejects_a_fragment_path_outside_expected_files() -> None:
    fragment = _fragment(path="app/outside.py")
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], expected_files=["app/service.py"])


def test_manifest_rejects_must_review_files_not_in_expected_files() -> None:
    with pytest.raises(ValidationError):
        _manifest(must_review_files=["app/other.py"])


def test_manifest_rejects_a_chunk_referencing_an_unknown_fragment() -> None:
    fragment = _fragment()
    bogus_chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=["f" * 64],
        payload_sha256=None,
    )
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], chunks=[bogus_chunk])


def test_manifest_rejects_a_fragment_assigned_to_two_chunks() -> None:
    fragment = _fragment()
    chunk_a = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    chunk_b = ManifestChunkV2(
        chunk_id="chunk-0001",
        order_index=1,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], chunks=[chunk_a, chunk_b])


def test_manifest_rejects_an_unexplained_omission_of_a_required_fragment() -> None:
    """The core losslessness guard: a coverage_required fragment missing
    from every chunk, with no degradation cause, is a hard validation
    error -- never a silently accepted partial manifest."""

    fragment = _fragment(required=True)
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], chunks=[])


def test_manifest_accepts_an_omission_explained_by_a_degradation_cause() -> None:
    fragment = _fragment(required=True)
    cause = ManifestDegradationV2(
        reason_code="budget_exhausted",
        affected_fragment_ids=[fragment.fragment_id],
        detail="max_chunks too small",
    )
    manifest = _manifest(fragments=[fragment], chunks=[], degradation_causes=[cause])
    assert manifest.chunks == []


def test_manifest_rejects_a_degradation_cause_for_a_non_required_fragment() -> None:
    fragment = _fragment(required=False)
    cause = ManifestDegradationV2(
        reason_code="budget_exhausted",
        affected_fragment_ids=[fragment.fragment_id],
        detail="irrelevant",
    )
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], chunks=[], degradation_causes=[cause])


def test_manifest_accepts_an_unreferenced_non_required_fragment() -> None:
    """An auxiliary (non-must_review) fragment can be present in the
    manifest without being assigned to any chunk -- it was optional
    context, not required coverage. must_review_files is empty here since
    this fragment's path carries no must-review claim at all."""

    fragment = _fragment(required=False)
    manifest = _manifest(fragments=[fragment], chunks=[], must_review_files=[])
    assert manifest.fragments[0].fragment_id == fragment.fragment_id
    assert manifest.chunks == []


def test_manifest_rejects_chunk_count_exceeding_max_chunks() -> None:
    fragment = _fragment()
    chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    with pytest.raises(ValidationError):
        _manifest(fragments=[fragment], chunks=[chunk], max_chunks=0)


# -- manifest_hash reuse of contracts_v2 -----------------------------------


def test_manifest_hash_is_deterministic() -> None:
    manifest = _manifest()
    manifest_copy = ManifestV2.model_validate_json(manifest.model_dump_json())
    assert compute_manifest_hash_v2_for(manifest) == compute_manifest_hash_v2_for(manifest_copy)


def test_manifest_hash_changes_when_a_fragment_changes() -> None:
    manifest = _manifest()
    other_fragment = _fragment(path="app/service.py", start=1, end=20)
    other_chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[other_fragment.fragment_id],
        payload_sha256=None,
    )
    other_manifest = _manifest(fragments=[other_fragment], chunks=[other_chunk])
    assert compute_manifest_hash_v2_for(manifest) != compute_manifest_hash_v2_for(other_manifest)


def test_manifest_rejects_unknown_fields() -> None:
    manifest = _manifest()
    raw = manifest.model_dump(mode="json")
    raw["unexpected_field"] = True
    with pytest.raises(ValidationError):
        ManifestV2.model_validate(raw)


# -- manifest_hash / identity circularity (post-merge finding, PR #99 review) --


def test_manifest_accepts_identity_manifest_hash_matching_the_material() -> None:
    """The positive case: a correctly constructed manifest (material ->
    hash -> identity, in that order) validates cleanly."""

    manifest = _manifest()
    assert manifest.identity.manifest_hash == compute_manifest_hash_v2_for(manifest)


def test_manifest_rejects_a_stale_identity_manifest_hash() -> None:
    """The core circularity guard: identity.manifest_hash must equal the
    hash of THIS manifest's own content, not an arbitrary or stale value.
    A manifest built with a placeholder/incorrect manifest_hash (e.g. from
    a different fragment set) must be rejected, not silently accepted."""

    fragment = _fragment()
    chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    material_kwargs = _material_kwargs(fragments=[fragment], chunks=[chunk])
    # A manifest_hash that is well-formed (64 lowercase hex) but does not
    # match this material's actual canonical hash.
    stale_identity = _identity(manifest_hash="0" * 64)
    with pytest.raises(ValidationError):
        ManifestV2(
            **material_kwargs,
            run_id=compute_run_id(stale_identity),
            identity=stale_identity,
        )


def test_manifest_rejects_identity_manifest_hash_from_a_different_manifest() -> None:
    """A manifest_hash that is valid for a DIFFERENT (also real) manifest
    must still be rejected -- it is not merely "well-formed", it must be
    THIS manifest's own hash."""

    fragment_a = _fragment(start=1, end=10)
    chunk_a = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment_a.fragment_id],
        payload_sha256=None,
    )
    material_a = ManifestMaterialV2.model_validate(
        _material_kwargs(fragments=[fragment_a], chunks=[chunk_a])
    )
    hash_a = compute_manifest_hash_v2_for(material_a)

    fragment_b = _fragment(start=1, end=50)
    chunk_b = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment_b.fragment_id],
        payload_sha256=None,
    )
    material_b_kwargs = _material_kwargs(fragments=[fragment_b], chunks=[chunk_b])

    assert hash_a != compute_manifest_hash_v2_for(ManifestMaterialV2.model_validate(material_b_kwargs))

    identity_with_a_hash = _identity(manifest_hash=hash_a)
    with pytest.raises(ValidationError):
        ManifestV2(
            **material_b_kwargs,
            run_id=compute_run_id(identity_with_a_hash),
            identity=identity_with_a_hash,
        )


def test_compute_manifest_hash_v2_for_agrees_between_material_and_full_manifest() -> None:
    """compute_manifest_hash_v2_for must return the identical digest
    whether called on the bare ManifestMaterialV2 used to build identity,
    or on the resulting full ManifestV2 -- proving run_id/identity are
    excluded from the preimage either way, not just coincidentally equal."""

    fragment = _fragment()
    chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    material_kwargs = _material_kwargs(fragments=[fragment], chunks=[chunk])
    material = ManifestMaterialV2.model_validate(material_kwargs)
    material_hash = compute_manifest_hash_v2_for(material)

    identity = _identity(manifest_hash=material_hash)
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    assert compute_manifest_hash_v2_for(manifest) == material_hash


def test_manifest_hash_does_not_depend_on_run_id_or_identity_contents() -> None:
    """Two manifests with identical material but DIFFERENT identities
    (different base_sha, different manifest_hash-independent fields) must
    still hash to the same manifest_hash for that shared material -- proving
    identity/run_id are truly excluded from the preimage, not merely
    unused by coincidence in the other tests."""

    fragment = _fragment()
    chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[fragment.fragment_id],
        payload_sha256=None,
    )
    material_kwargs = _material_kwargs(fragments=[fragment], chunks=[chunk])
    material = ManifestMaterialV2.model_validate(material_kwargs)
    material_hash = compute_manifest_hash_v2_for(material)

    identity_1 = _identity(manifest_hash=material_hash, base_sha="1" * 40)
    identity_2 = _identity(manifest_hash=material_hash, base_sha="5" * 40)

    manifest_1 = ManifestV2(**material_kwargs, run_id=compute_run_id(identity_1), identity=identity_1)
    manifest_2 = ManifestV2(**material_kwargs, run_id=compute_run_id(identity_2), identity=identity_2)

    assert compute_manifest_hash_v2_for(manifest_1) == compute_manifest_hash_v2_for(manifest_2)
    assert manifest_1.run_id != manifest_2.run_id


# -- post-merge finding (P1, Codex review of PR #99) ------------------------


def test_manifest_rejects_a_must_review_path_with_no_required_fragment_at_all() -> None:
    """A must_review_files path that has NO fragments at all (not even a
    non-required one) must be rejected -- the must-review guarantee must
    not be satisfiable by simply never emitting a fragment for that path."""

    fragment = _fragment(path="app/service.py", required=True)
    with pytest.raises(ValidationError):
        _manifest(
            fragments=[fragment],
            chunks=[],
            expected_files=["app/service.py", "app/uncovered.py"],
            must_review_files=["app/service.py", "app/uncovered.py"],
            degradation_causes=[
                ManifestDegradationV2(
                    reason_code="budget_exhausted",
                    affected_fragment_ids=[fragment.fragment_id],
                    detail="max_chunks too small",
                )
            ],
        )


def test_manifest_rejects_a_must_review_path_covered_only_by_non_required_fragments() -> None:
    """The exact scenario from the Codex finding: a must_review_files path
    has fragments, but all of them are coverage_required=False. Nothing
    upstream of this validator connects must_review_files to the
    coverage_required flag, so this must be caught here."""

    optional_fragment = _fragment(path="app/service.py", required=False)
    chunk = ManifestChunkV2(
        chunk_id="chunk-0000",
        order_index=0,
        semantic_group="primary_backend_logic",
        fragment_ids=[optional_fragment.fragment_id],
        payload_sha256=None,
    )
    with pytest.raises(ValidationError):
        _manifest(
            fragments=[optional_fragment],
            chunks=[chunk],
            must_review_files=["app/service.py"],
        )
