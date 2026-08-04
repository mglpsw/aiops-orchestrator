#!/usr/bin/env python3
"""Generate the AgentReview v2 benchmark premanifest (#88, OP-BENCH step 1).

Fixes the benchmark's identity BEFORE any provider call, per rev. 4.1's
determination: `created_before_provider_call: true` marks this file as
committed prior to Lane 2/3 acquisition. The final manifest
(`benchmark_identity.final.json`, written by PR-2) is append-only over
this file's fields -- it may add new keys, but must never change a value
already frozen here.

Every digest is computed from the actual committed content at HEAD, never
hand-typed, so a `--check` run can prove this file was not backdated or
edited after acquisition began.

Usage:
    python scripts/generate-benchmark-premanifest.py             # write
    python scripts/generate-benchmark-premanifest.py --check     # verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "reviewable_corpus"
MANIFEST_PATH = CORPUS_DIR / "MANIFEST.yaml"
CODEX_RULES_DIR = ROOT_DIR / ".codex"
OBSERVATION_MODULE = ROOT_DIR / "evals" / "agent_review_v2" / "observation.py"
OUTPUT_PATH = ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.pre.json"


def _sha256_of_files(paths: list[Path]) -> str:
    """Digest over sorted (relpath, content) pairs -- order-independent of
    filesystem iteration order, sensitive to both path and content."""
    hasher = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p)):
        hasher.update(str(path.relative_to(ROOT_DIR)).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(path.read_bytes())
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _git_head_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, capture_output=True, text=True, check=True
    ).stdout.strip()


def _codex_cli_version() -> str:
    result = subprocess.run(["codex", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip() or result.stderr.strip()


def build_premanifest(*, run_id: str, head_sha: str) -> dict:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    provider_applicable_case_ids = sorted(c["case_id"] for c in manifest["provider_applicable_cases"])

    corpus_files = [p for p in CORPUS_DIR.rglob("*") if p.is_file()]
    corpus_digest = _sha256_of_files(corpus_files)

    expected_files = sorted(CORPUS_DIR.glob("*/expected.yaml"))
    expected_manifest_digest = _sha256_of_files(expected_files)

    applicability_manifest_digest = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()

    rules_files = [p for p in CODEX_RULES_DIR.rglob("*") if p.is_file()]
    rules_files += sorted((ROOT_DIR / "tests" / "codex_review_rules").glob("RULE-*.md"))
    rules_digest = _sha256_of_files(rules_files)

    normalizer_digest = hashlib.sha256(OBSERVATION_MODULE.read_bytes()).hexdigest()

    return {
        "schema_id": "agent-review.benchmark-premanifest.v1",
        "benchmark_run_id": run_id,
        "source_master_sha": head_sha,
        "corpus_digest": corpus_digest,
        "applicability_manifest_digest": applicability_manifest_digest,
        "expected_manifest_digest": expected_manifest_digest,
        "codex_cli_version": _codex_cli_version(),
        "rules_digest": rules_digest,
        "normalizer_digest": normalizer_digest,
        "planned_cases_by_lane": {
            "aiops_subject_projection": provider_applicable_case_ids,
            "codex_local": provider_applicable_case_ids,
            "codex_github": provider_applicable_case_ids,
            "human": "deferred_pending_H1_or_H2_disposition",
        },
        "attempt_policy": {
            "attempts_per_case_per_lane": 1,
            "retry_condition": "documented transport/provider failure only",
            "retry_forbidden_reason": "obtaining a better result is fabrication of evidence",
        },
        "created_before_provider_call": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None, help="benchmark_run_id (default: derived from HEAD)")
    parser.add_argument("--check", action="store_true", help="verify OUTPUT_PATH matches a fresh generation")
    args = parser.parse_args()

    head_sha = _git_head_sha()
    run_id = args.run_id

    if args.check:
        if not OUTPUT_PATH.is_file():
            print(f"FAIL: {OUTPUT_PATH} does not exist", file=sys.stderr)
            return 1
        committed = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        run_id = committed["benchmark_run_id"]
        fresh = build_premanifest(run_id=run_id, head_sha=committed["source_master_sha"])
        if fresh != committed:
            print("DRIFT: benchmark_identity.pre.json does not match a fresh generation", file=sys.stderr)
            for key in fresh:
                if committed.get(key) != fresh[key]:
                    print(f"  field {key!r} differs", file=sys.stderr)
            return 1
        print(f"ok: benchmark_identity.pre.json is byte-identical (run_id={run_id})")
        return 0

    if run_id is None:
        run_id = f"agentreview-v2-benchmark-{head_sha[:12]}"

    manifest = build_premanifest(run_id=run_id, head_sha=head_sha)
    OUTPUT_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT_DIR)} (run_id={run_id}, source_master_sha={head_sha})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
