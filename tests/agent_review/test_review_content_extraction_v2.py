from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import SemanticGroupV2, TargetProfileV2
from app.agent_review.diff_acquisition_v2 import (
    HunkBodyV2,
    acquire_authoritative_diff_v2,
    compute_hunk_diff_sha256_v2,
    extract_hunk_bodies_v2,
)
from app.agent_review.manifest_v2 import LineRangeV2
from app.agent_review.payload_builder_v2 import build_chunk_payload_v2
from app.agent_review.review_content_extraction_v2 import (
    CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2,
    CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2,
    ExtractionBlockedError,
    extract_review_content_v2,
    slice_hunk_body_by_range_v2,
)
from app.agent_review.review_content_v2 import (
    DlpPolicyDeclarationV2,
    DlpPolicyRuleV2,
    ReviewContentPolicyV2,
    bind_review_content_to_manifest_v2,
)
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

# -- real-git-repo fixture helpers, matching test_diff_acquisition_v2.py's own pattern --


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _profile(*, must_review_paths: list[str], max_chars_per_chunk: int = 24000) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2",
            "schema_version": 2,
            "source": "repo-profile",
            "identity": {"repo": "example/repo", "default_branch": "main"},
            "artifacts": [
                {
                    "artifact_id": "full-diff",
                    "path": "artifacts/full.diff",
                    "kind": "diff",
                    "required": True,
                    "max_bytes": 1000000,
                }
            ],
            "budgets": {
                "max_chunks": 32,
                "total_prompt_chars": 250000,
                "max_chars_per_chunk": max_chars_per_chunk,
                "max_files_per_chunk": 50,
                "max_contracts_per_chunk": 50,
            },
            "must_review": {
                "paths": must_review_paths,
                "patterns": [],
                "artifact_ids": [],
                "minimum_coverage": "complete",
            },
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
            "contracts": [
                {
                    "contract_id": "contract.api",
                    "contract_version": "1",
                    "path": ".aiops/domain-contracts.yaml",
                    "sha256": "f" * 64,
                    "scope": "repository",
                    "required": True,
                }
            ],
            "limitations": [],
        }
    )


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
        path_patterns=["*"], contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json")]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _assemble(repo, base_sha, head_sha, *, profile, max_lines_per_chunk=1000):
    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=_grouping_policy(),
        repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
        tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
        max_lines_per_chunk=max_lines_per_chunk,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    return outcome.manifest


def _payload_shas(manifest):
    return {chunk.chunk_id: build_chunk_payload_v2(manifest, chunk).payload_sha256 for chunk in manifest.chunks}


# -- extract_hunk_bodies_v2 / compute_hunk_diff_sha256_v2 (diff_acquisition_v2) --


@pytest.mark.requires_network
def test_extract_hunk_bodies_reproduces_the_same_diff_sha256_as_the_parser(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "a.py").write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    from app.agent_review.diff_acquisition_v2 import acquire_diff_v2

    diff_text = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    bodies = extract_hunk_bodies_v2(diff_text)

    assert len(bodies) == 1
    parsed_hunk = file_diffs[0].hunks[0]
    assert bodies[0].diff_sha256 == parsed_hunk.diff_sha256
    recomputed = compute_hunk_diff_sha256_v2(
        bodies[0].body_text,
        old_no_newline_at_eof=bodies[0].old_no_newline_at_eof,
        new_no_newline_at_eof=bodies[0].new_no_newline_at_eof,
    )
    assert recomputed == parsed_hunk.diff_sha256


def test_extract_hunk_bodies_empty_diff_returns_empty_tuple() -> None:
    assert extract_hunk_bodies_v2("") == ()
    assert extract_hunk_bodies_v2("   \n") == ()


# -- slice_hunk_body_by_range_v2 --


def test_slice_whole_hunk_range_reproduces_the_full_body() -> None:
    body = HunkBodyV2(
        path="a.py", hunk_index=0, diff_sha256="x" * 64,
        body_text=" ctx1\n-old\n+new\n ctx2",
        old_start=1, old_lines=3, new_start=1, new_lines=3,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False,
    )
    sliced = slice_hunk_body_by_range_v2(
        body, old_range=LineRangeV2(start=1, end=3), new_range=LineRangeV2(start=1, end=3)
    )
    assert sliced == body.body_text


def test_slice_window_selects_only_lines_in_range() -> None:
    # 4-line hunk: ctx(old1/new1), del(old2), ins(new2), ctx(old3/new3)
    body = HunkBodyV2(
        path="a.py", hunk_index=0, diff_sha256="x" * 64,
        body_text=" ctx1\n-del\n+ins\n ctx3",
        old_start=1, old_lines=3, new_start=1, new_lines=3,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False,
    )
    # Only the deletion line (old line 2) is requested; new_range points at
    # a line the deletion does not touch, so only "-del" should surface.
    sliced = slice_hunk_body_by_range_v2(
        body, old_range=LineRangeV2(start=2, end=2), new_range=LineRangeV2(start=1, end=1)
    )
    assert "-del" in sliced
    assert "+ins" not in sliced
    assert "ctx3" not in sliced


# -- extract_review_content_v2: end-to-end with a real repo, real manifest, real payload --


@pytest.mark.requires_network
def test_extract_review_content_round_trips_and_binds_to_the_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "\n".join(f"line {i}" for i in range(1, 21))
    (repo / "app.py").write_text(body + "\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    lines = body.split("\n")
    lines[4] = "line 5 CHANGED"
    (repo / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=["app.py"]))
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest),
    )

    bind_review_content_to_manifest_v2(content, manifest)  # re-verified from the outside
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    assert fragments
    assert all(f.policy is ReviewContentPolicyV2.INCLUDED for f in fragments)
    assert "line 5 CHANGED" in fragments[0].content


@pytest.mark.requires_network
def test_extract_review_content_is_byte_deterministic_across_calls(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a\nB\nc\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=["app.py"]))
    payloads = _payload_shas(manifest)
    first = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=payloads,
    )
    second = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=payloads,
    )
    assert first.content_set_sha256 == second.content_set_sha256


@pytest.mark.requires_network
def test_extract_review_content_redacts_a_secret_before_it_reaches_the_sidecar(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nTOKEN = 'ghp_" + "x" * 36 + "'\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=["app.py"]))
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest),
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    assert all("ghp_" not in (f.content or "") for f in fragments)
    assert any(f.redaction_applied for f in fragments)


@pytest.mark.requires_network
def test_extract_review_content_windows_a_hunk_larger_than_the_line_budget_losslessly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "big.py").write_text("\n".join(f"orig {i}" for i in range(1, 121)) + "\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "big.py").write_text(
        "\n".join(f"new {i}" if i % 2 == 0 else f"orig {i}" for i in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(
        repo, base_sha, head_sha,
        profile=_profile(must_review_paths=["big.py"], max_chars_per_chunk=200_000),
        max_lines_per_chunk=20,
    )
    fragments_for_file = [f for f in manifest.fragments if f.path == "big.py"]
    assert len(fragments_for_file) > 1, "fixture must force windowing"

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), max_chars_per_chunk=200_000,
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    assert len(fragments) == len(fragments_for_file)
    assert all(f.policy is ReviewContentPolicyV2.INCLUDED for f in fragments)

    line_counts: Counter[str] = Counter()
    for fragment in fragments:
        for line in fragment.content.split("\n"):
            if line.strip():
                line_counts[line] += 1
    assert not any(count > 1 for count in line_counts.values()), "no line double-counted across windows"


@pytest.mark.requires_network
def test_extract_review_content_blocks_fail_closed_when_a_must_review_fragment_matches_dlp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\ncpf_lookup(patient)\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=["app.py"]))
    dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf_lookup", action="block", detail="synthetic PHI marker")],
        detector_name=None, detector_digest=None,
    )
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), dlp_policy=dlp,
        )
    assert excinfo.value.reason_code == "transport_blocked_by_dlp"


@pytest.mark.requires_network
def test_extract_review_content_degrades_a_non_must_review_dlp_match_to_a_typed_omission(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\ncpf_lookup(patient)\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=[]))
    dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf_lookup", action="block", detail="synthetic PHI marker")],
        detector_name=None, detector_digest=None,
    )
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), dlp_policy=dlp,
    )
    policies = {f.policy for chunk in content.chunks for f in chunk.fragments}
    assert ReviewContentPolicyV2.BLOCKED_BY_TARGET_DLP in policies


@pytest.mark.requires_network
def test_extract_review_content_blocks_fail_closed_when_must_review_content_exceeds_the_char_budget(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\n" + "x = 1\n" * 50, encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(
        repo, base_sha, head_sha,
        profile=_profile(must_review_paths=["app.py"], max_chars_per_chunk=200_000),
    )
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), max_chars_per_chunk=10,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2


def test_classify_unrepresentable_marks_binary_and_submodule_files_typed() -> None:
    # Defense-in-depth unit test: today's real run_assembly_v2 output never
    # actually routes a binary/submodule file's fragment through this
    # function (see its docstring) -- exercised directly here, matching
    # this codebase's own "kept in case a future change reopens it"
    # precedent, rather than silently left untested.
    from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2
    from app.agent_review.review_content_extraction_v2 import _classify_unrepresentable_v2

    binary_diff = ParsedFileDiffV2(
        old_path="image.bin", new_path="image.bin", change_type="modified",
        is_binary=True, is_submodule=False, similarity_index=None,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False, hunks=(), truncated=False,
    )
    submodule_diff = ParsedFileDiffV2(
        old_path="vendor/lib", new_path="vendor/lib", change_type="modified",
        is_binary=False, is_submodule=True, similarity_index=None,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False, hunks=(), truncated=False,
    )
    text_diff = ParsedFileDiffV2(
        old_path="app.py", new_path="app.py", change_type="modified",
        is_binary=False, is_submodule=False, similarity_index=None,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False, hunks=(), truncated=False,
    )
    generated_diff = ParsedFileDiffV2(
        old_path="dist/bundle.generated.js", new_path="dist/bundle.generated.js", change_type="modified",
        is_binary=False, is_submodule=False, similarity_index=None,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False, hunks=(), truncated=False,
    )

    assert _classify_unrepresentable_v2(binary_diff) == ReviewContentPolicyV2.OMITTED_BINARY
    assert _classify_unrepresentable_v2(submodule_diff) == ReviewContentPolicyV2.OMITTED_SUBMODULE
    assert _classify_unrepresentable_v2(text_diff) is None
    assert _classify_unrepresentable_v2(generated_diff) == ReviewContentPolicyV2.OMITTED_GENERATED
    assert _classify_unrepresentable_v2(None) == ReviewContentPolicyV2.UNREPRESENTABLE


@pytest.mark.requires_network
def test_extract_review_content_excludes_a_non_must_review_binary_file_entirely(tmp_path: Path) -> None:
    """Documents the REAL, current behavior (not the defensive one above):
    a non-must-review binary file produces no fragment at all -- it is
    invisible to ReviewContentV2, not present with a typed omission --
    because run_assembly_v2 excludes it before any fragment_id exists."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\x00\x01\x02binarydata")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\x00\x01\x02binarydataCHANGED")
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(
        repo, base_sha, head_sha,
        profile=_profile(must_review_paths=["app.py"]),  # image.bin is NOT must_review
    )
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest),
    )
    policies_by_path = {f.path: f.policy for chunk in content.chunks for f in chunk.fragments}
    assert "image.bin" not in policies_by_path
    assert policies_by_path.get("app.py") == ReviewContentPolicyV2.INCLUDED


@pytest.mark.requires_network
def test_extract_review_content_refuses_fail_closed_when_the_manifest_has_no_chunks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\x00\x01\x02binarydata")
    base_sha = _commit_all(repo, "init")
    (repo / "image.bin").write_bytes(b"\x00\x01\x02binarydataCHANGED")  # app.py untouched
    head_sha = _commit_all(repo, "update")

    manifest = _assemble(repo, base_sha, head_sha, profile=_profile(must_review_paths=["app.py"]))
    assert manifest.chunks == []
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={},
        )
    assert excinfo.value.reason_code == CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2
