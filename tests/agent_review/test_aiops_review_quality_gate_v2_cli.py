from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-quality-gate-v2.py"


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
    paths["checks"] = tmp_path / "checks.json"
    paths["checks"].write_text(
        json.dumps(
            [
                {"check_name": name, "required": True, "deterministic": True, "conclusion": "success", "head_sha": "2" * 40}
                for name in (required_checks if required_checks is not None else ["pytest"])
            ]
        ),
        encoding="utf-8",
    )
    paths["target_profile"] = target_profile_root
    return paths


def _base_args(paths: dict[str, Path], output_path: Path, *, pr_state: str = "open") -> list[str]:
    return [
        "--contract-version", "v2",
        "--decision", str(paths["decision"]),
        "--identity", str(paths["identity"]),
        "--evaluated-identity", str(paths["evaluated_identity"]),
        "--findings", str(paths["findings"]),
        "--pr-state", pr_state,
        "--checks", str(paths["checks"]),
        "--target-profile", str(paths["target_profile"]),
        "--output", str(output_path),
    ]


def test_cli_emits_a_valid_ready_readiness_artifact(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    assert doc["state"] == "ready"


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
    be refused by ReviewReadinessV2's own validator, never silently
    accepted."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path, pr_state="merged"))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "readiness_invariant_violation" in result.stderr


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

    assert result.returncode == 0, result.stderr
    assert output_path.exists()


# -- Thread 2: decision-run binding --------------------------------------------


def test_cli_rejects_a_decision_replayed_from_a_different_run(tmp_path: Path) -> None:
    """Regression (Codex review of #145): a `ready` decision computed for
    run A combined with an unrelated run B's identity/findings/checks must
    be rejected, never silently combined into a valid artifact."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    # Replace the decision with one bearing a run_id/manifest_hash that
    # does not correspond to the identity being emitted against.
    foreign_decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    foreign_decision["run_id"] = "9" * 64
    paths["decision"].write_text(json.dumps(foreign_decision), encoding="utf-8")

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "readiness_emission_decision_provenance_mismatch" in result.stderr


def test_cli_rejects_a_decision_with_matching_run_id_but_divergent_manifest_hash(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    foreign_decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    foreign_decision["manifest_hash"] = "9" * 64
    paths["decision"].write_text(json.dumps(foreign_decision), encoding="utf-8")

    result = _run(_base_args(paths, output_path))

    assert result.returncode != 0
    assert not output_path.exists()
    assert "readiness_emission_decision_provenance_mismatch" in result.stderr


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
    paths = _write_fixtures(tmp_path, required_checks=["pytest", "mypy"])
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode == 0, result.stderr


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

    assert result.returncode == 0, result.stderr


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
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(_base_args(paths, output_path))

    assert result.returncode == 0, result.stderr


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
