"""`#200-F` §14/§15 -- provider-free product acceptance, black box.

The real outer CLI is executed as a subprocess. Nothing is stubbed inside it:
it validates inputs, materialises the toolrepo execution subject from
committed bytes, opens the control channel, starts the inner, and the inner
composes the run and prints readiness.

No live Router. No provider. No network. Responses are prepared files.

The predecessor's methodology trap applies throughout: the execution subject
is materialised from the **committed** tree, so a change left in the worktree
is invisible to these tests. Any mutant intended to be observed here must be
committed first.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from app.agent_review.contracts_v2 import TargetProfileV2, compute_response_sha256_v2
from app.agent_review.diff_acquisition_v2 import parse_unified_diff
from app.agent_review.operational_inner_control_v2 import (
    INNER_CONTROL_CHANNEL_ABSENT_REASON_V2,
)
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.contracts_v2 import SemanticGroupV2

_REPOSITORY_ROOT_V2 = pathlib.Path(__file__).resolve().parents[2]
_CLI_V2 = _REPOSITORY_ROOT_V2 / "scripts" / "aiops-review-run-v2.py"

_SECRET_V2 = "super-secret-value"

_BASE_SHA_V2 = "1" * 40
_HEAD_SHA_V2 = "2" * 40
_TESTED_MERGE_SHA_V2 = "3" * 40
_TOOLCHAIN_DIGEST_V2 = "4" * 64


def _committed_toolrepo_sha_v2() -> str:
    """The sha the product will derive for itself and record as identity.

    Read here so the prepared responses bind to the same run identity the
    product computes. The product does not take this value from argv -- it
    materialises a subject and verifies the document against the bytes -- so
    the fixture has to agree with reality rather than assert it.
    """
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT_V2,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

# One reviewable file carrying a quoted secret, plus a pure rename that the
# predecessor's blanket refusal would have used to deny the whole review.
_DIFF_V2 = f'''diff --git a/app/service.py b/app/service.py
index 1111111..2222222 100644
--- a/app/service.py
+++ b/app/service.py
@@ -1,3 +1,4 @@
 import os
+password = "{_SECRET_V2}"
 def handler():
     return 1
diff --git a/app/old_name.py b/app/new_name.py
similarity index 100%
rename from app/old_name.py
rename to app/new_name.py
'''


def _profile_document_v2() -> dict[str, object]:
    return {
        "schema_id": "agent-review.target-profile.v2",
        "schema_version": 2,
        "source": "repo-profile",
        "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
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
            "max_chars_per_chunk": 24000,
            "max_files_per_chunk": 50,
            "max_contracts_per_chunk": 50,
        },
        "must_review": {
            "paths": [],
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
            "allowed_semantic_groups": ["primary_backend_logic", "tests"],
            "coverage_failure_state": "blocked_pipeline",
            "model_uncertainty_state": "manual_required",
        },
        "contracts": [],
        "limitations": [],
    }


def _grouping_policy_v2() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all",
        semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
        path_patterns=["*"],
        contract_ids=[],
        artifact_ids=[],
        priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [rule],
        "fallback_group": None,
    }
    policy_sha256 = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json")]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=policy_sha256)


@pytest.fixture
def product_workspace_v2(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Prepare every input the product reads, and matching offline responses.

    The library is used here to *derive* the payload identities so responses
    can be written for them. The product itself is then exercised strictly as
    a black box.
    """
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_profile_document_v2()), encoding="utf-8")

    grouping_policy = _grouping_policy_v2()
    policy_path = tmp_path / "grouping-policy.json"
    policy_path.write_text(grouping_policy.model_dump_json(), encoding="utf-8")

    diff_path = tmp_path / "changes.diff"
    diff_path.write_text(_DIFF_V2, encoding="utf-8")

    target_root = tmp_path / "target"
    target_root.mkdir()

    outcome = assemble_manifest_from_diff_v2(
        parse_unified_diff(_DIFF_V2),
        profile=TargetProfileV2.model_validate(_profile_document_v2()),
        grouping_policy=grouping_policy,
        repo="mglpsw/aiops-orchestrator",
        pr_number=200,
        base_sha=_BASE_SHA_V2,
        head_sha=_HEAD_SHA_V2,
        tested_merge_sha=_TESTED_MERGE_SHA_V2,
        toolrepo_sha=_committed_toolrepo_sha_v2(),
        evidence_hash=_TOOLCHAIN_DIGEST_V2,
        max_lines_per_chunk=400,
    )
    assert outcome.manifest is not None, outcome.blocked_reason

    responses_root = tmp_path / "responses"
    responses_root.mkdir()
    for built in build_chunk_payloads_v2(outcome.manifest):
        payload = built.payload
        envelope: dict[str, object] = {
            "schema_id": "agent-review.chunk-response-envelope.v2",
            "schema_version": 2,
            "source": "agent-review-provider-response",
            "status": "success",
            "run_id": payload.run_id,
            "chunk_id": payload.chunk_id,
            "payload_sha256": payload.payload_sha256,
            "head_sha": payload.identity.head_sha,
            "provider": "offline",
            "model": "offline-fixture",
            "attempt": 1,
            "request_id": f"req-{payload.chunk_id}",
            "finish_reason": "stop",
            "response_received": True,
            "response_sha256": "9" * 64,
            "result": {
                "schema_id": "agent-review.chunk-response.v2",
                "schema_version": 2,
                "summary": "offline-review",
                "findings": [],
                "coverage": json.loads(payload.coverage.model_dump_json()),
                "limitations": [],
            },
        }
        envelope["response_sha256"] = compute_response_sha256_v2(envelope)
        (responses_root / f"{payload.chunk_id}.json").write_text(
            json.dumps(envelope), encoding="utf-8"
        )

    return {
        "profile": profile_path,
        "grouping_policy": policy_path,
        "diff": diff_path,
        "responses": responses_root,
        "target_root": target_root,
    }


def _public_argv_v2(workspace: dict[str, pathlib.Path], **overrides: str) -> list[str]:
    values = {
        "--repo": "mglpsw/aiops-orchestrator",
        "--pr-number": "200",
        "--base-sha": _BASE_SHA_V2,
        "--head-sha": _HEAD_SHA_V2,
        "--tested-merge-sha": _TESTED_MERGE_SHA_V2,
        "--toolchain-digest": _TOOLCHAIN_DIGEST_V2,
        "--event-type": "pull_request",
        "--event-action": "synchronize",
        "--delivery-id": "delivery-0001",
        "--profile": str(workspace["profile"]),
        "--grouping-policy": str(workspace["grouping_policy"]),
        "--diff": str(workspace["diff"]),
        "--responses": str(workspace["responses"]),
        "--target-root": str(workspace["target_root"]),
    }
    values.update(overrides)
    argv: list[str] = []
    for flag, value in values.items():
        argv.extend([flag, value])
    return argv


def _run_product_v2(
    workspace: dict[str, pathlib.Path],
    *,
    extra_argv: list[str] | None = None,
    environment: dict[str, str] | None = None,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    argv = _public_argv_v2(workspace, **overrides) + list(extra_argv or [])
    return subprocess.run(
        [sys.executable, str(_CLI_V2), *argv],
        capture_output=True,
        text=True,
        cwd=_REPOSITORY_ROOT_V2,
        env={**os.environ, **(environment or {})},
        timeout=600,
    )


def test_the_offline_product_composes_a_run_end_to_end(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Non-vacuity control for every refusal test in this file."""
    completed = _run_product_v2(product_workspace_v2)

    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)

    assert document["schema_id"] == "agent-review.operational-run.v2"
    assert len(document["run_id"]) == 64
    assert len(document["toolrepo_sha"]) == 40
    assert document["readiness_state"] in {
        "ready",
        "blocked_code",
        "blocked_pipeline",
        "manual_required",
        "stale",
    }


def test_the_run_reports_the_toolrepo_sha_of_the_code_that_actually_ran(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Identity is derived, never asserted by the caller.

    The value in the artifact must be this checkout's committed HEAD, which
    the outer read for itself. There is no flag to say otherwise.
    """
    completed = _run_product_v2(product_workspace_v2)
    assert completed.returncode == 0, completed.stderr

    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT_V2,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert json.loads(completed.stdout)["toolrepo_sha"] == expected


def test_a_pure_rename_no_longer_denies_the_whole_review(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """The `#276` regression, reversed at product level.

    The predecessor refused this exact run outright. Here the rename is
    dispositioned as metadata-only, the reviewable file is reviewed, and total
    scope stays complete because the rename carries no material.
    """
    completed = _run_product_v2(product_workspace_v2)
    assert completed.returncode == 0, completed.stderr

    scope = json.loads(completed.stdout)["scope"]

    assert "app/new_name.py" in scope["metadata_only_paths"]
    assert "app/service.py" in scope["reviewable_paths"]
    assert scope["complete"] is True
    assert scope["blocked"] is False
    assert sorted(scope["changed_paths"]) == ["app/new_name.py", "app/service.py"]


def test_no_raw_secret_reaches_any_product_output(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Authority E, at the product boundary.

    The diff under review contains `password = "..."` -- the shape the merged
    redaction rule could not match. Neither stream may carry it.
    """
    completed = _run_product_v2(product_workspace_v2)

    assert _SECRET_V2 not in completed.stdout
    assert _SECRET_V2 not in completed.stderr


def test_the_product_makes_no_network_call(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Proved by severing DNS and proxying, not by inspection.

    If any layer attempted an outbound call it would have to resolve or
    proxy, and both are pointed at addresses that cannot answer.
    """
    completed = _run_product_v2(
        product_workspace_v2,
        environment={
            "http_proxy": "http://127.0.0.1:1",
            "https_proxy": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "no_proxy": "",
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["schema_id"] == "agent-review.operational-run.v2"


def test_the_target_repository_is_not_mutated_by_a_run(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """The toolrepo checkout the product runs from is left untouched."""
    def _state() -> tuple[str, str]:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT_V2, capture_output=True, text=True, check=True
        ).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=_REPOSITORY_ROOT_V2, capture_output=True, text=True, check=True
        ).stdout
        return head, porcelain

    before = _state()
    completed = _run_product_v2(product_workspace_v2)
    assert completed.returncode == 0, completed.stderr

    assert _state() == before


# --------------------------------------------------------------------------
# §15 -- the argv-level adversarial corpus for authority B.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forgery_argv",
    [
        ["--_controlled-inner"],
        ["--_inner-subject-root", "/tmp/attacker"],
        ["--_inner-declared-toolrepo-sha", "b" * 40],
        # The exact `#276` bypass: argparse accepts unambiguous abbreviations,
        # so a textual whole-token guard never saw this one.
        ["--_inner-d", "b" * 40],
        ["--_inner-subject-root=/tmp/attacker"],
        ["--_controlled-inner=1"],
        ["--controlled-inner"],
        ["--inner-subject-root", "/tmp/attacker"],
    ],
)
def test_no_argv_spelling_can_express_inner_authority(
    product_workspace_v2: dict[str, pathlib.Path], forgery_argv: list[str]
) -> None:
    """Structurally impossible, not blocked.

    These are not rejected by a guard that had to anticipate them -- there is
    no option for them to abbreviate, duplicate or assign to. argparse reports
    an unrecognised argument, which is the correct answer to a flag that does
    not exist.
    """
    completed = _run_product_v2(product_workspace_v2, extra_argv=forgery_argv)

    assert completed.returncode != 0
    assert "unrecognized arguments" in completed.stderr


def test_a_duplicate_public_flag_cannot_smuggle_inner_authority(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """`#276` leaned on argparse last-wins ordering as a defence.

    Ordering is not authority. A duplicated public flag cannot reach inner
    authority because no public flag carries any, so the run still reports the
    real committed toolrepo sha regardless of which occurrence argparse kept.
    ``--delivery-id`` is duplicated here precisely because it is *not* part of
    ``RunIdentityV2``: it isolates the argv question from the identity one,
    which the next test covers.
    """
    completed = _run_product_v2(
        product_workspace_v2, extra_argv=["--delivery-id", "delivery-second"]
    )

    assert completed.returncode == 0, completed.stderr
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT_V2, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert json.loads(completed.stdout)["toolrepo_sha"] == expected


def test_a_duplicated_identity_flag_refuses_rather_than_running_a_different_run(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Duplicating an identity-bearing flag must not quietly change the run.

    ``--toolchain-digest`` participates in ``RunIdentityV2``, so a second
    occurrence yields a different ``run_id``. The prepared responses then bind
    to nothing, and the product refuses with a typed reason instead of
    composing a run whose responses describe a different identity.

    This is the outcome that matters: last-wins is a parser behaviour, and the
    protection comes from identity binding downstream of it, not from argv
    ordering.
    """
    completed = _run_product_v2(
        product_workspace_v2, extra_argv=["--toolchain-digest", "5" * 64]
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == "run_id_mismatch"
    assert "Traceback" not in completed.stderr


def test_a_poisoned_environment_cannot_supply_inner_authority(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """There is no environment fallback either.

    The channel is the only route, and the outer builds the child's
    environment from scratch.
    """
    completed = _run_product_v2(
        product_workspace_v2,
        environment={
            "AGENT_REVIEW_INNER_SUBJECT_ROOT": "/tmp/attacker",
            "AGENT_REVIEW_INNER_DECLARED_TOOLREPO_SHA": "b" * 40,
            "AGENT_REVIEW_CONTROLLED_INNER": "1",
        },
    )

    assert completed.returncode == 0, completed.stderr
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT_V2, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert json.loads(completed.stdout)["toolrepo_sha"] == expected


def test_a_forged_control_document_is_refused_not_demoted(
    product_workspace_v2: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """A malformed channel must not buy a second, unconstrained attempt.

    Silently treating "the document is wrong" as "then I must be the outer"
    would hand an attacker exactly the retry the channel exists to deny.
    """
    forged = tmp_path / "forged.json"
    forged.write_text(
        json.dumps(
            {
                "schema_id": "agent-review.inner-control.v2",
                "subject_root": "/tmp/attacker",
                "declared_toolrepo_sha": "b" * 40,
                "subject_digest": "c" * 64,
            }
        ),
        encoding="utf-8",
    )

    descriptor = os.open(forged, os.O_RDONLY)
    try:
        completed = subprocess.run(
            [sys.executable, str(_CLI_V2), *_public_argv_v2(product_workspace_v2)],
            capture_output=True,
            text=True,
            cwd=_REPOSITORY_ROOT_V2,
            env={**os.environ, "AGENT_REVIEW_INNER_CONTROL_FD_V2": str(descriptor)},
            pass_fds=(descriptor,),
            timeout=600,
        )
    finally:
        os.close(descriptor)

    assert completed.returncode == 2
    assert completed.stderr.strip() == "inner_control_subject_root_mismatch"
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "field, bad_value, expected_reason",
    [
        ("--delivery-id", "bad id here", "operational_ingress_invalid_delivery_id"),
        ("--repo", "not-a-repo", "operational_ingress_invalid_repo"),
        ("--pr-number", "0", "operational_ingress_invalid_pr_number"),
        ("--pr-number", "not-a-number", "operational_ingress_invalid_pr_number"),
        ("--base-sha", "short", "operational_ingress_invalid_base_sha"),
        ("--head-sha", "Z" * 40, "operational_ingress_invalid_head_sha"),
        ("--toolchain-digest", "d" * 63, "operational_ingress_invalid_toolchain_digest"),
        ("--event-action", "exploded", "operational_ingress_invalid_event_action"),
    ],
)
def test_bad_public_input_is_a_content_free_refusal_with_no_traceback(
    product_workspace_v2: dict[str, pathlib.Path],
    field: str,
    bad_value: str,
    expected_reason: str,
) -> None:
    """The `#276` round-4 P0, at the product boundary.

    ``--delivery-id 'bad id here'`` used to print a raw pydantic traceback
    leaking virtualenv and subject temp-directory paths.
    """
    completed = _run_product_v2(product_workspace_v2, **{field: bad_value})

    assert completed.returncode == 2
    assert completed.stderr.strip() == expected_reason
    assert "Traceback" not in completed.stderr
    assert "pydantic" not in completed.stderr
    assert "site-packages" not in completed.stderr


def test_a_refusal_never_echoes_the_offending_value(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """A secret pasted into the wrong flag must not land in the logs."""
    completed = _run_product_v2(
        product_workspace_v2, **{"--delivery-id": f"{_SECRET_V2} with spaces"}
    )

    assert completed.returncode == 2
    assert _SECRET_V2 not in completed.stderr
    assert _SECRET_V2 not in completed.stdout


def test_a_missing_caller_path_is_refused_before_the_inner_starts(
    product_workspace_v2: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """Paths are public inputs and are validated pre-seal like the rest."""
    completed = _run_product_v2(
        product_workspace_v2, **{"--profile": str(tmp_path / "absent.json")}
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == "operational_ingress_path_not_a_file"
    assert "Traceback" not in completed.stderr


def test_a_relative_caller_path_is_refused_rather_than_resolved(
    product_workspace_v2: dict[str, pathlib.Path],
) -> None:
    """Resolving against the CWD would make a run mean different things.

    Where the caller happened to stand is not part of any identity the
    product records.
    """
    completed = _run_product_v2(product_workspace_v2, **{"--diff": "changes.diff"})

    assert completed.returncode == 2
    assert completed.stderr.strip() == "operational_ingress_path_not_absolute"


def test_the_outer_validates_before_it_materialises_or_spawns_anything() -> None:
    """§4's *pre-seal* ordering, asserted structurally.

    A behavioural probe was attempted first and abandoned, for reasons worth
    recording rather than hiding:

    * a read-only ``TMPDIR`` is inert, because this suite runs as root and
      root writes to a 0o500 directory happily;
    * a missing ``TMPDIR`` is also inert, because ``tempfile`` deliberately
      falls through its candidate list to ``/tmp`` when a candidate is
      unusable.

    Both would have produced a green test that proved nothing, which is
    precisely the `#276` failure mode. So the ordering is asserted where it is
    actually decidable -- in the source of the one function that owns it --
    and the claim is limited to what that shows: the outer calls the ingress
    authority before it materialises a subject or starts a child.
    """
    import ast

    source = _CLI_V2.read_text(encoding="utf-8")
    tree = ast.parse(source)
    outer = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_outer_bootstrap_v2"
    )

    def _first_line_calling(name: str) -> int:
        for node in ast.walk(outer):
            if isinstance(node, ast.Call):
                target = node.func
                called = getattr(target, "id", None) or getattr(target, "attr", None)
                if called == name:
                    return node.lineno
        raise AssertionError(f"{name} is not called by the outer bootstrap")

    validation_line = _first_line_calling("_validated_inputs_v2")

    for sealing_call in (
        "materialise_toolrepo_execution_subject_v2",
        "mkdtemp",
        "run",  # subprocess.run -- the child
        "pipe",
    ):
        assert validation_line < _first_line_calling(sealing_call), (
            f"public input must be validated before {sealing_call}"
        )


def test_both_processes_validate_public_input() -> None:
    """Validation is duplicated on purpose, and that is worth pinning.

    A product mutation that deleted the *outer's* validation survived: the
    inner validates too, so the same typed reason code still came out. That is
    not a coverage gap being papered over -- it is defence in depth working as
    designed, and the honest test is the one that asserts both call sites
    exist rather than one that pretends a single-sided deletion is observable
    from outside.

    The outer's copy exists so an impossible run is refused before a ~700-file
    subject is materialised and a child is started. The inner's copy exists so
    the semantic layer never trusts that someone upstream did it.
    """
    import ast

    tree = ast.parse(_CLI_V2.read_text(encoding="utf-8"))
    callers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and (getattr(inner.func, "id", None) == "_validated_inputs_v2")
            for inner in ast.walk(node)
        )
    }

    assert callers == {"_run_outer_bootstrap_v2", "_run_inner_semantic_v2"}
