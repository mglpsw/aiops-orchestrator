from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.agent_review.authoritative_diff_identity_v2 import (
    DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2,
    DIFF_BINDING_MANIFEST_IDENTITY_MISMATCH_REASON_V2,
    ManifestDiffBindingError,
    acquire_authoritative_diff_with_identity_v2,
    bind_manifest_to_diff_identity_v2,
    compute_authoritative_diff_sha256_v2,
    verify_manifest_diff_binding_v2,
)
from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from tests.agent_review.test_review_transport_v2 import (
    _commit_all,
    _grouping_policy,
    _init_repo,
    _profile,
)


def _manifest_and_exact_diff(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    path = repo / "app.py"
    path.write_text("alpha = 1\nkeep = 2\nomega = 3\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    path.write_text(
        "alpha = 10\nkeep = 2\nomega = 30\nextra = 40\n",
        encoding="utf-8",
    )
    head_sha = _commit_all(repo, "head")

    file_diffs, _diff_text, acquired_identity = acquire_authoritative_diff_with_identity_v2(
        repo,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=_profile(),
        grouping_policy=_grouping_policy(),
        repo="example/repo",
        pr_number=1,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=head_sha,
        toolrepo_sha="b" * 40,
        evidence_hash="c" * 64,
        max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    assert outcome.manifest is not None
    diff_text = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    return outcome.manifest, acquired_identity, diff_text


def test_acquisition_hash_matches_independent_hashlib_oracle(tmp_path: Path) -> None:
    _, acquired_identity, diff_text = _manifest_and_exact_diff(tmp_path)

    # Expected and observed deliberately come from different implementations.
    independent = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    assert acquired_identity.diff_sha256 == independent
    assert compute_authoritative_diff_sha256_v2(diff_text) == independent


def test_manifest_binding_accepts_the_exact_acquired_diff(tmp_path: Path) -> None:
    manifest, acquired_identity, diff_text = _manifest_and_exact_diff(tmp_path)
    binding = bind_manifest_to_diff_identity_v2(manifest, acquired_identity)

    verified = verify_manifest_diff_binding_v2(
        binding,
        manifest=manifest,
        diff_text=diff_text,
    )

    assert verified.repository == manifest.identity.repo
    assert verified.base_sha == manifest.identity.base_sha
    assert verified.head_sha == manifest.identity.head_sha
    assert verified.tested_merge_sha == manifest.identity.tested_merge_sha
    assert verified.diff_sha256 == hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    assert binding.run_id == manifest.run_id
    assert binding.manifest_hash == manifest.identity.manifest_hash


def test_truncated_diff_with_same_apparent_path_is_rejected_before_scope(tmp_path: Path) -> None:
    manifest, acquired_identity, diff_text = _manifest_and_exact_diff(tmp_path)
    binding = bind_manifest_to_diff_identity_v2(manifest, acquired_identity)

    # Keep the exact same file header/path but remove bytes from the tail.  A
    # path-set comparison still sees app.py on both sides; byte identity does not.
    lines = diff_text.splitlines(keepends=True)
    assert any("app.py" in line for line in lines)
    truncated = "".join(lines[:-1])
    assert "app.py" in truncated
    assert hashlib.sha256(truncated.encode("utf-8")).hexdigest() != binding.authoritative_diff_sha256

    scope_called = False

    def would_classify_scope() -> None:
        nonlocal scope_called
        scope_called = True

    with pytest.raises(ManifestDiffBindingError) as excinfo:
        verify_manifest_diff_binding_v2(
            binding,
            manifest=manifest,
            diff_text=truncated,
        )
        would_classify_scope()

    assert excinfo.value.reason_code == DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2
    assert str(excinfo.value) == DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2
    assert scope_called is False


def test_binding_cannot_be_replayed_against_another_run_identity(tmp_path: Path) -> None:
    manifest, acquired_identity, diff_text = _manifest_and_exact_diff(tmp_path)
    binding = bind_manifest_to_diff_identity_v2(manifest, acquired_identity)

    # Pydantic model_copy is intentionally capable of making an unvalidated
    # adversarial plain-data value.  The verifier must not assume construction
    # provenance; it rechecks the manifest identity fields explicitly.
    other_identity = manifest.identity.model_copy(update={"repo": "other/repo"})
    adversarial_manifest = manifest.model_copy(update={"identity": other_identity})

    with pytest.raises(ManifestDiffBindingError) as excinfo:
        verify_manifest_diff_binding_v2(
            binding,
            manifest=adversarial_manifest,
            diff_text=diff_text,
        )
    assert excinfo.value.reason_code == DIFF_BINDING_MANIFEST_IDENTITY_MISMATCH_REASON_V2


def test_binding_schema_is_additive_and_manifest_contract_is_not_modified(tmp_path: Path) -> None:
    manifest, acquired_identity, _ = _manifest_and_exact_diff(tmp_path)
    binding = bind_manifest_to_diff_identity_v2(manifest, acquired_identity)

    dumped = binding.model_dump(mode="json")
    assert dumped["schema_id"] == "agent-review.manifest-diff-binding.v2"
    assert dumped["schema_version"] == 2
    assert set(dumped) == {
        "schema_id",
        "schema_version",
        "source",
        "run_id",
        "manifest_hash",
        "repository",
        "base_sha",
        "head_sha",
        "tested_merge_sha",
        "authoritative_diff_sha256",
    }
    assert "authoritative_diff_sha256" not in manifest.model_dump(mode="json")
