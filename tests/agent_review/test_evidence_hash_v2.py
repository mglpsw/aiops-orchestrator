from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.evidence_hash_v2 import (
    EvidenceBundleV2,
    EvidenceReferenceV2,
    canonical_evidence_bundle_bytes_v2,
    compute_evidence_hash_v2,
)


# -- fixture helpers ------------------------------------------------------------


def _sha256(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _reference(*, reference_id: str, kind: str = "artifact", label: str | None = None) -> EvidenceReferenceV2:
    return EvidenceReferenceV2(
        reference_id=reference_id,
        kind=kind,
        sha256=_sha256(label or reference_id),
        detail=f"evidence reference {reference_id}",
    )


def _bundle(*, references: list[EvidenceReferenceV2]) -> EvidenceBundleV2:
    return EvidenceBundleV2(
        schema_id="agent-review.evidence-bundle.v2",
        schema_version=2,
        source="aiops-review-evidence-bundle-v2",
        references=references,
    )


# -- EvidenceReferenceV2 / EvidenceBundleV2 internal validity ------------------


def test_bundle_accepts_zero_references() -> None:
    bundle = _bundle(references=[])
    assert bundle.references == []


def test_bundle_rejects_duplicate_reference_id_within_the_same_kind() -> None:
    ref_a = _reference(reference_id="dup", kind="artifact")
    ref_b = _reference(reference_id="dup", kind="artifact", label="different-content")
    with pytest.raises(ValidationError):
        _bundle(references=[ref_a, ref_b])


def test_bundle_accepts_the_same_reference_id_across_different_kinds() -> None:
    ref_artifact = _reference(reference_id="shared-id", kind="artifact")
    ref_contract = _reference(reference_id="shared-id", kind="contract")
    bundle = _bundle(references=[ref_artifact, ref_contract])
    assert len(bundle.references) == 2


def test_bundle_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        EvidenceReferenceV2(reference_id="r1", kind="check", sha256=_sha256("r1"), detail="d")


def test_bundle_rejects_an_unknown_schema_id() -> None:
    with pytest.raises(ValidationError):
        EvidenceBundleV2(
            schema_id="agent-review.evidence-bundle.v1",
            schema_version=2,
            source="aiops-review-evidence-bundle-v2",
            references=[],
        )


# -- compute_evidence_hash_v2 ---------------------------------------------------


def test_evidence_hash_is_deterministic() -> None:
    bundle = _bundle(references=[_reference(reference_id="a1"), _reference(reference_id="c1", kind="contract")])
    first = compute_evidence_hash_v2(bundle)
    second = compute_evidence_hash_v2(bundle)
    assert first == second


def test_evidence_hash_is_independent_of_reference_order() -> None:
    ref_a = _reference(reference_id="a1")
    ref_b = _reference(reference_id="c1", kind="contract")
    forward = _bundle(references=[ref_a, ref_b])
    backward = _bundle(references=[ref_b, ref_a])
    assert compute_evidence_hash_v2(forward) == compute_evidence_hash_v2(backward)


def test_evidence_hash_changes_when_a_reference_sha256_changes() -> None:
    bundle_a = _bundle(references=[_reference(reference_id="a1", label="content-a")])
    bundle_b = _bundle(references=[_reference(reference_id="a1", label="content-b")])
    assert compute_evidence_hash_v2(bundle_a) != compute_evidence_hash_v2(bundle_b)


def test_evidence_hash_changes_when_a_reference_is_added() -> None:
    bundle_a = _bundle(references=[_reference(reference_id="a1")])
    bundle_b = _bundle(references=[_reference(reference_id="a1"), _reference(reference_id="a2")])
    assert compute_evidence_hash_v2(bundle_a) != compute_evidence_hash_v2(bundle_b)


def test_evidence_hash_of_an_empty_bundle_is_stable() -> None:
    bundle = _bundle(references=[])
    assert compute_evidence_hash_v2(bundle) == compute_evidence_hash_v2(_bundle(references=[]))


def test_evidence_hash_accepts_a_plain_mapping_equivalent_to_the_model() -> None:
    bundle = _bundle(references=[_reference(reference_id="a1")])
    mapping = bundle.model_dump(mode="json")
    assert compute_evidence_hash_v2(mapping) == compute_evidence_hash_v2(bundle)


def test_evidence_hash_rejects_a_non_model_non_mapping_value() -> None:
    with pytest.raises(TypeError):
        canonical_evidence_bundle_bytes_v2(object())  # type: ignore[arg-type]
