"""Base-owned authoritative-check policy (`#201-C0`, C0-3).

The property under test is not "YAML parses". It is that a pull request cannot
nominate itself as an authoritative producer, and that a required check can
never quietly end up with no declared source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_review.authoritative_check_policy_v2 import (
    POLICY_ENTRY_NOT_REQUIRED_REASON_V2,
    POLICY_INVALID_REASON_V2,
    POLICY_MISSING_REASON_V2,
    POLICY_PROFILE_MISMATCH_REASON_V2,
    POLICY_REQUIRED_CHECK_UNCOVERED_REASON_V2,
    POLICY_UNREADABLE_REASON_V2,
    AuthoritativeCheckPolicyErrorV2,
    ExecutedTreeRuleV2,
    compute_policy_semantic_digest_v2,
    load_authoritative_check_policy_v2,
    validate_policy_against_profile_v2,
)
from app.agent_review.profile_loader_v2 import load_target_profile_v2

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v2"
TARGETS = ("agent_escala", "interleitos")

VALID_POLICY = """\
schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: mglpsw/AgentEscala
authoritative_checks:
  - check_name: pytest
    workflow_path: .github/workflows/ci.yml
    job_name: Validate repository
    verifier_identity: github-actions
    producer_kind: sha_pinned_reusable_workflow
    producer_workflow:
      repository: mglpsw/aiops-orchestrator
      path: .github/workflows/authoritative-checks.reusable.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
"""


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".aiops").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".aiops" / "authoritative-checks.v2.yaml").write_text(text, encoding="utf-8")
    return tmp_path


# -- dual-target fixtures load and cross-validate ------------------------------


@pytest.mark.parametrize("target", TARGETS)
def test_shipped_fixtures_load_and_agree_with_their_profile(target: str) -> None:
    root = FIXTURES / target
    loaded = load_authoritative_check_policy_v2(root)
    profile = load_target_profile_v2(root)
    validate_policy_against_profile_v2(policy=loaded.policy, profile=profile)

    assert loaded.policy.identity.repo == profile.identity.repo
    assert {e.check_name for e in loaded.policy.authoritative_checks} == set(profile.policies.required_checks)


@pytest.mark.parametrize("target", TARGETS)
def test_both_digests_are_bare_hex(target: str) -> None:
    loaded = load_authoritative_check_policy_v2(FIXTURES / target)
    for digest in (loaded.policy_source_bytes_digest, loaded.policy_source_semantic_digest):
        assert len(digest) == 64 and not digest.startswith("sha256:")


def test_the_two_targets_do_not_share_a_policy_identity() -> None:
    """Dual-target proof: the engine branches on nothing -- the difference is
    entirely carried by the per-target instance."""

    digests = {
        load_authoritative_check_policy_v2(FIXTURES / t).policy_source_semantic_digest for t in TARGETS
    }
    assert len(digests) == len(TARGETS)


# -- the two digests answer different questions -------------------------------


def test_reformatting_moves_the_bytes_digest_only(tmp_path: Path) -> None:
    original = load_authoritative_check_policy_v2(_write(tmp_path / "a", VALID_POLICY))
    reformatted = load_authoritative_check_policy_v2(
        _write(tmp_path / "b", VALID_POLICY.replace("schema_version: 2", "schema_version:  2") + "\n")
    )

    assert original.policy_source_bytes_digest != reformatted.policy_source_bytes_digest
    assert original.policy_source_semantic_digest == reformatted.policy_source_semantic_digest


def test_a_semantic_change_moves_both_digests(tmp_path: Path) -> None:
    original = load_authoritative_check_policy_v2(_write(tmp_path / "a", VALID_POLICY))
    changed = load_authoritative_check_policy_v2(
        _write(tmp_path / "b", VALID_POLICY.replace("job_name: Validate repository", "job_name: something-else"))
    )

    assert original.policy_source_bytes_digest != changed.policy_source_bytes_digest
    assert original.policy_source_semantic_digest != changed.policy_source_semantic_digest


def test_semantic_digest_is_deterministic(tmp_path: Path) -> None:
    loaded = load_authoritative_check_policy_v2(_write(tmp_path, VALID_POLICY))
    assert compute_policy_semantic_digest_v2(loaded.policy) == loaded.policy_source_semantic_digest


# -- load-time fail-closed ----------------------------------------------------


def test_absent_policy_is_a_hard_failure(tmp_path: Path) -> None:
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(tmp_path)
    assert exc.value.reason_code == POLICY_MISSING_REASON_V2


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, "authoritative_checks: [unclosed"))
    assert exc.value.reason_code == POLICY_UNREADABLE_REASON_V2


def test_empty_check_list_is_refused(tmp_path: Path) -> None:
    text = VALID_POLICY.split("authoritative_checks:")[0] + "authoritative_checks: []\n"
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_duplicate_check_names_are_refused(tmp_path: Path) -> None:
    """Two entries for one check means two producers could satisfy it and the
    assembler would have to guess."""

    entry = VALID_POLICY.split("authoritative_checks:\n")[1]
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, VALID_POLICY + entry))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_unknown_field_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, VALID_POLICY + "surprise: 1\n"))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


@pytest.mark.parametrize("dropped", ["workflow_path", "job_name", "verifier_identity", "producer_kind"])
def test_every_identity_field_is_mandatory(tmp_path: Path, dropped: str) -> None:
    """A check name plus a workflow filename is not identity. Each field of the
    tuple has to be present or the entry does not load at all."""

    text = "\n".join(line for line in VALID_POLICY.splitlines() if not line.strip().startswith(f"{dropped}:"))
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text + "\n"))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_permitted_conclusions_cannot_be_widened(tmp_path: Path) -> None:
    """A target that could permit `neutral` or `skipped` could turn a
    non-verdict into a passing required check."""

    text = VALID_POLICY.replace("      - failure\n", "      - failure\n      - neutral\n")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_permitted_conclusions_cannot_drop_failure(tmp_path: Path) -> None:
    text = VALID_POLICY.replace("      - failure\n", "")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


# -- origin rules cannot be misdeclared ---------------------------------------


def test_pull_request_must_use_synthetic_merge_parentage(tmp_path: Path) -> None:
    text = VALID_POLICY.replace(
        "pull_request: synthetic_merge_parentage", "pull_request: explicit_tested_tree"
    )
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_other_origins_cannot_claim_synthetic_merge_semantics(tmp_path: Path) -> None:
    """`pull_request_target`, `manual` and `replay` do not run a synthetic
    merge commit. Letting a target say they do would reopen the tested-tree
    hole through the policy file."""

    text = VALID_POLICY + "      replay: synthetic_merge_parentage\n"
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_explicit_tested_tree_is_refused_at_load_time(tmp_path: Path) -> None:
    """Declarable in the type, not accepted in practice: no producer emits
    authenticated evidence of the tree it checked out, so accepting it would
    mean trusting the caller's own `--tested-merge-sha` echoed back. Refused
    when the policy is WRITTEN, so a target does not discover its policy cannot
    work only when a review silently never becomes ready."""

    text = VALID_POLICY + "      replay: explicit_tested_tree\n"
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


def test_origin_rules_cannot_be_empty(tmp_path: Path) -> None:
    text = VALID_POLICY.replace("      pull_request: synthetic_merge_parentage\n", "")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text + ""))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2


# -- cross-validation against the profile -------------------------------------


def test_repository_mismatch_is_refused(tmp_path: Path) -> None:
    loaded = load_authoritative_check_policy_v2(
        _write(tmp_path, VALID_POLICY.replace("mglpsw/AgentEscala", "mglpsw/somewhere-else"))
    )
    profile = load_target_profile_v2(FIXTURES / "agent_escala")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        validate_policy_against_profile_v2(policy=loaded.policy, profile=profile)
    assert exc.value.reason_code == POLICY_PROFILE_MISMATCH_REASON_V2


def test_a_required_check_with_no_declared_source_is_a_load_failure(tmp_path: Path) -> None:
    """Not "no source available" at verdict time -- a hard failure now. A
    required check that silently has nowhere authoritative to come from is
    exactly how a target ends up permanently inconclusive without noticing."""

    loaded = load_authoritative_check_policy_v2(
        _write(tmp_path, VALID_POLICY.replace("check_name: pytest", "check_name: mypy"))
    )
    profile = load_target_profile_v2(FIXTURES / "agent_escala")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        validate_policy_against_profile_v2(policy=loaded.policy, profile=profile)
    assert exc.value.reason_code in {
        POLICY_REQUIRED_CHECK_UNCOVERED_REASON_V2,
        POLICY_ENTRY_NOT_REQUIRED_REASON_V2,
    }


def test_an_entry_for_a_non_required_check_is_refused(tmp_path: Path) -> None:
    extra = VALID_POLICY + """\
  - check_name: mypy
    workflow_path: .github/workflows/ci.yml
    job_name: Validate repository
    verifier_identity: github-actions
    producer_kind: sha_pinned_reusable_workflow
    producer_workflow:
      repository: mglpsw/aiops-orchestrator
      path: .github/workflows/authoritative-checks.reusable.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
"""
    loaded = load_authoritative_check_policy_v2(_write(tmp_path, extra))
    profile = load_target_profile_v2(FIXTURES / "agent_escala")
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        validate_policy_against_profile_v2(policy=loaded.policy, profile=profile)
    assert exc.value.reason_code == POLICY_ENTRY_NOT_REQUIRED_REASON_V2






def test_the_producer_workflow_must_be_pinned_by_a_full_sha(tmp_path: Path) -> None:
    """Replaces the old base-owned-ref check, which Codex round 4 showed could
    never be satisfied by a real run. Immutability now comes from the SHA."""

    text = VALID_POLICY.replace('sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"', 'sha: "abc1234"')
    with pytest.raises(AuthoritativeCheckPolicyErrorV2) as exc:
        load_authoritative_check_policy_v2(_write(tmp_path, text))
    assert exc.value.reason_code == POLICY_INVALID_REASON_V2
