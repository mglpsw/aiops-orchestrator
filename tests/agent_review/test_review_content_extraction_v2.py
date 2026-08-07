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
    CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2,
    CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2,
    CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2,
    CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2,
    CONTENT_REASON_PROFILE_HASH_MISMATCH_V2,
    CONTENT_REASON_WINDOW_OWNS_NO_LINES_V2,
    ExtractionBlockedError,
    _assign_hunk_line_ownership_v2,
    extract_review_content_v2,
    slice_hunk_body_by_owned_lines_v2,
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
        path="a.py", hunk_index=0, diff_sha256="a" * 64,
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
        path="a.py", hunk_index=0, diff_sha256="a" * 64,
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

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
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

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    payloads = _payload_shas(manifest)
    first = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=payloads, target_profile=profile,
    )
    second = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=payloads, target_profile=profile,
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

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
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

    profile = _profile(must_review_paths=["big.py"], max_chars_per_chunk=200_000)
    manifest = _assemble(
        repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=20,
    )
    fragments_for_file = [f for f in manifest.fragments if f.path == "big.py"]
    assert len(fragments_for_file) > 1, "fixture must force windowing"

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
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

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf_lookup", action="block", detail="synthetic PHI marker")],
        detector_name=None, detector_digest=None,
    )
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile, dlp_policy=dlp,
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

    profile = _profile(must_review_paths=[])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="cpf", pattern="cpf_lookup", action="block", detail="synthetic PHI marker")],
        detector_name=None, detector_digest=None,
    )
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile, dlp_policy=dlp,
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

    # max_chars_per_chunk is a pure extraction-time content check -- the
    # planner never consults it for line packing (only max_lines_per_chunk
    # does) -- so a profile whose budget is already this tight still
    # assembles normally and only blocks once the REAL redacted content is
    # measured against it.
    profile = _profile(must_review_paths=["app.py"], max_chars_per_chunk=10)
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
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

    profile = _profile(must_review_paths=["app.py"])  # image.bin is NOT must_review
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
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

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    assert manifest.chunks == []
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={}, target_profile=profile,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2


# -- adversarial audit follow-up: repeated-anchor windowing duplication --
# (planner_v2._proportional_window's own documented starved-side exception)


def test_assign_hunk_line_ownership_gives_a_repeated_anchor_line_to_exactly_one_fragment() -> None:
    # 1 old line replaced by 4 new lines, windowed into 2 fragments: the
    # starved old side collapses to the SAME (1, 1) old_range for BOTH
    # windows (planner_v2._proportional_window's own documented behavior),
    # while the new side is genuinely split. Without ownership resolution,
    # BOTH fragments would independently select the old-side deletion line.
    body = HunkBodyV2(
        path="a.py", hunk_index=0, diff_sha256="a" * 64,
        body_text="-old\n+new1\n+new2\n+new3\n+new4",
        old_start=1, old_lines=1, new_start=1, new_lines=4,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False,
    )
    from app.agent_review.manifest_v2 import compute_fragment_id_v2

    old_range = LineRangeV2(start=1, end=1)  # repeated anchor, shared by both windows
    window0_new = LineRangeV2(start=1, end=2)
    window1_new = LineRangeV2(start=3, end=4)
    fragment0_id = compute_fragment_id_v2(path="a.py", old_range=old_range, new_range=window0_new, diff_sha256="a" * 64)
    fragment1_id = compute_fragment_id_v2(path="a.py", old_range=old_range, new_range=window1_new, diff_sha256="a" * 64)
    from app.agent_review.manifest_v2 import FragmentV2

    fragment0 = FragmentV2(
        fragment_id=fragment0_id, path="a.py", old_range=old_range, new_range=window0_new,
        hunk_indexes=[0], diff_chars=10, diff_sha256="a" * 64, coverage_required=True,
    )
    fragment1 = FragmentV2(
        fragment_id=fragment1_id, path="a.py", old_range=old_range, new_range=window1_new,
        hunk_indexes=[0], diff_chars=10, diff_sha256="a" * 64, coverage_required=True,
    )

    ownership = _assign_hunk_line_ownership_v2([fragment0, fragment1], body)
    owners = set(ownership.values())
    # Exactly one fragment owns the deletion line (index 0); no line index
    # is claimed by more than one owner (it's a dict, so that's structural,
    # but assert every real index IS owned -- lossless).
    assert len(ownership) == 5  # all 5 real lines owned by someone
    assert owners == {fragment0_id, fragment1_id}

    slice0 = slice_hunk_body_by_owned_lines_v2(
        body, owned_line_indices=frozenset(i for i, o in ownership.items() if o == fragment0_id)
    )
    slice1 = slice_hunk_body_by_owned_lines_v2(
        body, owned_line_indices=frozenset(i for i, o in ownership.items() if o == fragment1_id)
    )
    assert ("-old" in slice0) != ("-old" in slice1), "exactly one window owns the repeated anchor line"
    assert "+new1" in slice0 and "+new2" in slice0
    assert "+new3" in slice1 and "+new4" in slice1


@pytest.mark.requires_network
def test_extract_review_content_never_duplicates_a_repeated_anchor_line_across_windows(tmp_path: Path) -> None:
    """The exact adversarial scenario the audit flagged: 1 old line
    replaced by hundreds of new lines, forced into many windows by a small
    max_lines_per_chunk. planner_v2._proportional_window's own documented
    starved-side exception collapses the old_range to a single repeated
    anchor across every window -- proving by real-content IDENTITY (not
    just line text) that the single real deleted line is sent to the
    model exactly once, and that every real line (1 old + N new) is
    covered exactly once with zero omission."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "big.py").write_text("THE_ONE_OLD_LINE\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "big.py").write_text("\n".join(f"new line {i}" for i in range(1, 301)) + "\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["big.py"], max_chars_per_chunk=500_000)
    manifest = _assemble(
        repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=20,
    )
    fragments_for_file = [f for f in manifest.fragments if f.path == "big.py"]
    assert len(fragments_for_file) > 1, "fixture must force windowing"
    old_ranges = {(f.old_range.start, f.old_range.end) for f in fragments_for_file}
    assert old_ranges == {(1, 1)}, "fixture must exercise the repeated-anchor (starved old side) case"

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    included = [f for chunk in content.chunks for f in chunk.fragments if f.policy is ReviewContentPolicyV2.INCLUDED]

    occurrences = sum(1 for f in included if "THE_ONE_OLD_LINE" in (f.content or ""))
    assert occurrences == 1, f"the single deleted line must appear in exactly 1 fragment, found {occurrences}"

    line_counts: Counter[str] = Counter()
    for fragment in included:
        for raw_line in fragment.content.split("\n"):
            if not raw_line:
                continue
            line_counts[raw_line[1:] if raw_line[0] in "+- " else raw_line] += 1
    expected_lines = {"THE_ONE_OLD_LINE"} | {f"new line {i}" for i in range(1, 301)}
    assert set(line_counts) == expected_lines, "every real line must be covered, none invented"
    assert all(count == 1 for count in line_counts.values()), "no real line may be duplicated across windows"


# -- adversarial audit follow-up: chunk-level (not fragment-level) budget --


@pytest.mark.requires_network
def test_extract_review_content_blocks_when_many_small_must_review_fragments_sum_over_chunk_budget(
    tmp_path: Path,
) -> None:
    """Each individual fragment is well under max_chars_per_chunk, but
    enough of them land in the SAME chunk that their SUM exceeds it --
    the per-fragment-only check the audit flagged would let this through
    silently. Uses one file per hunk (not windowing) so every fragment is
    a genuine, independent whole-hunk fragment, isolating the chunk-level
    check from the windowing dedup fix above."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(20):
        (repo / f"file{i}.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    for i in range(20):
        (repo / f"file{i}.py").write_text("a = 1\nb = 2  # a change under 200 chars\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=[f"file{i}.py" for i in range(20)], max_chars_per_chunk=500)
    manifest = _assemble(repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=1000)
    # Force every fragment into the SAME single chunk regardless of
    # per-fragment size, so only the chunk-level SUM can trigger the block.
    assert len(manifest.chunks) == 1, "fixture must place every fragment in one chunk"

    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2


@pytest.mark.requires_network
def test_extract_review_content_drops_the_largest_auxiliary_fragments_first_when_chunk_exceeds_budget(
    tmp_path: Path,
) -> None:
    """Same shape as above, but NONE of the fragments are must_review --
    per planner_v2's own doctrine, auxiliary content is dropped (largest
    first) instead of blocking the whole run."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(20):
        (repo / f"file{i}.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    for i in range(20):
        (repo / f"file{i}.py").write_text("a = 1\nb = 2  # a change under 200 chars\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=[], max_chars_per_chunk=500)
    manifest = _assemble(repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=1000)
    assert len(manifest.chunks) == 1

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    included = [f for f in fragments if f.policy is ReviewContentPolicyV2.INCLUDED]
    dropped = [f for f in fragments if f.policy is ReviewContentPolicyV2.OMITTED_OVER_BUDGET]
    assert dropped, "at least one fragment must be dropped to fit the chunk budget"
    assert sum(f.chars for f in included) <= 500, "the chunk must never be transported above its budget"


@pytest.mark.requires_network
def test_extract_review_content_drops_auxiliaries_to_make_room_for_a_mixed_required_chunk(
    tmp_path: Path,
) -> None:
    """The exact scenario the audit flagged as broken: a chunk with BOTH a
    coverage_required fragment (well under budget on its own) AND
    auxiliary fragments that push the chunk's SUM over budget. The old
    check blocked the whole run the instant ANY coverage_required fragment
    was present in an over-budget chunk, even when dropping auxiliary
    content alone would have made it fit -- contradicting this module's
    own documented doctrine ("auxiliary dropped first; must_review never
    dropped"). The required fragment must survive INCLUDED; enough
    auxiliary fragments must be dropped for the chunk to fit."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "required.py").write_text("a = 1\n", encoding="utf-8")
    for i in range(20):
        (repo / f"aux{i}.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "required.py").write_text("a = 1\nb = 2  # required change\n", encoding="utf-8")
    for i in range(20):
        (repo / f"aux{i}.py").write_text("a = 1\nb = 2  # auxiliary change\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["required.py"], max_chars_per_chunk=500)
    manifest = _assemble(repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=1000)
    assert len(manifest.chunks) == 1, "fixture must place every fragment in one chunk"

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    included = [f for f in fragments if f.policy is ReviewContentPolicyV2.INCLUDED]
    dropped = [f for f in fragments if f.policy is ReviewContentPolicyV2.OMITTED_OVER_BUDGET]
    assert dropped, "at least one auxiliary fragment must be dropped to fit the chunk budget"
    required_included = [f for f in included if f.path == "required.py"]
    assert len(required_included) == 1, "the coverage_required fragment must survive INCLUDED"
    assert all(f.path != "required.py" for f in dropped), "a coverage_required fragment must never be dropped"
    assert sum(f.chars for f in included) <= 500, "the chunk must never be transported above its budget"


def test_target_budgets_cannot_be_relaxed_by_a_bare_int_the_caller_cannot_derive() -> None:
    # Structural proof, not behavioral: extract_review_content_v2 no
    # longer accepts a bare max_chars_per_chunk int, nor a bare
    # TargetBudgetsV2 a caller could construct with looser limits than the
    # profile that actually planned the manifest -- the ONLY input is the
    # full TargetProfileV2, whose hash is checked against the manifest's
    # own RunIdentityV2.profile_hash before any budget is ever read (see
    # test_extract_review_content_refuses_a_target_profile_that_did_not_
    # plan_the_manifest below for the behavioral proof).
    import inspect

    signature = inspect.signature(extract_review_content_v2)
    assert "max_chars_per_chunk" not in signature.parameters
    assert "target_budgets" not in signature.parameters
    assert signature.parameters["target_profile"].annotation == "TargetProfileV2"


@pytest.mark.requires_network
def test_extract_review_content_refuses_a_target_profile_that_did_not_plan_the_manifest(
    tmp_path: Path,
) -> None:
    """The behavioral half of the structural proof above: a caller cannot
    silently relax the effective budget by passing a DIFFERENT
    TargetProfileV2 at extraction time than the one run_assembly_v2 used
    to build the manifest -- even one that is otherwise valid and only
    diverges in ``budgets.max_chars_per_chunk`` -- because
    ``compute_profile_hash_v2(target_profile)`` is checked against
    ``manifest.identity.profile_hash`` before ``target_profile.budgets`` is
    ever read."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    planning_profile = _profile(must_review_paths=["app.py"], max_chars_per_chunk=24_000)
    manifest = _assemble(repo, base_sha, head_sha, profile=planning_profile)
    divergent_profile = _profile(must_review_paths=["app.py"], max_chars_per_chunk=999_999)
    assert divergent_profile != planning_profile

    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=divergent_profile,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_PROFILE_HASH_MISMATCH_V2

    # The SAME profile object, re-derived independently, is accepted --
    # proving the check is a real content-hash comparison, not identity.
    replanning_profile = _profile(must_review_paths=["app.py"], max_chars_per_chunk=24_000)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=replanning_profile,
    )
    assert content.chunks


# -- adversarial audit follow-up: DLP detector-only must fail-closed, never "clean" --


@pytest.mark.requires_network
def test_extract_review_content_blocks_must_review_fail_closed_when_dlp_policy_is_detector_only(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = 2  # perfectly ordinary, non-matching change\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    detector_only_dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1, rules=[],
        detector_name="host-owned-detector", detector_digest="a" * 64,
    )
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
            dlp_policy=detector_only_dlp,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2


@pytest.mark.requires_network
def test_extract_review_content_degrades_non_must_review_content_when_dlp_policy_is_detector_only(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = 2  # perfectly ordinary, non-matching change\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=[])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    detector_only_dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1, rules=[],
        detector_name="host-owned-detector", detector_digest="a" * 64,
    )
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
        dlp_policy=detector_only_dlp,
    )
    policies = {f.policy for chunk in content.chunks for f in chunk.fragments}
    assert policies == {ReviewContentPolicyV2.BLOCKED_BY_TARGET_DLP}
    assert all(f.content is None for chunk in content.chunks for f in chunk.fragments)


@pytest.mark.requires_network
def test_extract_review_content_still_evaluates_inline_rules_when_a_policy_also_has_a_detector(
    tmp_path: Path,
) -> None:
    """A policy naming BOTH a detector and inline rules is still
    unconditionally blocked (detector coverage can never be proven here) --
    this is not a case where the inline rules "save" the content."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = 2  # perfectly ordinary, non-matching change\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    combined_dlp = DlpPolicyDeclarationV2(
        schema_id="agent-review.dlp-policy.v1", schema_version=1,
        rules=[DlpPolicyRuleV2(rule_id="never-matches", pattern="ZZZ_NEVER_MATCHES_ZZZ", action="block", detail="d")],
        detector_name="host-owned-detector", detector_digest="a" * 64,
    )
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
            dlp_policy=combined_dlp,
        )
    assert excinfo.value.reason_code == CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2


# -- adversarial audit follow-up: additional hardening scenarios from #200's own test list --


@pytest.mark.requires_network
def test_extract_review_content_redacts_a_local_home_path_in_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nPATH = '/home/runner/.ssh/id_rsa'\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["app.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    assert all("/home/runner" not in (f.content or "") for f in fragments)


@pytest.mark.requires_network
def test_extract_review_content_covers_add_modify_delete_rename_in_one_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "modified.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "deleted.py").write_text("to be removed\n", encoding="utf-8")
    (repo / "old_name.py").write_text("rename target content\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "modified.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (repo / "deleted.py").unlink()
    (repo / "added.py").write_text("brand new file\n", encoding="utf-8")
    subprocess.run(["git", "mv", "old_name.py", "new_name.py"], cwd=repo, check=True)
    # A pure rename (no content change) produces zero hunks -- git has
    # nothing to show a diff of -- so it also touch the renamed file's
    # content, matching a realistic "rename + edit" PR.
    (repo / "new_name.py").write_text("rename target content\nplus an edit\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["modified.py", "deleted.py", "added.py", "new_name.py"])
    manifest = _assemble(repo, base_sha, head_sha, profile=profile)
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    fragments = [f for chunk in content.chunks for f in chunk.fragments]
    fragments_by_path = {f.path: f for f in fragments}
    assert "modified.py" in fragments_by_path
    assert "added.py" in fragments_by_path
    assert all(f.policy is ReviewContentPolicyV2.INCLUDED for f in fragments)

    # deleted.py: the fragment must exist (identity preserved, not silently
    # dropped) AND the removed line must actually be present in its real
    # extracted content -- not merely that a fragment with this path exists.
    assert "deleted.py" in fragments_by_path
    assert "-to be removed" in (fragments_by_path["deleted.py"].content or "")

    # rename + edit: ParsedFileDiffV2.path is new_path-or-old_path -- for a
    # rename (new_path always set) this is deterministically "new_name.py",
    # never "old_name.py". Proving the canonical identity exactly (no
    # permissive OR) catches a regression that silently files a rename
    # under its stale pre-rename path.
    assert "new_name.py" in fragments_by_path
    assert "old_name.py" not in fragments_by_path
    assert "plus an edit" in (fragments_by_path["new_name.py"].content or "")


def test_build_fragment_content_refuses_a_window_that_owns_no_real_lines() -> None:
    # Defense-in-depth unit test (see _build_fragment_content_v2's own
    # comment): not reachable through today's real planner_v2 windowing --
    # the driving side always partitions into >=1 real line per window --
    # but exercised directly here, matching this codebase's own "kept in
    # case a future change reopens it" precedent.
    from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2
    from app.agent_review.manifest_v2 import FragmentV2, compute_fragment_id_v2
    from app.agent_review.review_content_extraction_v2 import _build_fragment_content_v2

    file_diff = ParsedFileDiffV2(
        old_path="a.py", new_path="a.py", change_type="modified",
        is_binary=False, is_submodule=False, similarity_index=None,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False, hunks=(), truncated=False,
    )
    body = HunkBodyV2(
        path="a.py", hunk_index=0, diff_sha256="a" * 64,
        body_text="-old\n+new1\n+new2",
        old_start=1, old_lines=1, new_start=1, new_lines=2,
        old_no_newline_at_eof=False, new_no_newline_at_eof=False,
    )
    # A range that is NOT the hunk's own full range (new_range points past
    # the real content) -- is_whole_hunk_fragment is False, so this takes
    # the ownership-resolved slicing path, not the plain whole-hunk one.
    old_range = LineRangeV2(start=1, end=1)
    new_range = LineRangeV2(start=5, end=5)
    fragment_id = compute_fragment_id_v2(path="a.py", old_range=old_range, new_range=new_range, diff_sha256="a" * 64)
    fragment = FragmentV2(
        fragment_id=fragment_id, path="a.py", old_range=old_range, new_range=new_range,
        hunk_indexes=[0], diff_chars=10, diff_sha256="a" * 64, coverage_required=True,
    )

    with pytest.raises(ExtractionBlockedError) as excinfo:
        _build_fragment_content_v2(
            fragment, file_diff=file_diff, hunk_body=body, dlp_policy=None,
            max_chars_per_chunk=1000, owned_line_indices=frozenset(),  # owns nothing
        )
    assert excinfo.value.reason_code == CONTENT_REASON_WINDOW_OWNS_NO_LINES_V2

    fragment_optional = FragmentV2(
        fragment_id=fragment_id, path="a.py", old_range=old_range, new_range=new_range,
        hunk_indexes=[0], diff_chars=10, diff_sha256="a" * 64, coverage_required=False,
    )
    result = _build_fragment_content_v2(
        fragment_optional, file_diff=file_diff, hunk_body=body, dlp_policy=None,
        max_chars_per_chunk=1000, owned_line_indices=frozenset(),
    )
    assert result.policy is ReviewContentPolicyV2.UNREPRESENTABLE


@pytest.mark.requires_network
def test_extract_review_content_handles_two_hunks_of_the_same_file_landing_in_different_chunks(
    tmp_path: Path,
) -> None:
    """Two well-separated hunks in ONE file, small enough that neither
    needs windowing on its own, but far enough apart in the packer's line
    budget that they land in TWO DIFFERENT chunks -- proving ownership
    resolution (keyed globally by (path, hunk_index) across the whole
    manifest, not per chunk) and hunk-body lookup both work correctly when
    a single file's content is split across chunk boundaries."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    lines = [f"line {i}" for i in range(1, 101)]
    (repo / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    lines[4] = "CHANGED near top"
    lines[94] = "CHANGED near bottom"
    (repo / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile(must_review_paths=["big.py"], max_chars_per_chunk=200_000)
    manifest = _assemble(
        repo, base_sha, head_sha, profile=profile, max_lines_per_chunk=10,
    )
    assert len(manifest.chunks) == 2, "fixture must place the two hunks in different chunks"
    assert len(manifest.fragments) == 2, "fixture must not force windowing on either hunk"

    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id=_payload_shas(manifest), target_profile=profile,
    )
    bind_review_content_to_manifest_v2(content, manifest)  # re-verified from the outside
    assert len(content.chunks) == 2
    all_fragments = [f for chunk in content.chunks for f in chunk.fragments]
    assert all(f.path == "big.py" for f in all_fragments)
    assert all(f.policy is ReviewContentPolicyV2.INCLUDED for f in all_fragments)
    joined = "\n".join(f.content for f in all_fragments)
    assert "CHANGED near top" in joined
    assert "CHANGED near bottom" in joined
