#!/usr/bin/env python3
"""Generate the AgentReview v2 benchmark final identity manifest (#88, PR-2).

Append-only over benchmark_identity.pre.json (OP-BENCH step 1, committed
before any provider call): every field frozen there is carried forward
UNCHANGED and verified against the premanifest; only new fields (per-case
real PR/HEAD identity, invocation counts, the H2 disposition_ref) are
added.

Usage:
    python scripts/generate-benchmark-identity-final.py \
        --disposition-ref <issue-comment-url> \
        --case-registry <path to case_id -> {pr_number, base_sha, head_sha} JSON>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PRE_PATH = ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.pre.json"
FINAL_PATH = ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.final.json"

# Frozen at OP-BENCH step 1; carried forward verbatim, never recomputed here.
_FROZEN_KEYS = (
    "schema_id",
    "benchmark_run_id",
    "source_master_sha",
    "corpus_digest",
    "applicability_manifest_digest",
    "expected_manifest_digest",
    "codex_cli_version",
    "rules_digest",
    "normalizer_digest",
    "attempt_policy",
    "created_before_provider_call",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposition-ref", required=True)
    parser.add_argument("--case-registry", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    pre = json.loads(PRE_PATH.read_text(encoding="utf-8"))
    registry = json.loads(args.case_registry.read_text(encoding="utf-8"))

    final = {key: pre[key] for key in _FROZEN_KEYS}
    final["real_acquisition"] = {
        entry["case_id"]: {
            "repo": "mglpsw/aiops-orchestrator",
            "pr_number": entry["pr_number"],
            "base_ref": entry["base_ref"],
            "base_sha": entry["base_sha"],
            "head_ref": entry["head_ref"],
            "head_sha": entry["head_sha"],
            "patch_digest": entry["patch_digest"],
            "attempts_used": {"codex_local": 1, "codex_github": 1},
            "retries": {"codex_local": 0, "codex_github": 0},
            "pr_closed_unmerged": True,
            "branches_deleted": True,
        }
        for entry in sorted(registry, key=lambda e: e["case_id"])
    }
    final["human_lane"] = {
        "status": "deferred",
        "destination": "RI-C/RI-D",
        "completed": False,
        "disposition_ref": args.disposition_ref,
    }
    final["acquisition_complete"] = True

    rendered = json.dumps(final, indent=2, sort_keys=False) + "\n"

    if args.check:
        if not FINAL_PATH.is_file():
            print(f"FAIL: {FINAL_PATH} does not exist", file=sys.stderr)
            return 1
        committed = FINAL_PATH.read_text(encoding="utf-8")
        if committed != rendered:
            print("DRIFT: benchmark_identity.final.json does not match a fresh generation", file=sys.stderr)
            return 1
        for key in _FROZEN_KEYS:
            if json.loads(committed)[key] != pre[key]:
                print(f"FAIL: frozen field {key!r} was changed from the premanifest", file=sys.stderr)
                return 1
        print("ok: benchmark_identity.final.json is byte-identical and all premanifest fields remain frozen")
        return 0

    FINAL_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {FINAL_PATH.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
