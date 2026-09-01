"""`#200-G4` -- end-to-end regression tests for ``scripts/aiops-review-run-v2.py``.

Exercises the ingress boundary the way a real caller would: as a subprocess,
over real argv, real files, and a real environment variable, so that
"no raw exception/traceback/path/secret reaches stderr" is asserted against
actual process output rather than against an in-process function call that
could diverge from what a caller actually sees.

Each of the four "also open at STOP" items from `#277` round 2 gets its own
RED-witness-shaped test here, plus the mandatory bidirectional invariant at
the process boundary: a genuine internal programmer defect must still print
a raw traceback (and a non-zero, non-refusal-shaped exit), not a tidy reason
code.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import SemanticGroupV2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "aiops-review-run-v2.py"

_EXIT_OK = 0
_EXIT_REFUSED = 2
_EXIT_USAGE = 64

_PROFILE_YAML = """schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/repo
  default_branch: main
artifacts: []
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
contracts: []
limitations: []
"""

_SIMPLE_DIFF = """diff --git a/app.py b/app.py
index e69de29..0cfbf08 100644
--- a/app.py
+++ b/app.py
@@ -0,0 +1 @@
+a = 1
"""


def _grouping_policy_json() -> str:
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
        "rules": [rule.model_dump(mode="json")],
        "fallback_group": None,
    }
    material["policy_sha256"] = compute_semantic_grouping_policy_sha256_v2(material)
    return json.dumps(material)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "responses").mkdir()
    (tmp_path / "profile.yaml").write_text(_PROFILE_YAML, encoding="utf-8")
    (tmp_path / "grouping-policy.json").write_text(_grouping_policy_json(), encoding="utf-8")
    (tmp_path / "diff.txt").write_text(_SIMPLE_DIFF, encoding="utf-8")
    return tmp_path


def _run(workspace: Path, *, env: dict[str, str] | None = None, argv: list[str] | None = None):
    if argv is None:
        argv = [
            "--repo", "mglpsw/aiops-orchestrator",
            "--pr-number", "282",
            "--base-sha", "a" * 40,
            "--head-sha", "b" * 40,
            "--tested-merge-sha", "c" * 40,
            "--toolchain-digest", "d" * 64,
            "--event-type", "manual",
            "--event-action", "manual",
            "--delivery-id", "delivery-0001",
            "--profile", str(workspace / "profile.yaml"),
            "--grouping-policy", str(workspace / "grouping-policy.json"),
            "--diff", str(workspace / "diff.txt"),
            "--responses", str(workspace / "responses"),
        ]
    full_env = {"PATH": "/usr/bin:/bin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Non-vacuity: a legitimate run succeeds and says something meaningful.
# ---------------------------------------------------------------------------


def test_a_well_formed_run_succeeds(workspace: Path) -> None:
    result = _run(workspace)
    assert result.returncode == _EXIT_OK, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["schema_id"] == "agent-review.operational-ingress.v2"
    assert payload["repo"] == "mglpsw/aiops-orchestrator"
    assert payload["responses"] == {"answered": 0, "unanswered": 1}


def test_a_well_formed_run_with_an_answered_chunk_succeeds(workspace: Path) -> None:
    from app.agent_review.contracts_v2 import ChunkResponseEnvelopeV2

    envelope = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "run_id": "1" * 64,
        "chunk_id": "chunk-0000",
        "payload_sha256": "2" * 64,
        "head_sha": "b" * 40,
        "provider": "offline",
        "model": "offline-fixture",
        "attempt": 1,
        "request_id": "req-0001",
        "finish_reason": "error",
        "response_received": False,
        "response_sha256": None,
        "status": "error",
        "error": {"reason_code": "transport_failure", "retryable": True},
    }
    # Non-vacuity for the fixture itself: it must actually validate against
    # the real product contract, not just be plausible-looking JSON.
    ChunkResponseEnvelopeV2.model_validate_json(json.dumps(envelope))

    (workspace / "responses" / "chunk-0000.json").write_text(
        json.dumps(envelope), encoding="utf-8"
    )
    result = _run(workspace)
    assert result.returncode == _EXIT_OK, result.stderr
    payload = json.loads(result.stdout)
    assert payload["responses"] == {"answered": 1, "unanswered": 0}


# ---------------------------------------------------------------------------
# RED WITNESS 1 (mandatory): --responses file content.
# ---------------------------------------------------------------------------


def test_red_witness_malformed_response_json_is_a_typed_refusal_no_traceback(
    workspace: Path,
) -> None:
    (workspace / "responses" / "chunk-0000.json").write_text("{not valid json", encoding="utf-8")
    result = _run(workspace)

    assert result.returncode == _EXIT_REFUSED
    assert "Traceback" not in result.stderr
    assert "ValidationError" not in result.stderr
    assert str(workspace) not in result.stderr
    assert result.stderr.strip() == "operational_ingress_document_invalid_responses"


def test_red_witness_non_utf8_response_bytes_is_a_typed_refusal_no_traceback(
    workspace: Path,
) -> None:
    (workspace / "responses" / "chunk-0000.json").write_bytes(b"\xff\xfe\x00bad")
    result = _run(workspace)

    assert result.returncode == _EXIT_REFUSED
    assert "Traceback" not in result.stderr
    assert "UnicodeDecodeError" not in result.stderr
    assert str(workspace) not in result.stderr
    assert result.stderr.strip() == "operational_ingress_document_unreadable_responses"


# ---------------------------------------------------------------------------
# Also open at STOP, item 2: temp-directory cleanup on refusal paths.
#
# This script does not materialise a target subject (that is G1's job, not
# wired here), so there is no subject-shaped temp directory for THIS script
# to leak. The structural fix (`operational_workspace_v2.temp_workspace_v2`)
# is unit-tested directly in `test_operational_workspace_v2.py`, including a
# RED-witness reproduction of the exact leak. Recorded here so the mapping
# from "also open at STOP" item to its test coverage is discoverable from
# this file too.
# ---------------------------------------------------------------------------


def test_workspace_cleanup_fix_is_covered_elsewhere() -> None:
    from tests.agent_review import test_operational_workspace_v2 as _covered

    assert hasattr(_covered, "test_pre_fix_ordering_really_did_leak")
    assert hasattr(
        _covered, "test_the_workspace_is_removed_when_the_block_raises_a_typed_refusal"
    )


# ---------------------------------------------------------------------------
# Also open at STOP, item 3: OverflowError on an out-of-range control fd.
# ---------------------------------------------------------------------------


def test_red_witness_out_of_range_control_fd_is_a_typed_refusal_no_overflow_error(
    workspace: Path,
) -> None:
    result = _run(workspace, env={"AGENT_REVIEW_INNER_CONTROL_FD_V2": "9" * 40})

    assert result.returncode == _EXIT_REFUSED
    assert "Traceback" not in result.stderr
    assert "OverflowError" not in result.stderr
    assert result.stderr.strip() == "operational_ingress_invalid_control_fd"


# ---------------------------------------------------------------------------
# Also open at STOP, item 4: fd=0 hangs forever.
# ---------------------------------------------------------------------------


def test_red_witness_control_fd_zero_does_not_hang(workspace: Path) -> None:
    """A tight timeout is the point: the predecessor bug was an infinite
    block on stdin, so this test itself would hang (and eventually be
    killed by the suite's own timeout) if the regression reappeared,
    rather than failing fast with an assertion."""
    result = _run(workspace, env={"AGENT_REVIEW_INNER_CONTROL_FD_V2": "0"})

    assert result.returncode == _EXIT_REFUSED
    assert result.stderr.strip() == "operational_ingress_invalid_control_fd"


def test_red_witness_control_fd_one_and_two_are_also_refused(workspace: Path) -> None:
    """`0` is the headline (stdin), but `1`/`2` (stdout/stderr) are exactly
    as wrong for the same reason: never a freshly created pipe end."""
    for reserved_fd in ("1", "2"):
        result = _run(workspace, env={"AGENT_REVIEW_INNER_CONTROL_FD_V2": reserved_fd})
        assert result.returncode == _EXIT_REFUSED
        assert result.stderr.strip() == "operational_ingress_invalid_control_fd"


# ---------------------------------------------------------------------------
# Also open at STOP, item 5 (renumbered from the issue's 4th item): the
# argparse usage-error path echoing caller-supplied bytes.
# ---------------------------------------------------------------------------


def test_red_witness_usage_error_does_not_echo_argv_secret() -> None:
    secret = "sk-live-ARGV-SECRET-SHOULD-NOT-LEAK-0123456789"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", "x/y", "--unknown-flag", secret],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )

    assert secret not in result.stderr
    assert secret not in result.stdout
    assert result.returncode == _EXIT_USAGE
    assert result.stderr.strip() == "operational_ingress_usage_error"


def test_red_witness_missing_required_flag_does_not_echo_the_partial_argv() -> None:
    """A different argparse usage-error shape (missing a required flag)
    goes through the same override; asserted separately since argparse
    builds this message differently from "unrecognized arguments"."""
    secret = "sk-live-SHOULD-NOT-LEAK-EITHER-0123456789"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", secret],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )

    assert secret not in result.stderr
    assert secret not in result.stdout
    assert result.returncode == _EXIT_USAGE


# ---------------------------------------------------------------------------
# Mandatory bidirectional invariant at the process boundary.
# ---------------------------------------------------------------------------


def test_a_genuine_internal_defect_still_produces_a_raw_traceback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injects a real programmer defect into the ingress boundary function
    itself (not caller material) via a thin wrapper script, and asserts the
    process boundary lets it escape as an ordinary Python traceback with a
    non-family exit code -- proving the ``except ExpectedOperationalRefusalV2``
    catch in ``main()`` is not a bare ``except Exception`` in disguise."""
    defect_script = workspace / "run_with_injected_defect.py"
    defect_script.write_text(
        "import runpy\n"
        "import sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
        "import app.agent_review.operational_ingress_v2 as ingress\n"
        "\n"
        "def _broken(*args, **kwargs):\n"
        "    raise AssertionError('injected programmer defect, not caller material')\n"
        "ingress.validate_public_inputs_v2 = _broken\n"
        "\n"
        f"runpy.run_path({str(_SCRIPT)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )

    argv = [
        "--repo", "mglpsw/aiops-orchestrator",
        "--pr-number", "282",
        "--base-sha", "a" * 40,
        "--head-sha", "b" * 40,
        "--tested-merge-sha", "c" * 40,
        "--toolchain-digest", "d" * 64,
        "--event-type", "manual",
        "--event-action", "manual",
        "--delivery-id", "delivery-0001",
        "--profile", str(workspace / "profile.yaml"),
        "--grouping-policy", str(workspace / "grouping-policy.json"),
        "--diff", str(workspace / "diff.txt"),
        "--responses", str(workspace / "responses"),
    ]
    result = subprocess.run(
        [sys.executable, str(defect_script), *argv],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )

    assert result.returncode not in (_EXIT_OK, _EXIT_REFUSED, _EXIT_USAGE), (
        "a genuine programmer defect was laundered into one of this "
        f"product's own exit codes: {result.returncode}"
    )
    assert "Traceback" in result.stderr
    assert "AssertionError" in result.stderr
    assert "injected programmer defect" in result.stderr
    assert "operational_ingress" not in result.stderr.splitlines()[-1]
