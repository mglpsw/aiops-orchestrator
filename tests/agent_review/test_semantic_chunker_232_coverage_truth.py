"""RED/lock tests for aiops-orchestrator#232 (V1-C1, claim CL-1 coverage truth).

A changed file is reported as covered only when an observable textual hunk
for it was admitted into a chunk. #231/C8 closed this for `must_review`
files; these tests extend the same property to every tier and prove that the
resulting non-complete coverage reaches the quality gate, not only the plan.
The historical contract index is retained at
`campaign/agent-review-v1-freeze/evidence/v1-c1-coverage-truth-index.json`
and points to immutable source commit
`d3f5946c4d0513def9f7c2018b63703a53df1cc7` (OBL-CL1-01,
CM-CL1-01/02/03).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.chunk_result_parser import parse_chunk_results
from app.agent_review.final_synthesizer import synthesize_final_review
from app.agent_review.quality_gate import evaluate_review_quality_gate, validate_final_review_document
from app.agent_review.schemas import RedactionReport
from app.agent_review.semantic_chunker import build_semantic_chunk_plan

MUST = "backend/api/a.py"
ROOT = Path(__file__).resolve().parents[2]


def _hunk(path: str, lines: int = 3) -> str:
    body = "\n".join(f"+    value_{i} = compute_window(index_{i}, offset_{i})" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,{lines} @@\n{body}"


# Diff blocks with no `@@` hunk header: nothing line-level reaches the model.
NO_HUNK_BLOCKS = {
    "binary": (
        "assets/logo.png",
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ",
    ),
    # The form the consumer actually produces (`git diff --binary`).
    "binary_patch": (
        "assets/icon.png",
        "diff --git a/assets/icon.png b/assets/icon.png\n"
        "new file mode 100644\n"
        "index 0000000..9daeafb\n"
        "GIT binary patch\n"
        "literal 12\n"
        "Tc${NkU|?WnU|?WnU|?Wn01N;F\n"
        "\n"
        "literal 0\n"
        "HcmV?d00001",
    ),
    "mode_only": (
        "scripts/run.sh",
        "diff --git a/scripts/run.sh b/scripts/run.sh\nold mode 100644\nnew mode 100755",
    ),
    "pure_rename": (
        "docs/new.md",
        "diff --git a/docs/old.md b/docs/new.md\n"
        "similarity index 100%\n"
        "rename from docs/old.md\n"
        "rename to docs/new.md",
    ),
    "empty_new_file": (
        "pkg/__init__.py",
        "diff --git a/pkg/__init__.py b/pkg/__init__.py\nnew file mode 100644\nindex 0000000..e69de29",
    ),
    "block_absent": ("frontend/src/page.jsx", ""),
}


def _intake(extra_path: str, extra_block: str) -> dict:
    return _intake_for({MUST: _hunk(MUST), extra_path: extra_block})


def _intake_for(blocks: dict[str, str], *, with_diff_artifact: bool = True) -> dict:
    """`blocks` maps each declared changed file to its raw diff block ("" = no block)."""
    files = list(blocks)
    diff = "\n".join(block for block in blocks.values() if block)
    artifacts: dict = {
        "file-diff-context.json": {
            "path": "file-diff-context.json",
            "content": {
                "files": [{"path": p, "status": "modified", "summary": ""} for p in files],
                "coverage_requirements": {"must_review_files": [MUST]},
            },
        },
    }
    if with_diff_artifact:
        artifacts["full-diff.diff"] = {"path": "full-diff.diff", "content": diff}
    return {
        "schema_id": "agent-review.intake.v1",
        "schema_version": 1,
        "source": "aiops-review-intake",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {},
        "created_at": "2026-09-30T00:00:00Z",
        "artifacts": artifacts,
        "artifact_status": [],
        "redaction_summary": RedactionReport().model_dump(mode="json"),
        "limitations": [],
        "completeness": {},
        "status": "complete",
    }


def _gate_for(plan, tmp_path: Path):
    """Run the real parse -> synthesize -> gate path with a model that claims
    to have reviewed every file it was handed (the optimistic self-report the
    parser accepts verbatim)."""
    responses = tmp_path / "chunk-responses"
    responses.mkdir()
    for chunk in plan.chunks:
        (responses / f"{chunk.chunk_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "chunk_id": chunk.chunk_id,
                    "semantic_group": chunk.semantic_group,
                    "confirmed_findings": [],
                    "risks": [],
                    "limitations": [],
                    "coverage_notes": {"files_reviewed": chunk.files},
                }
            ),
            encoding="utf-8",
        )
    results = parse_chunk_results(plan, responses_dir=responses)
    final_review = synthesize_final_review(results, chunk_plan=plan)
    document = validate_final_review_document(json.loads(final_review.model_dump_json()))
    return evaluate_review_quality_gate(document, results, chunk_plan=plan)


@pytest.mark.parametrize("kind", sorted(NO_HUNK_BLOCKS))
def test_232_non_must_review_file_without_textual_hunk_is_not_covered(kind: str) -> None:
    path, block = NO_HUNK_BLOCKS[kind]
    plan = build_semantic_chunk_plan(_intake(path, block), max_blocks=6, max_chars_per_block=24_000)

    assert path not in plan.files_covered, plan.files_covered
    assert all(path not in chunk.files for chunk in plan.chunks)
    assert path in plan.files_not_covered, plan.files_not_covered
    # Tier-distinct reason code: non-must_review files are counted by one
    # aggregate limitation; the per-path must_review code stays reserved.
    assert "hunk_unavailable_count:1" in plan.limitations, plan.limitations
    assert f"must_review_hunk_unavailable:{path}" not in plan.limitations
    assert not any(item.endswith(f":{path}") for item in plan.limitations), plan.limitations
    assert plan.status == "degraded"
    # The must_review file with a real hunk is still covered.
    assert MUST in plan.files_covered


@pytest.mark.parametrize("kind", sorted(NO_HUNK_BLOCKS))
def test_232_uncovered_non_must_file_reaches_the_quality_gate(kind: str, tmp_path: Path) -> None:
    path, block = NO_HUNK_BLOCKS[kind]
    plan = build_semantic_chunk_plan(_intake(path, block), max_blocks=6, max_chars_per_block=24_000)

    gate = _gate_for(plan, tmp_path)

    assert gate.status != "passed", gate
    assert gate.manual_review_required is True
    assert gate.normalized_verdict == "manual_review_required"


@pytest.mark.parametrize("kind", sorted(NO_HUNK_BLOCKS))
def test_232_positive_control_same_path_with_textual_hunk_stays_covered(kind: str, tmp_path: Path) -> None:
    # Strict pairing: the SAME path as the countermodel, differing only in
    # the presence of an observable `@@` hunk.
    path, _ = NO_HUNK_BLOCKS[kind]
    plan = build_semantic_chunk_plan(_intake(path, _hunk(path)), max_blocks=6, max_chars_per_block=24_000)

    assert path in plan.files_covered
    assert plan.files_not_covered == []
    assert not any("hunk_unavailable" in item for item in plan.limitations), plan.limitations
    assert plan.status == "complete"

    gate = _gate_for(plan, tmp_path)
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False


def test_232_many_hunkless_files_do_not_crowd_reviewable_code_out_of_payloads() -> None:
    # Plan limitations are embedded unshrunk in every chunk payload's brief:
    # one code per hunk-less file (e.g. an asset pack or a directory move)
    # would push the real hunks over budget. The count is aggregated; identity
    # stays in files_not_covered.
    text = "backend/services/b.py"
    blocks = {MUST: _hunk(MUST), text: _hunk(text)}
    for index in range(300):
        asset = f"frontend/public/assets/illustrations/generated/asset_{index:03d}.png"
        blocks[asset] = (
            f"diff --git a/{asset} b/{asset}\nindex 1111111..2222222 100644\n"
            f"Binary files a/{asset} and b/{asset} differ"
        )
    plan = build_semantic_chunk_plan(_intake_for(blocks), max_blocks=6, max_chars_per_block=24_000)

    assert MUST in plan.files_covered, plan.limitations
    assert text in plan.files_covered, plan.limitations
    assert plan.chunks
    assert len(plan.files_not_covered) == 300
    assert [item for item in plan.limitations if "hunk_unavailable" in item] == ["hunk_unavailable_count:300"]
    assert plan.status == "degraded"


def test_232_absent_diff_artifact_leaves_no_file_covered(tmp_path: Path) -> None:
    # CM-CL1-03: with no full-diff artifact at all, no file has admitted
    # material -- must_review or not.
    other = "frontend/src/page.jsx"
    plan = build_semantic_chunk_plan(
        _intake_for({MUST: _hunk(MUST), other: _hunk(other)}, with_diff_artifact=False),
        max_blocks=6,
        max_chars_per_block=24_000,
    )

    assert plan.files_covered == []
    assert set(plan.files_not_covered) == {MUST, other}
    assert f"must_review_hunk_unavailable:{MUST}" in plan.limitations
    assert "hunk_unavailable_count:1" in plan.limitations
    assert plan.status == "degraded"
    gate = _gate_for(plan, tmp_path)
    assert gate.manual_review_required is True
    assert gate.status != "passed"


def _dev_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "dev",
            "AIOPS_NODE_ROLE": "toolrepo",
            "AIOPS_REPO_MODE": "agent_review_tooling",
            "AIOPS_PRODUCTION_RUNTIME": "false",
        }
    )
    return env


def _cli(script: str, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=_dev_env(),
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _cli_chain_gate(intake_dict: dict, tmp_path: Path) -> tuple[dict, dict]:
    """Real consumer CLI chain: plan-chunks -> parse-chunks -> synthesize -> quality-gate,
    with a model that claims to have reviewed every file it was handed."""
    intake = tmp_path / "aiops-intake.json"
    intake.write_text(json.dumps(intake_dict), encoding="utf-8")
    plan_path = tmp_path / "semantic-chunk-plan.json"
    _cli("aiops-review-plan-chunks.py", "--intake", str(intake), "--output", str(plan_path), "--max-blocks", "6")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    responses = tmp_path / "chunk-responses"
    responses.mkdir()
    for chunk in plan["chunks"]:
        (responses / f"{chunk['chunk_id']}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "chunk_id": chunk["chunk_id"],
                    "semantic_group": chunk["semantic_group"],
                    "confirmed_findings": [],
                    "risks": [],
                    "limitations": [],
                    "coverage_notes": {"files_reviewed": chunk["files"]},
                }
            ),
            encoding="utf-8",
        )
    results = tmp_path / "chunk-results.json"
    _cli(
        "aiops-review-parse-chunks.py",
        "--chunk-plan", str(plan_path), "--responses-dir", str(responses), "--output", str(results),
    )
    final_json = tmp_path / "final-review.json"
    _cli(
        "aiops-review-synthesize.py",
        "--chunk-results", str(results), "--chunk-plan", str(plan_path),
        "--output-json", str(final_json), "--output-md", str(tmp_path / "final-review.md"),
    )
    gate_path = tmp_path / "review-quality-gate.json"
    _cli(
        "aiops-review-quality-gate.py",
        "--final-review", str(final_json), "--chunk-results", str(results),
        "--chunk-plan", str(plan_path), "--output", str(gate_path),
    )
    return plan, json.loads(gate_path.read_text(encoding="utf-8"))


def test_232_cli_chain_binary_non_must_file_is_not_a_passed_review(tmp_path: Path) -> None:
    path, block = NO_HUNK_BLOCKS["binary"]
    plan, gate = _cli_chain_gate(_intake(path, block), tmp_path)

    assert path in plan["files_not_covered"]
    assert path not in plan["files_covered"]
    assert plan["status"] == "degraded"
    assert gate["status"] == "manual_review_required"
    assert gate["manual_review_required"] is True


def test_232_cli_chain_positive_control_same_path_with_hunk_passes(tmp_path: Path) -> None:
    path, _ = NO_HUNK_BLOCKS["binary"]
    plan, gate = _cli_chain_gate(_intake(path, _hunk(path)), tmp_path)

    assert path in plan["files_covered"]
    assert plan["status"] == "complete"
    assert gate["status"] == "passed"
    assert gate["manual_review_required"] is False
