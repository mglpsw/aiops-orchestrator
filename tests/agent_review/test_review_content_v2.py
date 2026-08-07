from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.review_content_v2 import (
    CONTENT_CHUNK_SET_MISMATCH_REASON_V2,
    CONTENT_COVERAGE_REQUIRED_MISMATCH_REASON_V2,
    CONTENT_DIFF_SHA256_MISMATCH_REASON_V2,
    CONTENT_FRAGMENT_NOT_IN_MANIFEST_REASON_V2,
    CONTENT_MANIFEST_HASH_MISMATCH_REASON_V2,
    CONTENT_PATH_MISMATCH_REASON_V2,
    CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2,
    CONTENT_RUN_IDENTITY_MISMATCH_REASON_V2,
    CONTENT_SET_HASH_MISMATCH_REASON_V2,
    DLP_POLICY_DIGEST_MISMATCH_REASON_V2,
    DLP_POLICY_NOT_HOST_OWNED_REASON_V2,
    ChunkContentV2,
    DlpPolicyContractError,
    DlpPolicyDeclarationV2,
    DlpPolicyRuleV2,
    FragmentContentV2,
    ReviewContentBindingError,
    ReviewContentPolicyV2,
    ReviewContentV2,
    bind_review_content_to_manifest_v2,
    compute_chunk_content_sha256_v2,
    compute_dlp_policy_digest_v2,
    compute_review_content_sha256_v2,
    load_dlp_policy_declaration_v2,
    verify_dlp_policy_digest_v2,
    verify_review_content_sha256_v2,
)

# -- fixture helpers, matching tests/agent_review/test_manifest_v2.py's own pattern --


def _identity(**overrides: object) -> RunIdentityV2:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 200,
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


def _fragment(
    *, path: str = "app/service.py", start: int = 1, end: int = 10, required: bool = True, salt: str = "e"
) -> FragmentV2:
    old_range = LineRangeV2(start=start, end=end)
    new_range = LineRangeV2(start=start, end=end)
    diff_sha256 = salt * 64
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
    fragments: list[FragmentV2] | None = None,
    chunks: list[ManifestChunkV2] | None = None,
    expected_files: list[str] | None = None,
    must_review_files: list[str] | None = None,
) -> ManifestV2:
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
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files or ["app/service.py"],
        "must_review_files": must_review_files if must_review_files is not None else ["app/service.py"],
        "fragments": fragments,
        "chunks": chunks,
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = _identity(manifest_hash=manifest_hash)
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _fragment_content(
    fragment: FragmentV2, *, text: str = "def f():\n    return 1\n", policy: ReviewContentPolicyV2 | None = None
) -> FragmentContentV2:
    policy = policy or ReviewContentPolicyV2.INCLUDED
    included = policy is ReviewContentPolicyV2.INCLUDED
    content = text if included else None
    content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest() if included else None
    return FragmentContentV2(
        fragment_id=fragment.fragment_id,
        path=fragment.path,
        diff_sha256=fragment.diff_sha256,
        policy=policy,
        coverage_required=fragment.coverage_required,
        content=content,
        content_sha256=content_sha256,
        redaction_applied=False,
        chars=len(text) if included else 0,
    )


def _chunk_content(chunk_id: str, fragments: list[FragmentContentV2], *, payload_sha256: str = "f" * 64) -> ChunkContentV2:
    material = {"chunk_id": chunk_id, "payload_sha256": payload_sha256, "fragments": fragments}
    return ChunkContentV2(**material, content_sha256=compute_chunk_content_sha256_v2(
        ChunkContentV2.model_construct(content_sha256="0" * 64, **material)
    ))


def _review_content(
    manifest: ManifestV2, chunks: list[ChunkContentV2], *, limitations: list[str] | None = None
) -> ReviewContentV2:
    material = {
        "schema_id": "agent-review.review-content.v2",
        "schema_version": 2,
        "source": "aiops-review-build-review-content",
        "run_id": manifest.run_id,
        "manifest_hash": manifest.identity.manifest_hash,
        "dlp_policy_digest": None,
        "chunks": chunks,
        "limitations": limitations or [],
    }
    return ReviewContentV2(**material, content_set_sha256=compute_review_content_sha256_v2(material))


def _default_content(manifest: ManifestV2) -> ReviewContentV2:
    fragment = manifest.fragments[0]
    fc = _fragment_content(fragment)
    cc = _chunk_content("chunk-0000", [fc])
    return _review_content(manifest, [cc])


# -- construction / self-consistency -----------------------------------------


def test_fragment_content_round_trips_strict() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    fc = _fragment_content(fragment)
    dumped = fc.model_dump_json()
    restored = FragmentContentV2.model_validate_json(dumped)
    assert restored == fc


def test_review_content_hash_is_deterministic_across_two_constructions() -> None:
    manifest = _manifest()
    content_a = _default_content(manifest)
    content_b = _default_content(manifest)
    assert content_a.content_set_sha256 == content_b.content_set_sha256


def test_review_content_hash_changes_when_one_byte_of_content_changes() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    content_a = _review_content(manifest, [_chunk_content("chunk-0000", [_fragment_content(fragment, text="a\n")])])
    content_b = _review_content(manifest, [_chunk_content("chunk-0000", [_fragment_content(fragment, text="b\n")])])
    assert content_a.content_set_sha256 != content_b.content_set_sha256


def test_chunk_order_does_not_change_the_content_set_hash() -> None:
    fragment_a = _fragment(path="app/a.py", salt="1")
    fragment_b = _fragment(path="app/b.py", salt="2")
    manifest = _manifest(
        fragments=[fragment_a, fragment_b],
        chunks=[
            ManifestChunkV2(
                chunk_id="chunk-0000",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_a.fragment_id],
                payload_sha256=None,
            ),
            ManifestChunkV2(
                chunk_id="chunk-0001",
                order_index=1,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_b.fragment_id],
                payload_sha256=None,
            ),
        ],
        expected_files=["app/a.py", "app/b.py"],
        must_review_files=["app/a.py", "app/b.py"],
    )
    cc_a = _chunk_content("chunk-0000", [_fragment_content(fragment_a)])
    cc_b = _chunk_content("chunk-0001", [_fragment_content(fragment_b)])

    forward = _review_content(manifest, [cc_a, cc_b])
    backward = _review_content(manifest, [cc_b, cc_a])
    assert forward.content_set_sha256 == backward.content_set_sha256


def test_verify_review_content_sha256_accepts_a_genuine_object() -> None:
    manifest = _manifest()
    content = _default_content(manifest)
    verify_review_content_sha256_v2(content)  # must not raise


def test_verify_review_content_sha256_rejects_a_bypassed_hash() -> None:
    manifest = _manifest()
    content = _default_content(manifest)
    tampered = content.model_copy(update={"content_set_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        verify_review_content_sha256_v2(tampered)


# -- FragmentContentV2 self-consistency invariants ----------------------------


def test_fragment_rejects_content_without_included_policy() -> None:
    fragment = _manifest().fragments[0]
    with pytest.raises(ValidationError):
        FragmentContentV2(
            fragment_id=fragment.fragment_id,
            path=fragment.path,
            diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.OMITTED_OVER_BUDGET,
            coverage_required=False,
            content="def f(): pass\n",
            content_sha256=hashlib.sha256(b"def f(): pass\n").hexdigest(),
            redaction_applied=False,
            chars=14,
        )


def test_fragment_rejects_a_mismatched_content_sha256() -> None:
    fragment = _manifest().fragments[0]
    text = "def f():\n    return 1\n"
    with pytest.raises(ValidationError):
        FragmentContentV2(
            fragment_id=fragment.fragment_id,
            path=fragment.path,
            diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.INCLUDED,
            coverage_required=True,
            content=text,
            content_sha256="0" * 64,
            redaction_applied=False,
            chars=len(text),
        )


def test_fragment_rejects_coverage_required_without_included_policy() -> None:
    fragment = _manifest().fragments[0]
    with pytest.raises(ValidationError, match=CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2):
        FragmentContentV2(
            fragment_id=fragment.fragment_id,
            path=fragment.path,
            diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.OMITTED_OVER_BUDGET,
            coverage_required=True,
            content=None,
            content_sha256=None,
            redaction_applied=False,
            chars=0,
        )


def test_omitted_over_budget_is_allowed_for_a_non_required_fragment() -> None:
    fragment = _fragment(required=False)
    fc = FragmentContentV2(
        fragment_id=fragment.fragment_id,
        path=fragment.path,
        diff_sha256=fragment.diff_sha256,
        policy=ReviewContentPolicyV2.OMITTED_OVER_BUDGET,
        coverage_required=False,
        content=None,
        content_sha256=None,
        redaction_applied=False,
        chars=0,
    )
    assert fc.content is None


def test_reviewable_content_rejects_a_planted_secret() -> None:
    fragment = _manifest().fragments[0]
    text = "token = ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
    with pytest.raises(ValidationError):
        FragmentContentV2(
            fragment_id=fragment.fragment_id,
            path=fragment.path,
            diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.INCLUDED,
            coverage_required=False,
            content=text,
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            redaction_applied=False,
            chars=len(text),
        )


def test_chunk_content_rejects_duplicate_fragment_ids() -> None:
    fragment = _manifest().fragments[0]
    fc = _fragment_content(fragment)
    with pytest.raises(ValidationError):
        _chunk_content("chunk-0000", [fc, fc])


def test_review_content_rejects_duplicate_chunk_ids() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    cc = _chunk_content("chunk-0000", [_fragment_content(fragment)])
    with pytest.raises(ValidationError):
        _review_content(manifest, [cc, cc.model_copy()])


# -- bind_review_content_to_manifest_v2 --------------------------------------


def test_bind_accepts_a_genuine_review_content_object() -> None:
    manifest = _manifest()
    content = _default_content(manifest)
    bind_review_content_to_manifest_v2(content, manifest)  # must not raise


def test_bind_rejects_a_run_id_mismatch() -> None:
    manifest = _manifest()
    other_manifest = _manifest(fragments=[_fragment(salt="9")])
    content = _default_content(other_manifest)
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_RUN_IDENTITY_MISMATCH_REASON_V2


def test_bind_rejects_a_manifest_hash_mismatch() -> None:
    manifest = _manifest()
    content = _default_content(manifest)
    tampered = content.model_copy(update={"manifest_hash": "9" * 64})
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(tampered, manifest)
    assert exc_info.value.reason_code == CONTENT_MANIFEST_HASH_MISMATCH_REASON_V2


def test_bind_rejects_a_content_chunk_absent_from_the_manifest() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    extra_chunk = _chunk_content("chunk-9999", [_fragment_content(fragment)])
    content = _review_content(manifest, [_chunk_content("chunk-0000", [_fragment_content(fragment)]), extra_chunk])
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_CHUNK_SET_MISMATCH_REASON_V2


def test_bind_rejects_a_fragment_not_in_the_manifest_chunk() -> None:
    manifest_fragment = _fragment(path="app/a.py", salt="1")
    manifest = _manifest(
        fragments=[manifest_fragment], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    foreign_fragment = _fragment(path="app/other.py", salt="7", required=False)
    foreign_content = _fragment_content(foreign_fragment)
    content = _review_content(manifest, [_chunk_content("chunk-0000", [foreign_content])])
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_FRAGMENT_NOT_IN_MANIFEST_REASON_V2


def test_bind_rejects_a_path_diverging_from_the_manifest_fragment() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    fc = _fragment_content(fragment)
    tampered_fc = fc.model_copy(update={"path": "app/elsewhere.py"})
    content = _review_content(manifest, [_chunk_content("chunk-0000", [tampered_fc])])
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_PATH_MISMATCH_REASON_V2


def test_bind_rejects_a_diff_sha256_diverging_from_the_manifest_fragment() -> None:
    manifest = _manifest()
    fragment = manifest.fragments[0]
    fc = _fragment_content(fragment)
    tampered_fc = fc.model_copy(update={"diff_sha256": "9" * 64})
    content = _review_content(manifest, [_chunk_content("chunk-0000", [tampered_fc])])
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_DIFF_SHA256_MISMATCH_REASON_V2


def test_bind_rejects_a_coverage_required_flag_diverging_from_the_manifest() -> None:
    manifest = _manifest()  # fragment is coverage_required=True
    fragment = manifest.fragments[0]
    fc = _fragment_content(fragment)
    tampered_fc = fc.model_copy(update={"coverage_required": False})
    content = _review_content(manifest, [_chunk_content("chunk-0000", [tampered_fc])])
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(content, manifest)
    assert exc_info.value.reason_code == CONTENT_COVERAGE_REQUIRED_MISMATCH_REASON_V2


def test_bind_rejects_a_tampered_content_set_hash() -> None:
    manifest = _manifest()
    content = _default_content(manifest)
    tampered = content.model_copy(update={"content_set_sha256": "0" * 64})
    with pytest.raises(ReviewContentBindingError) as exc_info:
        bind_review_content_to_manifest_v2(tampered, manifest)
    assert exc_info.value.reason_code == CONTENT_SET_HASH_MISMATCH_REASON_V2


# -- DLP policy contract -------------------------------------------------------


def test_dlp_policy_requires_rules_or_a_host_owned_detector() -> None:
    with pytest.raises(ValidationError):
        DlpPolicyDeclarationV2(
            schema_id="agent-review.dlp-policy.v1",
            schema_version=1,
            rules=[],
            detector_name=None,
            detector_digest=None,
        )


def test_dlp_policy_digest_is_deterministic() -> None:
    declaration = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1",
        schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf-like", action="block", detail="Brazilian CPF")],
        detector_name=None,
        detector_digest=None,
    )
    assert compute_dlp_policy_digest_v2(declaration) == compute_dlp_policy_digest_v2(declaration.model_copy())


@pytest.mark.parametrize("forbidden_key", ["path", "module", "import", "entrypoint", "entry_point", "code", "script"])
def test_load_dlp_policy_rejects_target_owned_code_references(forbidden_key: str) -> None:
    raw = {
        "schema_id": "agent-review.dlp-policy.v1",
        "schema_version": 1,
        "rules": [],
        "detector_name": "phi_ptbr",
        "detector_digest": "a" * 64,
        forbidden_key: "target/dlp.py",
    }
    with pytest.raises(DlpPolicyContractError) as exc_info:
        load_dlp_policy_declaration_v2(raw)
    assert exc_info.value.reason_code == DLP_POLICY_NOT_HOST_OWNED_REASON_V2


def test_load_dlp_policy_accepts_a_clean_declaration() -> None:
    raw = {
        "schema_id": "agent-review.dlp-policy.v1",
        "schema_version": 1,
        "rules": [{"rule_id": "cpf", "pattern": "cpf-like", "action": "block", "detail": "Brazilian CPF"}],
        "detector_name": None,
        "detector_digest": None,
    }
    declaration = load_dlp_policy_declaration_v2(raw)
    assert declaration.rules[0].rule_id == "cpf"


def test_verify_dlp_policy_digest_rejects_a_divergent_digest() -> None:
    declaration = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1",
        schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf-like", action="block", detail="Brazilian CPF")],
        detector_name=None,
        detector_digest=None,
    )
    with pytest.raises(DlpPolicyContractError) as exc_info:
        verify_dlp_policy_digest_v2(declaration, expected_digest="0" * 64)
    assert exc_info.value.reason_code == DLP_POLICY_DIGEST_MISMATCH_REASON_V2


def test_verify_dlp_policy_digest_accepts_the_real_digest() -> None:
    declaration = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1",
        schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf-like", action="block", detail="Brazilian CPF")],
        detector_name=None,
        detector_digest=None,
    )
    verify_dlp_policy_digest_v2(declaration, expected_digest=compute_dlp_policy_digest_v2(declaration))
