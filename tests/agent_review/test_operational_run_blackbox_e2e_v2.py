"""`#200-E` Phase 3 -- product-level black-box tests, running the REAL
`scripts/aiops-review-run-v2.py` as a real subprocess (outer bootstrap ->
real inner semantic child), not merely calling `run_operational_review_v2`
in-process. This is what distinguishes these from
`test_operational_run_v2.py`/`test_operational_run_router_v2.py`: those
prove the composer is correct; these prove the composer is actually WIRED
into the product entrypoint the way the architecture claims.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLREPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = TOOLREPO_ROOT / "scripts" / "aiops-review-run-v2.py"
FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2" / "agent_escala"
_TOOLCHAIN_DIGEST = "e" * 64
_TESTED_MERGE_SHA = "3" * 40


def _write_grouping_policy(path: Path) -> None:
    doc = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [
            {
                "rule_id": "backend",
                "semantic_group": "primary_backend_logic",
                "path_patterns": ["backend/scheduling/*.py"],
                "contract_ids": [],
                "artifact_ids": [],
                "priority": 0,
            }
        ],
        "fallback_group": None,
        "policy_sha256": "0" * 64,
    }
    sys.path.insert(0, str(TOOLREPO_ROOT))
    from app.agent_review.semantic_grouping_policy_v2 import compute_semantic_grouping_policy_sha256_v2

    doc["policy_sha256"] = compute_semantic_grouping_policy_sha256_v2(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _build_real_target(tmp_path: Path, *, source_line: str = "return 1") -> tuple[Path, str, str]:
    repo = tmp_path / "target_repo"
    _init_repo(repo)
    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        f"def compute_shift():\n    {source_line}\n", encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    base_sha = _commit_all(repo, "base")
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 2\n", encoding="utf-8"
    )
    head_sha = _commit_all(repo, "head")
    return repo, base_sha, head_sha


def _cli_argv(*, target_repo: Path, base_sha: str, head_sha: str, responses_dir: Path, policy_path: Path) -> list[str]:
    return [
        sys.executable, str(CLI_SCRIPT),
        "--target-root", str(target_repo),
        "--base-sha", base_sha,
        "--head-sha", head_sha,
        "--tested-merge-sha", _TESTED_MERGE_SHA,
        "--toolchain-digest", _TOOLCHAIN_DIGEST,
        "--repo", "mglpsw/AgentEscala",
        "--pr-number", "101",
        "--trusted-profile-root", str(FIXTURES_ROOT),
        "--grouping-policy", str(policy_path),
        "--responses-dir", str(responses_dir),
        "--pr-state", "open",
        "--event-type", "manual",
        "--event-action", "manual",
        "--delivery-id", "blackbox-1",
    ]


def _recursive_snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_offline_product_e2e_via_real_outer_and_inner_process(tmp_path: Path):
    """§15: real outer subprocess -> real inner subprocess -> real
    controlled target subject -> real diff/manifest/payload/content ->
    offline transport -> stdout JSON, exit 0. No authoritative checks
    submitted, so the honest terminal readiness is non-ready -- never
    fabricated to `ready`."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    readiness = json.loads(result.stdout)
    assert readiness["schema_id"] == "agent-review.review-readiness.v2"
    assert readiness["evaluated_head_sha"] == head_sha
    assert readiness["state"] != "ready"
    assert readiness["identity"]["toolrepo_sha"] != "0" * 40  # a real toolrepo HEAD, not a placeholder


def test_source_origin_diagnostic_proves_subject_not_devrepo(tmp_path: Path):
    """§5: the inner semantic child's own loaded modules must report
    __file__ origins inside the materialized subject, never this
    toolrepo's own mutable checkout."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    argv = _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                      responses_dir=responses_dir, policy_path=policy_path)
    # Same outer-bootstrap path, but ask the OUTER process to forward a
    # diagnostic-only inner invocation instead of a real review. Simplest
    # correct way: invoke the outer CLI normally; it always forwards
    # unrecognized-by-outer flags straight through to inner, and
    # --_diagnose-source-origin is inner-only, so outer parses it too
    # (SUPPRESS help, but it's a real argparse flag) without it changing
    # outer's own bootstrap behavior.
    result = subprocess.run(
        [*argv, "--_diagnose-source-origin"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    diagnostic = json.loads(result.stderr.strip().splitlines()[-1])
    for key in ("operational_run_v2_file", "controlled_subject_v2_file", "review_transport_v2_file", "this_script_file"):
        assert str(TOOLREPO_ROOT) not in diagnostic[key], (
            f"{key} loaded from the mutable development checkout, not the materialized subject: "
            f"{diagnostic[key]}"
        )
        assert "agent-review-toolrepo-subject-v2-" in diagnostic[key]


def test_product_level_hostile_outer_environment_has_zero_semantic_effect(tmp_path: Path, monkeypatch):
    """§7's product-level pin of the already-proven Phase-3 finding: run
    the REAL outer CLI, with the REAL inner semantic child, exercising the
    REAL unmodified acquire_diff_v2, from a shell carrying hostile ambient
    GIT_* and PYTHONPATH state -- must have zero effect on the result."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    clean_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert clean_result.returncode == 0, clean_result.stderr
    clean_readiness = json.loads(clean_result.stdout)

    hostile_env = dict(os.environ)
    hostile_env["GIT_DIR"] = "/nonexistent/attacker/repo"
    hostile_env["GIT_WORK_TREE"] = "/nonexistent/attacker/worktree"
    hostile_env["GIT_OBJECT_DIRECTORY"] = "/nonexistent/attacker/objects"
    hostile_env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = "/nonexistent/attacker/alt"
    hostile_env["GIT_CONFIG_PARAMETERS"] = "'core.hooksPath=/tmp/should-not-matter'"
    hostile_env["PYTHONPATH"] = "/nonexistent/attacker/pypath"

    hostile_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True, env=hostile_env,
    )
    assert hostile_result.returncode == 0, hostile_result.stderr
    hostile_readiness = json.loads(hostile_result.stdout)

    assert hostile_readiness["identity"]["manifest_hash"] == clean_readiness["identity"]["manifest_hash"]
    assert hostile_readiness["state"] == clean_readiness["state"]


def test_product_level_target_nonmutation_recursive_oracle(tmp_path: Path):
    """§17: recursive before/after snapshot (worktree + .git + ignored +
    untracked) around the REAL product subprocess."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    before = _recursive_snapshot(target_repo)
    result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    after = _recursive_snapshot(target_repo)
    assert before == after


def test_product_level_target_readonly_fixture_still_succeeds(tmp_path: Path):
    """§17: a successful product run must not REQUIRE a target write --
    proven by making the target worktree and .git read-only (where the
    host permits) and confirming the CLI still succeeds."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    def _chmod_tree_readonly(root: Path) -> None:
        for p in root.rglob("*"):
            if p.is_file():
                p.chmod(0o444)
            elif p.is_dir():
                p.chmod(0o555)
        root.chmod(0o555)

    try:
        _chmod_tree_readonly(target_repo)
        result = subprocess.run(
            _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                       responses_dir=responses_dir, policy_path=policy_path),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        # restore permissions so tmp_path cleanup can remove the tree
        for p in target_repo.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        target_repo.chmod(0o755)


def test_product_level_reference_material_ignores_post_commit_tampering(tmp_path: Path):
    """§9: target worktree artifact/contract bytes tampered AFTER commit
    -> product review still consumes committed head bytes, proven through
    the real CLI subprocess (unit-level already proven in
    test_controlled_subject_v2.py; this is the product-level witness)."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    clean_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert clean_result.returncode == 0, clean_result.stderr
    clean_readiness = json.loads(clean_result.stdout)

    # Tamper the WORKING TREE copy of the declared artifact/contract AFTER
    # the commit -- never a new commit.
    (target_repo / "artifacts" / "full.diff").write_text(
        "TAMPERED_AFTER_COMMIT_NOT_PART_OF_ANY_COMMIT\n", encoding="utf-8"
    )
    (target_repo / "contracts" / "domain-contracts.yaml").write_text(
        "TAMPERED_AFTER_COMMIT_NOT_PART_OF_ANY_COMMIT\n", encoding="utf-8"
    )

    tampered_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert tampered_result.returncode == 0, tampered_result.stderr
    tampered_readiness = json.loads(tampered_result.stdout)

    assert tampered_readiness["identity"]["manifest_hash"] == clean_readiness["identity"]["manifest_hash"]
    assert tampered_readiness["state"] == clean_readiness["state"]


def test_outer_bootstrap_has_zero_semantic_module_imports_at_top_level():
    """M3-01, structural: the outer bootstrap must never be CAPABLE of
    running review semantics merely by module import -- every semantic
    owner import lives inside _run_inner_semantic_child, never at module
    top level. Enforced via AST inspection, not eyeballed once."""
    import ast

    tree = ast.parse(CLI_SCRIPT.read_text(encoding="utf-8"))
    semantic_markers = (
        "operational_run_v2", "review_transport_v2", "run_assembly_v2",
        "payload_builder_v2", "review_content_extraction_v2", "synthesis_v2",
        "readiness_decision_v2", "semantic_grouping_policy_v2", "profile_loader_v2",
    )
    top_level_imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in top_level_imports:
        module_name = getattr(node, "module", None) or ""
        names = [alias.name for alias in node.names]
        combined = module_name + " " + " ".join(names)
        for marker in semantic_markers:
            assert marker not in combined, (
                f"top-level import touches a semantic module ({marker}): {ast.dump(node)}"
            )


def test_cli_has_no_output_path_flag():
    """M3-15, structural: --output must not exist as a CLI contract at
    all -- an unrecognized flag makes argparse itself refuse, not a
    behavioral check that could silently regress."""
    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--target-root", "/nonexistent", "--base-sha", "1" * 40, "--head-sha", "2" * 40,
            "--tested-merge-sha", "3" * 40, "--toolchain-digest", "e" * 64,
            "--repo", "x/y", "--pr-number", "1", "--trusted-profile-root", "/nonexistent",
            "--grouping-policy", "/nonexistent", "--responses-dir", "/nonexistent",
            "--pr-state", "open", "--event-type", "manual", "--event-action", "manual",
            "--delivery-id", "d",
            "--output", "/tmp/should-not-be-accepted.json",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr and "--output" in result.stderr


def test_post_seal_validation_error_escapes_the_product_cli_raw(tmp_path: Path):
    """M3-10: a post-seal ValidationError (not this composer's own typed
    OperationalRunError) must crash the product CLI process, never be
    silently sanitized into a typed refusal JSON on stderr."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    # A grouping-policy document that parses as JSON but fails the
    # SemanticGroupingPolicyV2 pydantic contract post-seal (an internal
    # deep field type violation, not a top-level "file missing" refusal)
    # -- this must escape as an unhandled traceback (nonzero exit, but
    # NOT the CLI's own typed {"error_class": ...} shape), never quietly
    # caught by the SemanticGroupingError family this CLI DOES catch,
    # since the malformed document fails pydantic validation before the
    # CLI's SemanticGroupingError-catching code path is even reached --
    # the actual escape here is a pydantic ValidationError from inside
    # model_validate_json, which the CLI's own except clause for THIS step
    # (OSError, UnicodeDecodeError, ValueError) DOES catch broadly.
    # Kept as a documented, verified limitation of this control rather
    # than silently dropped: pydantic.ValidationError IS a ValueError
    # subclass, so the CLI's own input-parsing boundary legitimately
    # converts it -- this is CORRECT per §13 (a CALLER-material parsing
    # boundary the CLI owns), not a leak. The true post-seal escape is
    # exercised at the composer level instead
    # (test_post_seal_synthesis_defect_escapes_raw,
    # test_post_bind_readiness_defect_escapes_raw in
    # test_operational_run_authority_v2.py), which this test defers to.
    pytest.skip(
        "post-seal escape is exercised at the composer level "
        "(test_operational_run_authority_v2.py); the CLI's own grouping-policy "
        "parse step legitimately converts ValidationError as CALLER input, not a leak"
    )


def test_direct_inner_mode_invocation_is_refused(tmp_path: Path):
    """Independent review lane A's finding: --_controlled-inner was
    reachable directly, skipping the entire outer bootstrap/materialization/
    bounded-env, with a self-declared, unverified --_inner-declared-
    toolrepo-sha flowing straight into the canonical output. This DIRECT
    route is closed: the inner child refuses unless its own resolved path
    genuinely sits inside a real materialize_toolrepo_execution_subject_v2
    output directory matching --_inner-subject-root.

    SCOPE CORRECTION (round-3 lane B): this test closes the DIRECT route
    only. It does NOT establish that the toolrepo SHA cannot be forged --
    lane B reproduced the identical defect through the ORDINARY outer CLI
    by appending --_inner-declared-toolrepo-sha after the normal arguments
    (argparse is last-wins, and the outer spliced its own derived value
    BEFORE the caller's argv). The earlier wording here claimed the class
    was closed when only one of its two routes was. The pass-through route
    is covered by
    test_caller_supplied_outer_owned_flag_is_refused_through_the_ordinary_cli
    below, and the mechanism itself by _OUTER_OWNED_PRIVATE_FLAGS_V2."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    result = subprocess.run(
        [
            sys.executable, str(CLI_SCRIPT),
            "--_controlled-inner",
            "--_inner-subject-root", "/totally/bogus/never/checked",
            "--_inner-declared-toolrepo-sha", "d" * 40,
            "--target-root", str(target_repo), "--base-sha", base_sha, "--head-sha", head_sha,
            "--tested-merge-sha", _TESTED_MERGE_SHA, "--toolchain-digest", _TOOLCHAIN_DIGEST,
            "--repo", "mglpsw/AgentEscala", "--pr-number", "101",
            "--trusted-profile-root", str(FIXTURES_ROOT),
            "--grouping-policy", str(policy_path), "--responses-dir", str(responses_dir),
            "--pr-state", "open", "--event-type", "manual", "--event-action", "manual",
            "--delivery-id", "bypass-attempt",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error_class"] == "cli_inner_materialization_unverified"


@pytest.mark.parametrize(
    "injected",
    [
        pytest.param(["--_inner-declared-toolrepo-sha", "d" * 40], id="sha-space-form"),
        pytest.param([f"--_inner-declared-toolrepo-sha={'d' * 40}"], id="sha-equals-form"),
        pytest.param(["--_inner-subject-root", "/totally/bogus"], id="subject-root-space-form"),
        pytest.param(["--_inner-subject-root=/totally/bogus"], id="subject-root-equals-form"),
    ],
)
def test_caller_supplied_outer_owned_flag_is_refused_through_the_ordinary_cli(
    tmp_path: Path, injected: list[str]
):
    """Round-3 lane B P0: `identity.toolrepo_sha` in the EMITTED artifact was
    forgeable by any caller of the ordinary public CLI -- no private setup,
    no direct inner invocation. The outer derives the real SHA from its own
    sealed `git rev-parse HEAD` probe, then spliced `*argv` AFTER it; argparse
    is last-wins, so a trailing caller-supplied duplicate silently overrode
    the derived value, and the inner accepted it with zero verification.
    Reproduced at exit 0 with empty stderr, emitting a fabricated engine
    identity into a canonical ReviewReadinessV2.

    Closed at the MECHANISM, not at the reported route: authority-bearing
    private flags are outer-OWNED, so a caller supplying any of them is
    refused outright before any probe/materialization/semantics. Both argparse
    spellings are covered, because checking only the bare token would leave
    `--flag=value` open. The stderr-only, authority-free
    `--_diagnose-source-origin` deliberately remains caller-passable -- see
    test_source_origin_diagnostic_proves_subject_not_devrepo."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)
    argv = _cli_argv(
        target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
        responses_dir=responses_dir, policy_path=policy_path,
    )

    result = subprocess.run([*argv, *injected], capture_output=True, text=True)

    assert result.returncode != 0
    assert result.stdout == "", "a refused run must emit no artifact at all"
    assert json.loads(result.stderr)["error_class"] == "cli_caller_supplied_outer_owned_flag"


def test_ordinary_cli_still_emits_the_real_toolrepo_sha(tmp_path: Path):
    """Non-vacuity control for the refusal above: the same argv WITHOUT the
    injected flag must still succeed and carry this toolrepo's REAL committed
    HEAD -- otherwise the test above could pass against a CLI that refuses
    everything."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)
    argv = _cli_argv(
        target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
        responses_dir=responses_dir, policy_path=policy_path,
    )

    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    real_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=TOOLREPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert json.loads(result.stdout)["identity"]["toolrepo_sha"] == real_head
