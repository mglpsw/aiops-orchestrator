from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import unittest.mock
from pathlib import Path

from app.agent_review.authoritative_check_policy_v2 import load_authoritative_check_policy_v2
from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.contracts_v2 import RequiredCheckResultV2, RunIdentityV2, RunOriginV2, compute_run_id
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.required_check_assembly_v2 import assemble_authoritative_ci_promotion_v2

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-quality-gate-v2.py"


@contextlib.contextmanager
def _ci_promotion_bypassing_independent_judge_gate():
    """Round-7 architectural correction:
    `assemble_authoritative_ci_promotion_v2` now refuses unconditionally at
    `verify_independent_semantic_judge_v2` (see that function's docstring in
    `authoritative_producer_evidence_v2`). This test file's fixture builder
    (`_write_fixtures`) calls the real assembler, IN THIS PARENT PROCESS, to
    produce a genuinely-derived `--checks`/`--checks-provenance` pair on disk
    for tests that are about the GATE's structural/collision/fail-closed
    behaviour, not about whether CI-sourced pytest is authoritative. Bypassed
    here so that fixture construction succeeds; the GATE SUBPROCESS itself is
    never patched by this (patching cannot cross a process boundary), so any
    test whose defect-under-test fires strictly BEFORE this gate inside the
    subprocess's own re-derivation still exercises real, unpatched production
    code end-to-end."""

    import app.agent_review.required_check_assembly_v2 as assembly_module

    with unittest.mock.patch.object(
        assembly_module, "verify_independent_semantic_judge_v2", lambda **_: None
    ):
        yield


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _write_target_profile(tmp_path: Path, *, required_checks: list[str] | None = None) -> Path:
    """A minimal, real, on-disk TargetProfileV2 fixture -- loaded through
    the real load_target_profile_v2, never hand-constructed in Python,
    matching #86's own established fixture discipline."""

    profile_root = tmp_path / "target_profile"
    aiops_dir = profile_root / ".aiops"
    aiops_dir.mkdir(parents=True)
    checks = required_checks if required_checks is not None else ["pytest"]
    checks_yaml = "\n".join(f"    - {name}" for name in checks)
    (aiops_dir / "target-profile.v2.yaml").write_text(
        f"""schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: mglpsw/aiops-orchestrator
  default_branch: master
artifacts: []
budgets:
  max_chunks: 16
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
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
{checks_yaml}
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: blocked_pipeline
  model_uncertainty_state: manual_required
contracts: []
limitations: []
""",
        encoding="utf-8",
    )
    entries = "\n".join(
        f"""  - check_name: {name}
    workflow_path: .github/workflows/authoritative-checks.yml
    job_name: authoritative {name}
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: mglpsw/aiops-orchestrator
      path: .github/workflows/authoritative-checks.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    producer_workflow_ref: refs/heads/master
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage"""
        for name in checks
    )
    (aiops_dir / "authoritative-checks.v2.yaml").write_text(
        f"""schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: mglpsw/aiops-orchestrator
authoritative_checks:
{entries}
""",
        encoding="utf-8",
    )
    return profile_root


def _identity_dict(*, profile_hash: str, **overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 130,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": profile_hash,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _ready_decision_dict(*, run_id: str, manifest_hash: str) -> dict[str, object]:
    return {
        "state": "ready",
        "reason_codes": [],
        "blockers": [],
        "coverage": {
            "status": "complete",
            "expected_files": ["app/a.py"],
            "reviewed_files": ["app/a.py"],
            "partially_reviewed_files": [],
            "missing_files": [],
            "must_review_files": ["app/a.py"],
            "missing_must_review_files": [],
            "degradation_causes": [],
        },
        "pipeline": {"degraded": False, "causes": []},
        "run_id": run_id,
        "manifest_hash": manifest_hash,
    }


def _write_fixtures(tmp_path: Path, *, required_checks: list[str] | None = None) -> dict[str, Path]:
    target_profile_root = _write_target_profile(tmp_path, required_checks=required_checks)
    profile = load_target_profile_v2(target_profile_root)
    profile_hash = compute_profile_hash_v2(profile)

    identity_dict = _identity_dict(profile_hash=profile_hash)
    identity_obj = RunIdentityV2.model_validate(identity_dict)
    run_id = compute_run_id(identity_obj)

    paths = {}
    paths["identity"] = tmp_path / "identity.json"
    paths["identity"].write_text(json.dumps(identity_dict), encoding="utf-8")
    paths["evaluated_identity"] = tmp_path / "evaluated_identity.json"
    paths["evaluated_identity"].write_text(json.dumps(identity_dict), encoding="utf-8")
    paths["decision"] = tmp_path / "decision.json"
    paths["decision"].write_text(
        json.dumps(_ready_decision_dict(run_id=run_id, manifest_hash=identity_dict["manifest_hash"])),
        encoding="utf-8",
    )
    paths["findings"] = tmp_path / "findings.json"
    paths["findings"].write_text(json.dumps([]), encoding="utf-8")
    check_names = required_checks if required_checks is not None else ["pytest"]

    # Build a REAL snapshot and derive the checks/provenance from it with the
    # real assembler, rather than hand-building either. A Codex review of this
    # PR found that hand-built pairs were accepted, so the gate now re-derives
    # from the snapshot -- which means the fixture has to produce evidence, not
    # assertions.
    paths["checks_snapshot"] = tmp_path / "snapshot.json"
    paths["checks_snapshot"].write_text(
        json.dumps(_snapshot_dict(check_names)), encoding="utf-8"
    )
    paths["run_origin"] = tmp_path / "origin.json"
    paths["run_origin"].write_text(
        json.dumps(
            {"event_type": "pull_request", "event_action": "synchronize", "delivery_id": "delivery-1"}
        ),
        encoding="utf-8",
    )

    loaded_policy = load_authoritative_check_policy_v2(target_profile_root)
    snapshot = parse_authoritative_ci_snapshot_v2(paths["checks_snapshot"].read_bytes())
    origin = RunOriginV2.model_validate(json.loads(paths["run_origin"].read_text(encoding="utf-8")))

    with _ci_promotion_bypassing_independent_judge_gate():
        promoted = [
            assemble_authoritative_ci_promotion_v2(
                check_name=name,
                snapshot=snapshot,
                loaded_policy=loaded_policy,
                identity=identity_obj,
                origin=origin,
                toolchain_digest=TOOLCHAIN_DIGEST,
            )
            for name in check_names
        ]

    paths["checks"] = tmp_path / "checks.json"
    paths["checks"].write_text(
        json.dumps([p.result.model_dump(mode="json") for p in promoted]), encoding="utf-8"
    )
    paths["checks_provenance"] = tmp_path / "checks_provenance.json"
    paths["checks_provenance"].write_text(
        json.dumps([p.provenance.model_dump(mode="json") for p in promoted]), encoding="utf-8"
    )
    paths["target_profile"] = target_profile_root
    return paths


TOOLCHAIN_DIGEST = "e" * 64


def _reseal_attestation(fields: dict) -> dict:
    """Re-digest a mutated attestation so it is WELL-FORMED but wrong.

    Without this a mutation would be caught by the self-digest, and the test
    would prove only that tampering is detected -- not that a correctly-sealed
    attestation describing a different base is still refused."""

    from app.agent_review.authoritative_producer_evidence_v2 import (
        ProducerAttestationV2,
        compute_producer_attestation_digest_v2,
    )

    material = {key: value for key, value in fields.items() if key != "attestation_digest"}
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**material, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**material, attestation_digest=digest).model_dump(mode="json")


def _gate_attestation(check_name: str, outcome: str = "success") -> dict:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        ProducerAttestationV2,
        compute_producer_attestation_digest_v2,
    )

    fields: dict = {
        "schema_id": "agent-review.producer-attestation.v2",
        "schema_version": 2,
        "source": "aiops-authoritative-check-producer",
        "repository": "mglpsw/aiops-orchestrator",
        "pr_number": 130,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "executed_sha": "3" * 40,
        "workflow_run_id": f"wf-{check_name}",
        "run_attempt": 1,
        "test_outcome": outcome,
        "check_execution_mode": "reexecuted_in_producer_run",
        "executed_sha_derivation": "verified_checkout_rev_parse",
        "policy_digest": "5" * 64,
        "toolchain_digest": "6" * 64,
    }
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**fields, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**fields, attestation_digest=digest).model_dump(mode="json")


def _observation(check_name: str, **overrides: object) -> dict:
    record: dict = {
        "repository": "mglpsw/aiops-orchestrator",
        "head_sha": "2" * 40,
        "check_run_id": f"run-{check_name}",
        "check_run_name": f"authoritative {check_name}",
        "status": "completed",
        "conclusion": "success",
        "app_slug": "github-actions",
        "workflow_path": ".github/workflows/authoritative-checks.yml",
        "workflow_execution_ref": "refs/heads/master",
        "workflow_repository": "mglpsw/aiops-orchestrator",
        "workflow_sha": "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96",
        "referenced_workflows": [],
        "producer_trigger": "workflow_run",
        "producer_attestation": _gate_attestation(check_name),
        "workflow_run_id": f"wf-{check_name}",
        "run_attempt": 1,
        "run_started_at": "2026-08-11T10:00:00Z",
        "run_event": "workflow_run",
        "run_base_sha": None,
        "run_head_sha": None,
    }
    record.update(overrides)
    return record


def _snapshot_dict(check_names: list[str], **overrides: object) -> dict:
    payload: dict = {
        "schema_id": "agent-review.authoritative-check-snapshot.v2",
        "schema_version": 2,
        "source": "aiops-acquire-authoritative-checks",
        "acquisition": {
            "acquired_by": "aiops-acquire-authoritative-checks-v2",
            "api_host": "api.github.com",
            "repository": "mglpsw/aiops-orchestrator",
            "head_sha": "2" * 40,
        },
        "observations": [_observation(name) for name in check_names],
        "tested_merge_sha": "3" * 40,
        "tested_merge_parents": ["1" * 40, "2" * 40],
        "observation_bytes_digest": "f" * 64,
    }
    payload.update(overrides)
    return payload


def _base_args(paths: dict[str, Path], output_path: Path, *, pr_state: str = "open") -> list[str]:
    return [
        "--contract-version", "v2",
        "--decision", str(paths["decision"]),
        "--identity", str(paths["identity"]),
        "--evaluated-identity", str(paths["evaluated_identity"]),
        "--findings", str(paths["findings"]),
        "--pr-state", pr_state,
        "--checks", str(paths["checks"]),
        "--checks-provenance", str(paths["checks_provenance"]),
        "--checks-snapshot", str(paths["checks_snapshot"]),
        "--run-origin", str(paths["run_origin"]),
        "--toolchain-digest", TOOLCHAIN_DIGEST,
        "--target-profile", str(paths["target_profile"]),
        "--output", str(output_path),
    ]


## Round-7 architectural correction, and its effect on this test file
## ---------------------------------------------------------------------------
## `assemble_authoritative_ci_promotion_v2` now refuses every subject-code
## check unconditionally (`required_check_provenance_independent_semantic_
## judge_required` -- see `authoritative_producer_evidence_v2`'s docstring).
## `_write_fixtures`'s DEFAULT fixture is pytest, CI-sourced, which was the
## only source_kind ever reachable through this gate's re-derivation:
## `TrustedHostPromotion` records are refused there unconditionally too, on a
## separate, pre-existing constraint (`#201-B3`'s executor has no CT104-backed
## producer yet). So `state: ready` is not reachable through the live gate
## SUBPROCESS today, for any source, in this environment -- consistent with
## `AUTHORITATIVE_PYTEST_PROMOTION=UNAVAILABLE_BY_DESIGN`.
##
## `_check_no_output_input_collision` -> payload/response/contract-version
## validation -> `_validate_required_check_provenance` all run BEFORE
## `emit_review_readiness_v2`, so a test whose OWN concern lies in one of the
## earlier stages still proves its point precisely by reaching the terminal
## independent-judge refusal rather than being rejected earlier for an
## unrelated reason: that is what `_assert_reached_the_independent_judge_gate`
## below checks. It is NOT a workaround for the correction; it is the correct
## updated assertion for "does this earlier stage still let a well-formed
## submission through".


def _assert_reached_the_independent_judge_gate(result: subprocess.CompletedProcess[str]) -> None:
    """The submission passed every check ahead of it in the pipeline, and was
    refused only by the terminal, architecturally-mandated gate -- not by the
    stage actually under test in the calling test."""

    assert result.returncode != 0
    assert "required_check_provenance_independent_semantic_judge_required" in result.stderr, result.stderr


def test_cli_emits_a_valid_ready_readiness_artifact(tmp_path: Path) -> None:
    """Was: proves a nominal invocation reaches `state: ready`. That terminal
    state is not reachable today for any source -- see the module note above.
    What is still real and still worth proving: a nominal invocation is not
    rejected by any EARLIER stage (collision guards, contract-version
    handling, decision binding, required-check coverage) -- it reaches the
    LAST gate, and is refused only there."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)
    assert not output_path.exists()


def test_cli_refuses_without_contract_version_v2(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"
    args = _base_args(paths, output_path, pr_state="open")
    args[args.index("v2")] = "v1"

    result = _run(args)

    assert result.returncode != 0
    assert not output_path.exists()


def test_cli_fails_closed_on_a_readiness_invariant_violation(tmp_path: Path) -> None:
    """ready requires an open PR -- a merged PR with a ready decision must
    never be silently accepted.

    `ReviewReadinessV2`'s own validator, which is what actually enforces
    this, is covered directly and unaffected by the round-7 correction: see
    `test_ready_state_with_a_merged_pr_fails_closed_via_the_contracts_own_
    validator` in `test_review_readiness_emission_v2.py` -- a file `#201-C0`
    is forbidden from touching. What THIS test can still prove, through the
    live CLI, is that a merged PR is refused no matter which stage catches it
    first: `_validate_required_check_provenance` now runs (and refuses)
    before `emit_review_readiness_v2` is ever reached, so the readiness
    invariant's own check is not reached via this path today -- but the
    submission is refused regardless, which is the property that matters end
    to end."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path, pr_state="merged"))

    _assert_reached_the_independent_judge_gate(result)
    assert not output_path.exists()


def test_cli_rejects_a_v1_payload_mixed_into_a_v2_gate_run(tmp_path: Path) -> None:
    """Closes the 'select_contract_version sem call site em produção' gap:
    a v1-shaped payload/response fed alongside --contract-version v2 is
    refused as mixed_contract_versions, zero conversion."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v1", "schema_version": 1}), encoding="utf-8"
    )
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    result = _run([*_base_args(paths, output_path), "--payload", str(payload_path), "--response", str(response_path)])

    assert result.returncode != 0
    assert not output_path.exists()
    assert "mixed_contract_versions" in result.stderr


def test_cli_accepts_a_matching_v2_payload_and_response(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v2", "schema_version": 2}), encoding="utf-8"
    )
    response_path = tmp_path / "response.json"
    response_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2}), encoding="utf-8"
    )

    result = _run([*_base_args(paths, output_path), "--payload", str(payload_path), "--response", str(response_path)])

    # A matching v2 payload/response is not what refuses this submission --
    # see the module note above the independent-judge-gate helper.
    _assert_reached_the_independent_judge_gate(result)


# -- Thread 2: decision-run binding --------------------------------------------


def test_cli_rejects_a_decision_replayed_from_a_different_run(tmp_path: Path) -> None:
    """Regression (Codex review of #145): a `ready` decision computed for
    run A combined with an unrelated run B's identity/findings/checks must
    be rejected, never silently combined into a valid artifact.

    The mechanism itself (`READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_
    REASON_V2`) is covered directly, in-process, against
    `emit_review_readiness_v2` --
    `test_emit_review_readiness_rejects_a_decision_replayed_from_a_different_run`
    in `test_review_readiness_emission_v2.py`, a file `#201-C0` is forbidden
    from touching. `_validate_required_check_provenance` now runs (and
    refuses) before `emit_review_readiness_v2` is ever reached, so THIS test
    proves the weaker, still-real property: a foreign decision is refused end
    to end via the live CLI regardless of which stage catches it."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    # Replace the decision with one bearing a run_id/manifest_hash that
    # does not correspond to the identity being emitted against.
    foreign_decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    foreign_decision["run_id"] = "9" * 64
    paths["decision"].write_text(json.dumps(foreign_decision), encoding="utf-8")

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)
    assert not output_path.exists()


def test_cli_rejects_a_decision_with_matching_run_id_but_divergent_manifest_hash(tmp_path: Path) -> None:
    """See `test_cli_rejects_a_decision_replayed_from_a_different_run` above:
    the mechanism is covered directly against `emit_review_readiness_v2` in
    `test_review_readiness_emission_v2.py`; this proves the live CLI still
    refuses end to end."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    foreign_decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    foreign_decision["manifest_hash"] = "9" * 64
    paths["decision"].write_text(json.dumps(foreign_decision), encoding="utf-8")

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)
    assert not output_path.exists()


def test_cli_rejects_a_decision_file_without_run_provenance(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    legacy_decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    del legacy_decision["run_id"]
    del legacy_decision["manifest_hash"]
    paths["decision"].write_text(json.dumps(legacy_decision), encoding="utf-8")

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "gate_input_invalid" in result.stderr


# -- Thread 3: required-checks completeness ------------------------------------


def test_cli_accepts_when_all_required_checks_are_present_and_green(tmp_path: Path) -> None:
    """Was: proves the gate accepts full coverage. Coverage completeness is
    not what refuses this submission now -- see the module note above."""

    paths = _write_fixtures(tmp_path, required_checks=["pytest", "mypy"])
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)


def test_cli_rejects_when_a_required_check_is_missing(tmp_path: Path) -> None:
    """Regression (Codex review of #145): a target requiring both pytest
    and mypy was previously satisfied by a submission containing only a
    green pytest -- the CLI never cross-checked against
    TargetPoliciesV2.required_checks at all."""

    paths = _write_fixtures(tmp_path, required_checks=["pytest", "mypy"])
    output_path = tmp_path / "out" / "readiness.json"

    # Submit only "pytest", omitting the required "mypy".
    paths["checks"].write_text(
        json.dumps(
            [{"check_name": "pytest", "required": True, "deterministic": True, "conclusion": "success", "head_sha": "2" * 40}]
        ),
        encoding="utf-8",
    )

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "gate_required_check_missing" in result.stderr


def test_cli_rejects_a_target_profile_with_a_diverging_hash(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    # Point --target-profile at a DIFFERENT, real profile (different
    # required_checks -> different profile_hash) than the one the
    # identity's own profile_hash was computed from.
    different_profile_root = _write_target_profile(tmp_path / "other", required_checks=["pytest", "flake8"])

    args = _base_args(paths, output_path)
    args[args.index("--target-profile") + 1] = str(different_profile_root)
    result = _run(args)

    assert result.returncode != 0
    assert not output_path.exists()
    assert "gate_profile_identity_mismatch" in result.stderr


def test_cli_extra_check_does_not_substitute_a_missing_required_check(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path, required_checks=["pytest", "mypy"])
    output_path = tmp_path / "out" / "readiness.json"

    # Submit "pytest" and an unrelated extra check, still omitting "mypy".
    paths["checks"].write_text(
        json.dumps(
            [
                {"check_name": "pytest", "required": True, "deterministic": True, "conclusion": "success", "head_sha": "2" * 40},
                {"check_name": "lint-extra", "required": True, "deterministic": True, "conclusion": "success", "head_sha": "2" * 40},
            ]
        ),
        encoding="utf-8",
    )

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "gate_required_check_missing" in result.stderr


# -- Thread 4: one-sided version-gate inputs -----------------------------------


def test_cli_accepts_an_isolated_v2_payload(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v2", "schema_version": 2}), encoding="utf-8"
    )

    result = _run([*_base_args(paths, output_path), "--payload", str(payload_path)])

    # An isolated v2 payload is not what refuses this submission -- see the
    # module note above the independent-judge-gate helper.
    _assert_reached_the_independent_judge_gate(result)


def test_cli_rejects_an_isolated_v1_payload(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v1", "schema_version": 1}), encoding="utf-8"
    )

    result = _run([*_base_args(paths, output_path), "--payload", str(payload_path)])

    assert result.returncode != 0
    assert not output_path.exists()


def test_cli_rejects_a_response_supplied_without_its_payload(tmp_path: Path) -> None:
    """Regression (Codex review of #145): --response without --payload
    previously bypassed the version gate silently instead of being
    rejected as an invalid, unrepresentable combination."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    response_path = tmp_path / "response.json"
    response_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2}), encoding="utf-8"
    )

    result = _run([*_base_args(paths, output_path), "--response", str(response_path)])

    assert result.returncode != 0
    assert not output_path.exists()
    assert "gate_response_without_payload" in result.stderr


def test_cli_valid_flow_with_neither_payload_nor_response(tmp_path: Path) -> None:
    """Was: proves the gate accepts a nominal flow with neither optional
    input. That is not what refuses this submission now -- see the module
    note above the independent-judge-gate helper."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)


# -- Thread 5: output/input collision ------------------------------------------


def test_cli_rejects_output_colliding_with_each_file_input(tmp_path: Path) -> None:
    """Regression (Codex review of #145): --output resolving to the same
    file as an input previously read every input first and then silently
    overwrote that source artifact with the readiness JSON, returning
    success. Parametrized over every relevant single-file input,
    confirming the original file's bytes are untouched after the
    rejection."""

    for colliding_key in ("decision", "identity", "checks"):
        paths = _write_fixtures(tmp_path / colliding_key)
        colliding_path = paths[colliding_key]
        original_bytes = colliding_path.read_bytes()

        result = _run(_base_args(paths, colliding_path))

        assert result.returncode != 0, colliding_key
        assert "gate_output_overwrites_input" in result.stderr, colliding_key
        assert colliding_path.read_bytes() == original_bytes, colliding_key


def test_cli_rejects_output_colliding_with_target_profile_path(tmp_path: Path) -> None:
    """--target-profile is a directory, not a single file, but the same
    resolved-path collision check must still catch --output pointing at
    that exact path (a write there would fail against a directory anyway,
    but the rejection must happen BEFORE any input is even read, per the
    same reason code as every other input)."""

    paths = _write_fixtures(tmp_path)
    result = _run(_base_args(paths, paths["target_profile"]))

    assert result.returncode != 0
    assert "gate_output_overwrites_input" in result.stderr


def test_cli_rejects_output_colliding_with_the_target_profiles_nested_file(tmp_path: Path) -> None:
    """Codex review of PR #156 -- --target-profile is a repo-root directory,
    but load_target_profile_v2 actually reads
    <target-profile>/.aiops/target-profile.v2.yaml underneath it. The
    collision check must catch --output pointing at THAT nested file too,
    not just the bare root -- otherwise the CLI reads the real profile,
    computes readiness, and then silently overwrites the profile source on
    the final write."""

    paths = _write_fixtures(tmp_path)
    profile_file = paths["target_profile"] / ".aiops" / "target-profile.v2.yaml"
    original_bytes = profile_file.read_bytes()

    result = _run(_base_args(paths, profile_file))

    assert result.returncode != 0
    assert "gate_output_overwrites_input" in result.stderr
    assert profile_file.read_bytes() == original_bytes


def test_cli_rejects_output_colliding_with_optional_payload(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v2", "schema_version": 2}), encoding="utf-8"
    )
    original_bytes = payload_path.read_bytes()

    result = _run([*_base_args(paths, payload_path), "--payload", str(payload_path)])

    assert result.returncode != 0
    assert "gate_output_overwrites_input" in result.stderr
    assert payload_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# #201-C0 -- authorised provenance is mandatory (#217)
# ---------------------------------------------------------------------------


def test_cli_requires_checks_provenance(tmp_path: Path) -> None:
    """Fail-closed by construction: omitting the argument is an argparse
    error, not a silent legacy path that reinstates the bypass."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"
    args = [a for a in _base_args(paths, output_path)]
    index = args.index("--checks-provenance")
    del args[index : index + 2]

    result = _run(args)

    assert result.returncode != 0
    assert "--checks-provenance" in result.stderr


def test_cli_rejects_a_hand_built_green_check_with_no_provenance(tmp_path: Path) -> None:
    """The #217 attack end to end: an object named `pytest` with
    `conclusion=success` used to satisfy the gate by name alone."""

    paths = _write_fixtures(tmp_path)
    paths["checks_provenance"].write_text(json.dumps([]), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance" in result.stderr
    assert not output_path.exists()


def test_cli_rejects_provenance_naming_the_right_check_but_a_different_result(tmp_path: Path) -> None:
    """Name coincidence is not coverage. The submitted check is flipped to
    `failure` while the sidecar still describes the green one, so the digests
    no longer agree even though `check_name` does."""

    paths = _write_fixtures(tmp_path)
    checks = json.loads(paths["checks"].read_text(encoding="utf-8"))
    checks[0]["conclusion"] = "failure"
    paths["checks"].write_text(json.dumps(checks), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance_missing" in result.stderr


def test_cli_rejects_provenance_from_a_different_run(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    provenance = json.loads(paths["checks_provenance"].read_text(encoding="utf-8"))
    provenance[0]["run_id"] = "9" * 64
    paths["checks_provenance"].write_text(json.dumps(provenance), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    # The self-digest catches the edit before the run binding is even reached.
    assert "gate_input_invalid" in result.stderr or "required_check_provenance" in result.stderr


def test_cli_rejects_a_non_allowlisted_producer(tmp_path: Path) -> None:
    """Attack the EVIDENCE, not the sidecar. Now that the gate re-derives, a
    forged sidecar is caught generically; the interesting case is a snapshot
    whose observed producer is not the one the base-owned policy names."""

    paths = _write_fixtures(tmp_path)
    snapshot = _snapshot_dict(["pytest"])
    snapshot["observations"][0]["app_slug"] = "attacker-app"
    paths["checks_snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance_missing" in result.stderr
    assert not output_path.exists()


def test_cli_rejects_a_workflow_the_policy_does_not_name(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    snapshot = _snapshot_dict(["pytest"])
    snapshot["observations"][0]["workflow_path"] = ".github/workflows/attacker.yml"
    paths["checks_snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance_missing" in result.stderr


def test_cli_rejects_a_fabricated_green_with_a_self_consistent_sidecar(tmp_path: Path) -> None:
    """The exact defect a Codex review of this PR found.

    The submitted check claims success while the evidence says failure. The
    sidecar is rebuilt to be internally consistent with that claim -- correct
    self-digest, correct run identity, correct policy digests, correct producer
    strings. Before re-derivation this passed every structural check. It must
    now fail, because the evidence does not produce it."""

    paths = _write_fixtures(tmp_path)
    snapshot = _snapshot_dict(["pytest"])
    snapshot["observations"][0]["conclusion"] = "failure"
    snapshot["observations"][0]["producer_attestation"] = _gate_attestation("pytest", outcome="failure")
    paths["checks_snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance" in result.stderr
    assert not output_path.exists()


def test_cli_rejects_a_run_produced_against_a_different_base(tmp_path: Path) -> None:
    """Second Codex finding: the base can advance without the head moving, so
    a green from the previous base plus a fresh local merge would otherwise
    line up perfectly."""

    paths = _write_fixtures(tmp_path)
    snapshot = _snapshot_dict(["pytest"])
    # The run's own base/head are only meaningful for a PR-triggered producer;
    # a `workflow_run`'s head is the default branch. This test is about that
    # mechanism, so it uses the topology the mechanism guards -- which is
    # refused for staleness here, before it is refused for being PR-writable.
    observation = snapshot["observations"][0]
    observation["producer_trigger"] = "pull_request"
    observation["run_event"] = "pull_request"
    observation["run_head_sha"] = "2" * 40
    observation["run_base_sha"] = "7" * 40
    paths["checks_snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance_observation_stale" in result.stderr


def test_cli_rejects_a_base_owned_producer_attesting_a_different_base(tmp_path: Path) -> None:
    """The same defect for the promotable topology.

    A `workflow_run` producer has no meaningful base/head of its own, so the
    binding to this review's base comes from the attestation instead. A green
    attested against a previous base is refused there rather than slipping
    through because the run-level check does not apply."""

    paths = _write_fixtures(tmp_path)
    snapshot = _snapshot_dict(["pytest"])
    attestation = snapshot["observations"][0]["producer_attestation"]
    attestation["base_sha"] = "7" * 40
    snapshot["observations"][0]["producer_attestation"] = _reseal_attestation(attestation)
    paths["checks_snapshot"].write_text(json.dumps(snapshot), encoding="utf-8")
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "required_check_provenance_producer_attestation_mismatch" in result.stderr


def test_cli_rejects_a_missing_authoritative_check_policy(tmp_path: Path) -> None:
    """A target profile with no policy beside it cannot legitimate anything."""

    paths = _write_fixtures(tmp_path)
    (paths["target_profile"] / ".aiops" / "authoritative-checks.v2.yaml").unlink()
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert "authoritative_check_policy_missing" in result.stderr


def test_cli_rejects_output_colliding_with_the_authoritative_check_policy(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    policy_file = paths["target_profile"] / ".aiops" / "authoritative-checks.v2.yaml"
    original_bytes = policy_file.read_bytes()

    result = _run(_base_args(paths, policy_file))

    assert result.returncode != 0
    assert "gate_output_overwrites_input" in result.stderr
    assert policy_file.read_bytes() == original_bytes


def test_cli_no_longer_emits_for_a_subject_code_check_pending_an_independent_judge(
    tmp_path: Path,
) -> None:
    """Was `test_cli_still_emits_when_provenance_is_authorised`, asserting
    "the hardening must not make the legitimate path unreachable" -- true
    under round 4's model, false under the round-7 architectural correction.

    An independent audit found that `base_owned_workflow_run` +
    `reexecuted_in_producer_run` re-runs the PULL REQUEST'S OWN test suite
    and reports its exit code: the subject still controls the
    success_signal, so `#201-B3`'s theorem
    (`controls(subject, success_signal) => not authoritative(success_signal)`)
    still applies regardless of the workflow definition's base-ownership. The
    round-7 acceptance condition this test encoded is REVOKED, not
    reinterpreted -- see `authoritative_producer_evidence_v2`'s module
    docstring.

    `AUTHORITATIVE_PYTEST_PROMOTION=UNAVAILABLE_BY_DESIGN`: this is the
    regression test for that state remaining true. If this test ever starts
    failing because the gate emits `ready` again, that is a signal an
    independent-judge producer was added -- which is real, welcome progress,
    but it means THIS test needs to be re-ratified deliberately, not patched
    silently to keep it green."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    _assert_reached_the_independent_judge_gate(result)


