"""V1-C0 evidence: in-memory reproduction of CM-CL5-01/CM-CL5-02 through the real v1
parse -> synthesize -> gate path. Read-only: writes only to a TemporaryDirectory.

Usage (from repo root, with the project venv):
    PYTHONDONTWRITEBYTECODE=1 python -B campaign/agent-review-v1-freeze/evidence/cm_c5_repro.py .
"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.agent_review.schemas import SemanticChunk, SemanticChunkPlan
from app.agent_review.chunk_result_parser import parse_chunk_results
from app.agent_review.final_synthesizer import synthesize_final_review
from app.agent_review.quality_gate import evaluate_review_quality_gate, validate_final_review_document

FILES = ["backend/services/schedule.py", "backend/services/swap.py"]
chunk = SemanticChunk(chunk_id="chunk-01-primary_backend_logic", semantic_group="primary_backend_logic",
    order_index=0, files=FILES, coverage="complete", prompt_budget_chars=20000, estimated_chars=1000)
plan = SemanticChunkPlan(target_repo="mglpsw/AgentEscala", max_parallel_blocks=6, chunks=[chunk],
    files_covered=FILES, status="complete")
P1 = {"severity": "P1", "title": "Swap removes coverage guard", "file_path": FILES[0], "line_or_hunk": "L10-L20",
      "evidence": "The changed hunk deletes the coverage guard before swap approval.", "source_artifact": "artifact:full-diff",
      "contract_id": None, "impact": "Uncovered shifts can be approved.", "confidence": "high", "dedupe_key": "k1"}

def run(name, findings, coverage_notes, critical=False, downstream_plan=plan):
    with tempfile.TemporaryDirectory() as d:
        Path(d, f"{chunk.chunk_id}.json").write_text(json.dumps({"schema_version": 1, "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group, "confirmed_findings": findings, "risks": [], "limitations": [],
            "coverage_notes": coverage_notes}))
        res = parse_chunk_results(plan, responses_dir=d)
    fr = synthesize_final_review(res, chunk_plan=downstream_plan)
    doc = validate_final_review_document(json.loads(fr.model_dump_json()))
    g = evaluate_review_quality_gate(doc, res, chunk_plan=downstream_plan, critical_pr=critical)
    print(f"{name}: parse={res.status} synth={fr.status}/{fr.verdict} gate={g.status}/{g.normalized_verdict}/manual={g.manual_review_required} lim={g.limitations}")

run("CM-CL5-02 all_not_reviewed noncritical", [], {"files_not_reviewed": FILES})
run("CM-CL5-02 all_not_reviewed noncritical no-plan (synth+gate without plan)", [], {"files_not_reviewed": FILES}, downstream_plan=None)
run("CM-CL5-02 all_not_reviewed critical", [], {"files_not_reviewed": FILES}, critical=True)
run("CM-CL5-01 P1 on not_reviewed file", [P1], {"files_reviewed": [FILES[1]], "files_not_reviewed": [FILES[0]]})
run("PC-CL5 P1 on reviewed file (positive control)", [P1], {"files_reviewed": FILES})
run("PC-CL5 clean all reviewed", [], {"files_reviewed": FILES})
