from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
DOCS_DIR = REPO_ROOT / "docs"

CONTRACT_DOC = DOCS_DIR / "AGENTESCALA_TARGET_REPO_CONTRACT.md"
E2E_DOC = DOCS_DIR / "AGENT_REVIEW_E2E_PIPELINE.md"
QUALITY_GATE_DOC = DOCS_DIR / "AGENT_REVIEW_QUALITY_GATE.md"
MANUAL_DOC = DOCS_DIR / "AIOPS_PROJECT_MANUAL.md"
INTEGRATION_DOC = DOCS_DIR / "AGENTESCALA_TOOL_REPO_INTEGRATION.md"

ACTIVE_CONTRACT_DOCS = (
    CONTRACT_DOC,
    E2E_DOC,
    QUALITY_GATE_DOC,
    MANUAL_DOC,
    INTEGRATION_DOC,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_first_yaml_block(markdown: str) -> dict[str, object]:
    match = re.search(r"```yaml\n(.*?)\n```", markdown, flags=re.DOTALL)
    assert match, "expected at least one YAML block in contractual documentation"
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict), "YAML contractual snippet must parse to an object"
    return parsed


def test_contract_checkout_yaml_snippet_is_valid_and_pinned() -> None:
    snippet = _extract_first_yaml_block(_read(CONTRACT_DOC))

    env = snippet.get("env")
    assert isinstance(env, dict)
    assert "AIOPS_ORCHESTRATOR_SHA" in env

    jobs = snippet.get("jobs")
    assert isinstance(jobs, dict)
    analysis_job = jobs.get("aiops-analysis")
    assert isinstance(analysis_job, dict)
    assert analysis_job.get("if") == "github.event.pull_request.head.repo.full_name == github.repository"

    steps = analysis_job.get("steps")
    assert isinstance(steps, list) and steps

    checkout_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout@")
        ),
        None,
    )
    assert isinstance(checkout_step, dict), "contract must include a pinned actions/checkout step"
    uses = str(checkout_step["uses"])
    assert "@v" not in uses.lower(), "contractual checkout example cannot use floating tag pins"

    checkout_with = checkout_step.get("with")
    assert isinstance(checkout_with, dict)
    assert checkout_with.get("repository") == "mglpsw/aiops-orchestrator"
    assert checkout_with.get("ref") == "${{ env.AIOPS_ORCHESTRATOR_SHA }}"
    assert checkout_with.get("path") == "${{ runner.temp }}/aiops-orchestrator"
    assert checkout_with.get("persist-credentials") is False

    run_steps = "\n".join(str(step.get("run", "")) for step in steps if isinstance(step, dict))
    assert "[[ \"$AIOPS_ORCHESTRATOR_SHA\" =~ ^[0-9a-f]{40}$ ]]" in run_steps
    assert "git -C \"$RUNNER_TEMP/aiops-orchestrator\" rev-parse HEAD" in run_steps


def test_active_docs_do_not_use_unscoped_git_head_or_sha_tag_guidance() -> None:
    unscoped_git_head = re.compile(r"\bgit\s+rev-parse\s+HEAD\b")
    for doc in ACTIVE_CONTRACT_DOCS:
        text = _read(doc)
        assert not unscoped_git_head.search(text), f"{doc.name} contains unscoped git HEAD validation"
        assert "SHA/tag" not in text, f"{doc.name} contains deprecated SHA/tag guidance"
        assert "approved SHA/tag" not in text, f"{doc.name} contains deprecated approved SHA/tag guidance"


def test_contract_examples_require_non_floating_checkout_and_persist_credentials_false() -> None:
    for doc in (CONTRACT_DOC, MANUAL_DOC):
        text = _read(doc)
        assert "actions/checkout@v4" not in text, f"{doc.name} contains floating actions checkout example"
        assert "persist-credentials: false" in text, f"{doc.name} must require persist-credentials false"


def test_quality_gate_matrix_disallows_degraded_approval_and_requires_deterministic_fail_closed() -> None:
    contract = _read(CONTRACT_DOC)
    quality_gate = _read(QUALITY_GATE_DOC)

    decision_table = contract.split("## Wrapper decision table", maxsplit=1)[1].split(
        "## Safe publication contract", maxsplit=1
    )[0]
    assert "| Valid gate; `status=manual_review_required`;" in decision_table
    assert "| Valid gate; `status=failed`;" in decision_table
    assert (
        "| Valid gate; `status=degraded`; `manual_review_required=false`; "
        "`normalized_verdict=changes_requested`; `blocked_reasons` non-empty |"
    ) in decision_table
    assert "status=degraded` must never be hidden and can never be used for conclusive\napproval" in decision_table
    assert "gate_combination_invalid" in decision_table
    assert "publication_result=review_unavailable" in decision_table
    assert "manual_review_required=true" in decision_table
    assert "publication_class=fail_closed" in decision_table
    assert (
        "`status=degraded`; `manual_review_required=false`; `normalized_verdict` is `approved`"
        not in decision_table
    )

    assert "`degraded` never approves." in quality_gate
    assert "gate_combination_invalid" in quality_gate
