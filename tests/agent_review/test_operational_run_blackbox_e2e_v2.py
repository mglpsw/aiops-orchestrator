"""`#200-D` successor: provider-free black-box E2E (issue #200, §4/§12).

The mandatory acceptance test: drives `scripts/aiops-review-run-v2.py` as a
REAL PROCESS from the toolrepo, against a REAL target Git repository created
OUTSIDE the toolrepo tree. No model, provider or network dependency. The
target is never imported into the toolrepo tree, no engine Python is copied
into the target, and nothing here branches on target repository name.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import compute_response_sha256_v2
from app.agent_review.operational_run_v2 import prepare_operational_review_v2
from app.agent_review.review_transport_contract_v2 import compute_request_sha256_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = REPO_ROOT / "scripts" / "aiops-review-run-v2.py"

_PROFILE_TEXT_TEMPLATE = """schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/blackbox-target
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
budgets:
  max_chunks: 32
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths:
    - app.py
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
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts:
  - contract_id: contract.api
    contract_version: "1"
    path: .aiops/domain-contracts.yaml
    sha256: "{contract_sha256}"
    scope: repository
    required: true
limitations: []
"""

_AUTHORITATIVE_CHECKS_TEXT = """schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: example/blackbox-target
authoritative_checks:
  - check_name: pytest
    workflow_path: .github/workflows/authoritative-checks.yml
    job_name: authoritative pytest
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: example/blackbox-target
      path: .github/workflows/authoritative-checks.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    producer_workflow_ref: refs/heads/main
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
"""


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_target_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "blackbox-target-repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((repo / ".aiops" / "domain-contracts.yaml").read_bytes()).hexdigest()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(
        _PROFILE_TEXT_TEMPLATE.format(contract_sha256=contract_sha256), encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "full.diff").write_bytes(b"placeholder\n")
    base_sha = _commit_all(repo, "init")

    (repo / "app.py").write_text("a = 1\nb = CHANGED\nc = 3\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")
    return repo, base_sha, head_sha


def _make_trusted_profile_root(tmp_path: Path) -> Path:
    profile_root = tmp_path / "blackbox-trusted-profile"
    aiops_dir = profile_root / ".aiops"
    aiops_dir.mkdir(parents=True)
    (aiops_dir / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((aiops_dir / "domain-contracts.yaml").read_bytes()).hexdigest()
    (aiops_dir / "target-profile.v2.yaml").write_text(
        _PROFILE_TEXT_TEMPLATE.format(contract_sha256=contract_sha256), encoding="utf-8"
    )
    (aiops_dir / "authoritative-checks.v2.yaml").write_text(_AUTHORITATIVE_CHECKS_TEXT, encoding="utf-8")
    return profile_root


def _grouping_policy_file(tmp_path: Path) -> Path:
    rule = SemanticGroupingRuleV2(
        rule_id="all", semantic_group="primary_backend_logic", path_patterns=["*"],
        contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule.model_dump(mode="json")],
        "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2(material)
    path = tmp_path / "grouping-policy.json"
    path.write_text(json.dumps({**material, "policy_sha256": digest}), encoding="utf-8")
    return path


def _run_origin_file(tmp_path: Path) -> Path:
    path = tmp_path / "run-origin.json"
    path.write_text(
        json.dumps({"event_type": "pull_request", "event_action": "synchronize", "delivery_id": "delivery-bb-1"}),
        encoding="utf-8",
    )
    return path


def _checks_snapshot_file(tmp_path: Path) -> Path:
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import TOOLCHAIN_DIGEST, _snapshot_dict

    path = tmp_path / "checks-snapshot.json"
    path.write_text(json.dumps(_snapshot_dict([])), encoding="utf-8")
    return path, TOOLCHAIN_DIGEST


def _success_envelope_dict(*, run_id: str, chunk_id: str, head_sha: str, payload_sha256: str) -> dict:
    result = {
        "schema_id": "agent-review.chunk-response.v2", "schema_version": 2,
        "summary": "looks fine", "findings": [],
        "coverage": {
            "status": "complete", "expected_files": ["app.py"], "reviewed_files": ["app.py"],
            "partially_reviewed_files": [], "missing_files": [], "must_review_files": ["app.py"],
            "missing_must_review_files": [], "degradation_causes": [],
        },
        "limitations": [],
    }
    envelope = {
        "schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2,
        "source": "agent-review-provider-response", "status": "success",
        "run_id": run_id, "chunk_id": chunk_id, "payload_sha256": payload_sha256, "head_sha": head_sha,
        "provider": "test-provider", "model": "test-model", "attempt": 1, "request_id": f"req-{chunk_id}",
        "finish_reason": "stop", "response_received": True, "result": result, "response_sha256": None,
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _write_offline_responses(responses_dir: Path, *, content, manifest) -> None:
    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in content.chunks:
        response = _success_envelope_dict(
            run_id=content.run_id, chunk_id=chunk.chunk_id,
            head_sha=manifest.identity.head_sha, payload_sha256=chunk.payload_sha256,
        )
        request_sha256 = compute_request_sha256_v2(
            run_id=content.run_id, chunk_id=chunk.chunk_id, head_sha=manifest.identity.head_sha,
            payload_sha256=chunk.payload_sha256, content_sha256=chunk.content_sha256,
        )
        envelope = {
            "schema_id": "agent-review.review-transport-envelope.v1", "schema_version": 1,
            "request_sha256": request_sha256, "content_sha256": chunk.content_sha256, "response": response,
        }
        (responses_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(envelope), encoding="utf-8")


@pytest.fixture()
def real_toolrepo_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _canonical_target_observation_v2(repo: Path) -> str:
    """A before/after oracle capable of detecting tracked modification,
    tracked deletion, a new untracked file, AND a new file an ignore rule
    would otherwise hide -- `HEAD^{tree}` alone proves only committed-tree
    identity and cannot see any of these (a new untracked file changes
    nothing about a tree hash). `-uall` lists every untracked path
    individually rather than collapsing a directory; `--ignored=matching`
    surfaces ignored paths too, verified directly: without it, a file
    matched by a `.gitignore` entry is invisible to `git status` even
    though it is fully present on disk."""

    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_target_mutation_oracle_detects_an_ignored_untracked_file(tmp_path):
    """`#200-D` correction: `HEAD^{tree}` equality -- the ONLY oracle the
    prior black-box test used -- cannot see a new untracked file at all,
    ignored or not. This proves the CORRECTED oracle
    (`_canonical_target_observation_v2`) actually discriminates a mutation
    the old one would have silently passed."""

    repo, _base_sha, _head_sha = _make_target_repo(tmp_path)

    tree_before = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    observation_before = _canonical_target_observation_v2(repo)

    # Simulate what a mutating bug could leave behind: a new file matched
    # by an existing or newly-added ignore rule.
    (repo / ".gitignore").write_text("leaked-review-output.json\n")
    (repo / "leaked-review-output.json").write_text('{"leaked": true}\n')

    tree_after = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    observation_after = _canonical_target_observation_v2(repo)

    assert tree_after == tree_before, (
        "sanity check: HEAD^{tree} is UNCHANGED by this mutation -- exactly "
        "why it is not a sufficient oracle on its own"
    )
    assert observation_after != observation_before, (
        "the corrected oracle must detect a new file even when a .gitignore "
        "rule would hide it from an unadorned `git status`"
    )


def test_cli_process_reaches_honest_readiness_from_a_separate_target_repo(tmp_path, real_toolrepo_sha):
    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    assert REPO_ROOT != repo and REPO_ROOT not in repo.resolve().parents, "target must be outside the toolrepo tree"
    profile_root = _make_trusted_profile_root(tmp_path)

    from app.agent_review.semantic_grouping_policy_v2 import (
        SemanticGroupingPolicyV2,
        SemanticGroupingRuleV2 as _Rule,
    )

    rule = _Rule(
        rule_id="all", semantic_group="primary_backend_logic", path_patterns=["*"],
        contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2({**material, "rules": [rule.model_dump(mode="json")]})
    grouping_policy = SemanticGroupingPolicyV2(**material, policy_sha256=digest)

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=grouping_policy,
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha=real_toolrepo_sha, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=prepared.content, manifest=prepared.manifest)

    grouping_policy_file = _grouping_policy_file(tmp_path)
    run_origin_file = _run_origin_file(tmp_path)
    checks_snapshot_file, toolchain_digest = _checks_snapshot_file(tmp_path)

    observation_before = _canonical_target_observation_v2(repo)

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--contract-version", "v2",
            "--repo-root", str(repo),
            "--target-profile", str(profile_root),
            "--grouping-policy", str(grouping_policy_file),
            "--base-sha", base_sha,
            "--head-sha", head_sha,
            "--tested-merge-sha", head_sha,
            "--pr-number", "1",
            "--toolrepo-sha", real_toolrepo_sha,
            "--evidence-hash", "d" * 64,
            "--max-lines-per-chunk", "1000",
            "--pr-state", "open",
            "--run-origin", str(run_origin_file),
            "--checks-snapshot", str(checks_snapshot_file),
            "--toolchain-digest", toolchain_digest,
            "--transport", "offline",
            "--offline-responses-dir", str(responses_dir),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    # No --output: the canonical result is on stdout, and stdout ONLY.
    readiness = json.loads(result.stdout)
    assert readiness["state"] == "manual_required"
    assert "policy_failure" in readiness["reason_codes"]

    observation_after = _canonical_target_observation_v2(repo)
    assert observation_after == observation_before, (
        "the CLI must never mutate the target checkout -- this includes "
        "tracked changes, new untracked files, and new files an ignore "
        "rule would otherwise hide"
    )


def test_cli_has_no_filesystem_output_authority(tmp_path, real_toolrepo_sha):
    """Structural control for the removed `--output` authority: the CLI
    accepts no destination-path argument at all, so it cannot be pointed
    at a path inside the target checkout. Piping stdout to a file OUTSIDE
    the target still works normally; nothing appears INSIDE the target."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)
    grouping_policy_file = _grouping_policy_file(tmp_path)
    run_origin_file = _run_origin_file(tmp_path)
    checks_snapshot_file, toolchain_digest = _checks_snapshot_file(tmp_path)

    # Precise structural proof, not a substring match against --help (whose
    # text is this module's own docstring and legitimately discusses the
    # removed authority in prose): with every REQUIRED argument supplied,
    # argparse must reject the extra, unknown --output flag specifically
    # (rather than reporting missing-required-arguments first, which would
    # not prove --output itself is unrecognized).
    rejected = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--contract-version", "v2",
            "--repo-root", str(repo),
            "--target-profile", str(profile_root),
            "--grouping-policy", str(grouping_policy_file),
            "--base-sha", base_sha,
            "--head-sha", head_sha,
            "--tested-merge-sha", head_sha,
            "--pr-number", "1",
            "--toolrepo-sha", real_toolrepo_sha,
            "--evidence-hash", "d" * 64,
            "--max-lines-per-chunk", "1000",
            "--pr-state", "open",
            "--run-origin", str(run_origin_file),
            "--checks-snapshot", str(checks_snapshot_file),
            "--toolchain-digest", toolchain_digest,
            "--transport", "offline",
            "--offline-responses-dir", str(tmp_path / "responses"),
            "--output", "/tmp/anything",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert rejected.returncode != 0
    assert "unrecognized arguments" in rejected.stderr and "--output" in rejected.stderr

    observation_before = _canonical_target_observation_v2(repo)

    external_output = tmp_path / "outside-the-target.json"
    with open(external_output, "w") as fh:
        result = subprocess.run(
            [
                sys.executable, str(CLI_SCRIPT),
                "--contract-version", "v2",
                "--repo-root", str(repo),
                "--target-profile", str(profile_root),
                "--grouping-policy", str(grouping_policy_file),
                "--base-sha", base_sha,
                "--head-sha", head_sha,
                "--tested-merge-sha", head_sha,
                "--pr-number", "1",
                "--toolrepo-sha", real_toolrepo_sha,
                "--evidence-hash", "d" * 64,
                "--max-lines-per-chunk", "1000",
                "--pr-state", "open",
                "--run-origin", str(run_origin_file),
                "--checks-snapshot", str(checks_snapshot_file),
                "--toolchain-digest", toolchain_digest,
                "--transport", "offline",
                "--offline-responses-dir", str(tmp_path / "responses"),
            ],
            cwd=str(REPO_ROOT),
            stdout=fh,
            stderr=subprocess.PIPE,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    readiness = json.loads(external_output.read_text(encoding="utf-8"))
    assert readiness["state"] == "manual_required"

    observation_after = _canonical_target_observation_v2(repo)
    assert observation_after == observation_before


def test_cli_process_refuses_with_wrong_toolrepo_sha(tmp_path):
    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)
    grouping_policy_file = _grouping_policy_file(tmp_path)
    run_origin_file = _run_origin_file(tmp_path)
    checks_snapshot_file, toolchain_digest = _checks_snapshot_file(tmp_path)

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--contract-version", "v2",
            "--repo-root", str(repo),
            "--target-profile", str(profile_root),
            "--grouping-policy", str(grouping_policy_file),
            "--base-sha", base_sha,
            "--head-sha", head_sha,
            "--tested-merge-sha", head_sha,
            "--pr-number", "1",
            "--toolrepo-sha", "a" * 40,
            "--evidence-hash", "d" * 64,
            "--max-lines-per-chunk", "1000",
            "--pr-state", "open",
            "--run-origin", str(run_origin_file),
            "--checks-snapshot", str(checks_snapshot_file),
            "--toolchain-digest", toolchain_digest,
            "--transport", "offline",
            "--offline-responses-dir", str(tmp_path / "responses"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "toolrepo_identity_mismatch" in result.stderr
    assert result.stdout == ""
