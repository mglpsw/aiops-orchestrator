"""`#200-E` Phase 3 -- integration test for the operational composer
(`operational_run_v2.run_operational_review_v2`), against a REAL git
target repository and the EXISTING `agent_escala` profile fixture (never
hand-constructed here -- loaded through the real `load_target_profile_v2`,
same discipline as `test_v2_dual_target_e2e.py`).

Provider-free: uses `offline_file_transport_v2`. No network, no live
Router, no provider credential.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import PullRequestStateV2, RunOriginV2, SemanticGroupV2
from app.agent_review.operational_run_v2 import OperationalReviewInputsV2, run_operational_review_v2
from app.agent_review.run_assembly_v2 import RunAssemblyError
from app.agent_review.review_transport_v2 import offline_file_transport_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2" / "agent_escala"
_EVIDENCE_HASH = "d" * 64


def _agent_escala_policy() -> SemanticGroupingPolicyV2:
    rules = [
        SemanticGroupingRuleV2(
            rule_id="backend",
            semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
            path_patterns=["backend/scheduling/*.py"],
            contract_ids=[],
            artifact_ids=[],
            priority=0,
        )
    ]
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": rules,
        "fallback_group": None,
    }
    policy_sha256 = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json") for rule in rules]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=policy_sha256)


def _build_real_agent_escala_target(tmp_path: Path) -> tuple[Path, str, str]:
    """A real git repo whose diff touches `backend/scheduling/*.py`
    (matching the profile's `must_review.patterns`), and which itself
    carries the profile-declared `artifacts/full.diff` and
    `contracts/domain-contracts.yaml` at head_sha -- the byte-identical
    fixture content, so the contract's pre-declared sha256 in the profile
    still matches."""

    repo = tmp_path / "target_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 1\n", encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    return repo, base_sha, head_sha


def test_offline_operational_review_reaches_honest_readiness(tmp_path: Path):
    """The decisive Phase-3 offline proof: real target checkout, real
    diff, real manifest/payload/content assembled through the controlled
    subjects, offline transport, real synthesis and readiness -- with NO
    authoritative check submitted, so the terminal readiness state is
    honestly non-ready, never forced to `ready`."""
    repo, base_sha, head_sha = _build_real_agent_escala_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()

    inputs = OperationalReviewInputsV2(
        source_target_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha="3" * 40,
        toolrepo_sha="4" * 40,
        evidence_hash=_EVIDENCE_HASH,
        repo="mglpsw/AgentEscala",
        pr_number=101,
        trusted_profile_root=FIXTURES_ROOT,
        grouping_policy=_agent_escala_policy(),
        transport=offline_file_transport_v2(responses_dir),
        pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id="delivery-200e-1"),
    )

    readiness = run_operational_review_v2(inputs)

    assert readiness.evaluated_head_sha == head_sha
    assert readiness.state != "ready"
    assert readiness.checks == []


def test_offline_operational_review_never_reuses_original_target_as_semantic_root(tmp_path: Path):
    """Structural oracle (§6): the original target path must not appear
    as the repo_root any semantic owner actually operated against --
    proven by severing the original target mid-review is NOT viable here
    (materialize_controlled_target_subject_v2 already proved severance
    survivability in Phase 2); instead this proves the ORIGINAL target's
    working tree is untouched by the run, which could only hold if no
    semantic owner wrote through it as a repo_root."""
    repo, base_sha, head_sha = _build_real_agent_escala_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()

    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }

    before = _snapshot(repo)

    inputs = OperationalReviewInputsV2(
        source_target_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha="3" * 40,
        toolrepo_sha="4" * 40,
        evidence_hash=_EVIDENCE_HASH,
        repo="mglpsw/AgentEscala",
        pr_number=101,
        trusted_profile_root=FIXTURES_ROOT,
        grouping_policy=_agent_escala_policy(),
        transport=offline_file_transport_v2(responses_dir),
        pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id="delivery-200e-2"),
    )
    run_operational_review_v2(inputs)

    after = _snapshot(repo)
    assert before == after


def test_unclassifiable_diff_propagates_the_owning_authoritys_typed_error(tmp_path: Path):
    """A target whose diff touches nothing the grouping policy's rules (or
    a fallback) cover fails closed with RunAssemblyError -- the OWNING
    authority's (assemble_manifest_from_diff_v2's) own typed family,
    propagated unmodified by this composer, not wrapped into a distinct
    OperationalRunError. Only this composer's OWN pre-seal failure classes
    (assembly returning a blocked_pipeline OUTCOME, or a
    preparation-closure mismatch) become OperationalRunError -- every
    exception RAISED by a front-half owner stays that owner's own family."""
    repo = tmp_path / "target_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    (repo / "unrelated.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "unrelated.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = OperationalReviewInputsV2(
        source_target_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha="3" * 40,
        toolrepo_sha="4" * 40,
        evidence_hash=_EVIDENCE_HASH,
        repo="mglpsw/AgentEscala",
        pr_number=101,
        trusted_profile_root=FIXTURES_ROOT,
        grouping_policy=_agent_escala_policy(),
        transport=offline_file_transport_v2(responses_dir),
        pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id="delivery-200e-3"),
    )

    with pytest.raises(RunAssemblyError) as excinfo:
        run_operational_review_v2(inputs)
    assert excinfo.value.reason_code == "semantic_grouping_no_match"


def test_manifest_assembly_blocked_pipeline_becomes_operational_run_error(tmp_path: Path):
    """The composer's OWN pre-seal failure class: assemble_manifest_from_diff_v2
    can return state="blocked_pipeline" (not raise) when required content
    cannot fit the declared budget -- this composer converts THAT outcome,
    specifically, into a typed OperationalRunError."""
    from app.agent_review.operational_run_v2 import (
        OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2,
        OperationalRunError,
    )

    repo = tmp_path / "target_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "".join(f"line_{i} = {i}\n" for i in range(200)), encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "".join(f"changed_line_{i} = {i}\n" for i in range(200)), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = OperationalReviewInputsV2(
        source_target_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha="3" * 40,
        toolrepo_sha="4" * 40,
        evidence_hash=_EVIDENCE_HASH,
        repo="mglpsw/AgentEscala",
        pr_number=101,
        trusted_profile_root=FIXTURES_ROOT,
        grouping_policy=_agent_escala_policy(),
        transport=offline_file_transport_v2(responses_dir),
        pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id="delivery-200e-4"),
        max_lines_per_chunk=1,
    )

    with pytest.raises(OperationalRunError) as excinfo:
        run_operational_review_v2(inputs)
    assert excinfo.value.reason_code == OPERATIONAL_RUN_ASSEMBLY_BLOCKED_REASON_V2
