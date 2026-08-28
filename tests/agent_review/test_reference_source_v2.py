"""`#200-D` successor: immutable head-bound reference material (issue #200).

Proves `TARGET_REFERENCE_SOURCE_INVARIANT`: payload reference bytes consumed
by the (UNCHANGED) payload owner derive exclusively from immutable Git
objects at `head_sha`, never from the target's mutable working tree. The
decisive control is `test_working_tree_presence_is_irrelevant_to_reference_
material` -- run A (path absent from the working tree) and run B (same path
present as an untracked/generated file) must produce byte-identical
materialized reference material, because the Git tree entry at `head_sha` is
the sole decision authority and the working tree is never consulted.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError
from app.agent_review.payload_builder_v2 import build_chunk_payloads_from_profile_v2
from app.agent_review.profile_loader_v2 import load_target_profile_v2
from app.agent_review.reference_source_v2 import (
    REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2,
    REFERENCE_SOURCE_UNAVAILABLE_REASON_V2,
    ReferenceSourceError,
    resolve_reference_source_v2,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


# Deterministic commit for `test_working_tree_presence_is_irrelevant_to_
# reference_material`: a Git commit SHA is a function of tree + parent(s) +
# author/committer identity + author/committer TIMESTAMP + message. Two
# independently-created repos with identical content only hash identically
# if every one of those fields matches -- the wall-clock timestamp does not,
# by construction, across two sequential `git commit` invocations. A prior
# version of this test omitted the fixed timestamp and happened to pass
# locally (both commits landing in the same wall-clock second) while failing
# in CI (they did not) -- a control that could pass by coincidence is not
# evidence.
_DETERMINISTIC_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
}


def _commit_all_deterministic(repo: Path, message: str) -> str:
    import os

    env = {**os.environ, **_DETERMINISTIC_COMMIT_ENV}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True, env=env)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


_PROFILE_TEXT_TEMPLATE = """schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/demo
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
  - artifact_id: optional-thing
    path: artifacts/optional.txt
    kind: text
    required: false
    max_bytes: 1000
budgets:
  max_chunks: 10
  total_prompt_chars: 100000
  max_chars_per_chunk: 20000
  max_files_per_chunk: 10
  max_contracts_per_chunk: 5
must_review:
  paths: []
  patterns: []
  artifact_ids: []
  minimum_coverage: complete
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks:
    - pytest
  allowed_semantic_groups:
    - docs_changelog
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts:
  - contract_id: scheduling-rules
    contract_version: "1"
    path: contracts/domain-contracts.yaml
    sha256: "{contract_sha256}"
    scope: repository
    required: true
limitations: []
"""


def _write_profile(repo: Path, *, contract_sha256: str) -> None:
    profile_dir = repo / ".aiops"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "target-profile.v2.yaml").write_text(
        _PROFILE_TEXT_TEMPLATE.format(contract_sha256=contract_sha256), encoding="utf-8"
    )


@pytest.fixture()
def committed_repo(tmp_path: Path):
    """A real repo with `artifacts/full.diff` and `contracts/domain-
    contracts.yaml` COMMITTED at the returned `head_sha`, plus a profile
    declaring both, and a third declared-but-never-present optional
    artifact."""

    repo = tmp_path / "target"
    _init_repo(repo)
    (repo / "artifacts").mkdir()
    (repo / "contracts").mkdir()
    (repo / "artifacts" / "full.diff").write_bytes(b"diff --git a/x b/x\n")
    (repo / "contracts" / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((repo / "contracts" / "domain-contracts.yaml").read_bytes()).hexdigest()
    _write_profile(repo, contract_sha256=contract_sha256)
    head_sha = _commit_all(repo, "init")
    profile = load_target_profile_v2(repo)
    return repo, head_sha, profile


def test_materializes_exact_committed_blob_bytes(committed_repo):
    repo, head_sha, profile = committed_repo
    with resolve_reference_source_v2(repo_root=repo, head_sha=head_sha, profile=profile) as ref:
        assert (ref.root / "artifacts" / "full.diff").read_bytes() == b"diff --git a/x b/x\n"
        assert (ref.root / "contracts" / "domain-contracts.yaml").read_bytes() == b"rules: []\n"


def test_absent_declared_path_is_not_materialized(committed_repo):
    """Optional artifact declared in the profile but never committed: no
    entry is materialized. The existing payload owner's own missing-
    reference semantics (untouched by this module) then apply."""

    repo, head_sha, profile = committed_repo
    with resolve_reference_source_v2(repo_root=repo, head_sha=head_sha, profile=profile) as ref:
        assert not (ref.root / "artifacts" / "optional.txt").exists()


def test_private_root_is_removed_on_success(committed_repo):
    repo, head_sha, profile = committed_repo
    with resolve_reference_source_v2(repo_root=repo, head_sha=head_sha, profile=profile) as ref:
        root = ref.root
        assert root.exists()
    assert not root.exists()


def test_private_root_is_removed_on_typed_refusal(committed_repo):
    repo, head_sha, profile = committed_repo
    (repo / "artifacts" / "full.diff").unlink()
    (repo / "artifacts" / "full.diff").symlink_to("/etc/hostname")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "symlink"], cwd=repo, check=True)
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    captured_root = None
    with pytest.raises(ReferenceSourceError) as excinfo:
        with resolve_reference_source_v2(repo_root=repo, head_sha=new_head, profile=profile) as ref:
            captured_root = ref.root  # pragma: no cover -- never reached
    assert excinfo.value.reason_code == REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2
    assert captured_root is None


def test_private_root_is_removed_on_unexpected_defect(committed_repo):
    """Cleanup must not depend on the body raising ONLY a typed refusal --
    any exception propagating through the `with` block must still trigger
    removal."""

    repo, head_sha, profile = committed_repo
    captured_root = None
    with pytest.raises(RuntimeError):
        with resolve_reference_source_v2(repo_root=repo, head_sha=head_sha, profile=profile) as ref:
            captured_root = ref.root
            raise RuntimeError("simulated programmer defect")
    assert captured_root is not None
    assert not captured_root.exists()


def test_symlink_tree_entry_is_refused(committed_repo):
    repo, head_sha, profile = committed_repo
    (repo / "artifacts" / "full.diff").unlink()
    (repo / "artifacts" / "full.diff").symlink_to("/etc/hostname")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "symlink"], cwd=repo, check=True)
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    with pytest.raises(ReferenceSourceError) as excinfo:
        with resolve_reference_source_v2(repo_root=repo, head_sha=new_head, profile=profile):
            pass
    assert excinfo.value.reason_code == REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2


def test_unresolvable_repository_is_unavailable(tmp_path: Path, committed_repo):
    _, head_sha, profile = committed_repo
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(ReferenceSourceError) as excinfo:
        with resolve_reference_source_v2(repo_root=not_a_repo, head_sha=head_sha, profile=profile):
            pass
    assert excinfo.value.reason_code == REFERENCE_SOURCE_UNAVAILABLE_REASON_V2


def test_full_pipeline_composes_with_the_unmodified_payload_owner(committed_repo):
    """The private root, unchanged, feeds the EXISTING
    `build_chunk_payloads_from_profile_v2` -- proving this module needs no
    change to the payload owner."""

    from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
    from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
    from app.agent_review.semantic_grouping_policy_v2 import (
        SemanticGroupingPolicyV2,
        SemanticGroupingRuleV2,
        bind_semantic_grouping_policy_to_target_profile_v2,
        compute_semantic_grouping_policy_sha256_v2,
    )

    repo, base_sha, profile = committed_repo
    (repo / "src.py").write_text("print(1)\n", encoding="utf-8")
    head_sha = _commit_all(repo, "second")

    rule = SemanticGroupingRuleV2(
        rule_id="r1", semantic_group="docs_changelog", path_patterns=["**"],
        contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2({**material, "rules": [rule.model_dump(mode="json")]})
    grouping_policy = SemanticGroupingPolicyV2(**material, policy_sha256=digest)
    bind_semantic_grouping_policy_to_target_profile_v2(grouping_policy, profile)

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=grouping_policy, repo=profile.identity.repo,
        pr_number=1, base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha,
        toolrepo_sha="a" * 40, evidence_hash="b" * 64, max_lines_per_chunk=100,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    manifest = outcome.manifest

    with resolve_reference_source_v2(repo_root=repo, head_sha=head_sha, profile=profile) as ref:
        built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=ref.root)
    assert len(built) >= 1
    for item in built:
        assert len(item.payload.artifact_references) == 1
        assert len(item.payload.contract_references) == 1
        assert "optional_artifact_missing:optional-thing" in item.limitations


# -- the decisive determinism control -----------------------------------------


def test_working_tree_presence_is_irrelevant_to_reference_material(tmp_path: Path):
    """Same head_sha, same profile: run A has the optional path ABSENT from
    the working tree; run B has the SAME path present as an untracked,
    generated working-tree file. Both must produce byte-identical
    materialized reference sets -- the working tree is never consulted.

    This is the falsifier for the whole design: a worktree-sensitive
    implementation (e.g. one that still branches on `Path.is_file()`) passes
    every other test in this module but fails this one.
    """

    repo_a = tmp_path / "run-a"
    _init_repo(repo_a)
    (repo_a / "artifacts").mkdir()
    (repo_a / "contracts").mkdir()
    (repo_a / "artifacts" / "full.diff").write_bytes(b"diff --git a/x b/x\n")
    (repo_a / "contracts" / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((repo_a / "contracts" / "domain-contracts.yaml").read_bytes()).hexdigest()
    _write_profile(repo_a, contract_sha256=contract_sha256)
    head_sha = _commit_all_deterministic(repo_a, "init")
    profile_a = load_target_profile_v2(repo_a)

    # run B: identical commit content, but the optional path additionally
    # exists as an UNTRACKED working-tree file after the commit -- as if
    # generated post-checkout by some other process.
    repo_b = tmp_path / "run-b"
    _init_repo(repo_b)
    (repo_b / "artifacts").mkdir()
    (repo_b / "contracts").mkdir()
    (repo_b / "artifacts" / "full.diff").write_bytes(b"diff --git a/x b/x\n")
    (repo_b / "contracts" / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    _write_profile(repo_b, contract_sha256=contract_sha256)
    head_sha_b = _commit_all_deterministic(repo_b, "init")
    assert head_sha_b == head_sha, "both repos must reach byte-identical commits to compare"
    (repo_b / "artifacts" / "optional.txt").write_text("generated after checkout\n", encoding="utf-8")

    profile_b = load_target_profile_v2(repo_b)

    with resolve_reference_source_v2(repo_root=repo_a, head_sha=head_sha, profile=profile_a) as ref_a:
        listing_a = sorted(p.relative_to(ref_a.root).as_posix() for p in ref_a.root.rglob("*") if p.is_file())
        bytes_a = {p: (ref_a.root / p).read_bytes() for p in listing_a}

    with resolve_reference_source_v2(repo_root=repo_b, head_sha=head_sha_b, profile=profile_b) as ref_b:
        listing_b = sorted(p.relative_to(ref_b.root).as_posix() for p in ref_b.root.rglob("*") if p.is_file())
        bytes_b = {p: (ref_b.root / p).read_bytes() for p in listing_b}

    assert listing_a == listing_b
    assert bytes_a == bytes_b
    assert "artifacts/optional.txt" not in listing_a
    assert "artifacts/optional.txt" not in listing_b


def test_reference_source_v2_never_calls_checkout_head_resolution():
    """Structural assertion for the amendment's explicit narrowing: this
    module must not resolve or condition on the target checkout's current
    HEAD -- that primitive belongs only to `toolrepo_identity_v2`'s
    different subject. Checked at the AST level (imports and call names),
    not by grepping prose -- the module's own docstring explains the
    narrowing using the words this assertion must not be tripped by."""

    import ast

    from app.agent_review import reference_source_v2

    tree = ast.parse(inspect_module_source(reference_source_v2))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "resolve_checkout_head_v2" not in imported_names
    assert "subprocess" not in {
        node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
    }

    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "resolve_checkout_head_v2" not in call_names


def inspect_module_source(module) -> str:
    import inspect

    return inspect.getsource(module)
