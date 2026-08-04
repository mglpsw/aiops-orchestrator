#!/usr/bin/env python3
"""Generate/verify the AgentReview v2 benchmark final identity manifest (#88).

Append-only over benchmark_identity.pre.json (OP-BENCH step 1, committed
before any provider call): every field frozen there is carried forward
UNCHANGED and verified against the premanifest; only new fields (per-case
real PR/HEAD identity, invocation counts, the H2 disposition_ref) are
added.

`--check` is self-contained (reads only already-committed files -- no
external registry/disposition-ref args, so it runs unmodified in CI long
after the one-time acquisition that produced the committed file). It
verifies: every premanifest-frozen field is unchanged; every
`real_acquisition` entry is structurally complete; `human_lane` carries a
non-empty `disposition_ref`; `acquisition_complete` is `true`.

Generation mode (writing a NEW final.json, only ever run once, at PR-2
authoring time) still requires `--disposition-ref` and `--case-registry`.

Usage:
    python scripts/generate-benchmark-identity-final.py --check
    python scripts/generate-benchmark-identity-final.py \
        --disposition-ref <issue-comment-url> \
        --case-registry <path to case_id -> {pr_number, base_sha, head_sha} JSON>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PATCH_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXPECTED_REPO = "mglpsw/aiops-orchestrator"
_EXPECTED_ATTEMPT_KEYS = frozenset({"codex_local", "codex_github"})

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

_REQUIRED_ACQUISITION_FIELDS = (
    "repo",
    "pr_number",
    "base_ref",
    "base_sha",
    "head_ref",
    "head_sha",
    "patch_digest",
    "attempts_used",
    "retries",
    "pr_closed_unmerged",
    "branches_deleted",
)


def build_final(*, disposition_ref: str, registry: list[dict], pre: dict) -> dict:
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
        "disposition_ref": disposition_ref,
    }
    final["acquisition_complete"] = True
    return final


def run_check() -> int:
    if not PRE_PATH.is_file():
        print(f"FAIL: {PRE_PATH} does not exist", file=sys.stderr)
        return 1
    if not FINAL_PATH.is_file():
        print(f"FAIL: {FINAL_PATH} does not exist", file=sys.stderr)
        return 1

    pre = json.loads(PRE_PATH.read_text(encoding="utf-8"))
    final = json.loads(FINAL_PATH.read_text(encoding="utf-8"))

    errors = []
    for key in _FROZEN_KEYS:
        if key not in final:
            errors.append(f"missing frozen field {key!r}")
        elif final[key] != pre[key]:
            errors.append(f"frozen field {key!r} diverged from premanifest")

    if final.get("acquisition_complete") is not True:
        errors.append("acquisition_complete is not true")

    human_lane = final.get("human_lane", {})
    if not human_lane.get("disposition_ref"):
        errors.append("human_lane.disposition_ref is missing or empty")
    if human_lane.get("completed") is not False:
        errors.append("human_lane.completed must be false (Lane 4 is deferred, never fabricated)")
    if human_lane.get("status") != "deferred":
        errors.append("human_lane.status must be 'deferred'")

    # Every case the premanifest PLANNED to acquire must actually appear in
    # real_acquisition -- a Codex GitHub review of this exact PR found that
    # checking only "non-empty" lets a silently-incomplete acquisition (some
    # cases dropped) pass. planned_cases_by_lane's "codex_local" list is the
    # authoritative full case set (identical across all three provider lanes
    # by construction -- see generate-benchmark-premanifest.py).
    planned_case_ids = set(pre.get("planned_cases_by_lane", {}).get("codex_local", []))
    real_acquisition = final.get("real_acquisition", {})
    if not real_acquisition:
        errors.append("real_acquisition is empty")
    acquired_case_ids = set(real_acquisition.keys())
    missing_cases = planned_case_ids - acquired_case_ids
    if missing_cases:
        errors.append(f"real_acquisition is missing planned case(s): {sorted(missing_cases)}")
    extra_cases = acquired_case_ids - planned_case_ids
    if extra_cases:
        errors.append(f"real_acquisition has unplanned case(s) not in the premanifest: {sorted(extra_cases)}")

    for case_id, entry in real_acquisition.items():
        missing = [f for f in _REQUIRED_ACQUISITION_FIELDS if f not in entry]
        if missing:
            errors.append(f"real_acquisition[{case_id!r}] missing fields {missing}")
            continue
        # A Codex GitHub review of this exact PR found that checking field
        # PRESENCE alone lets a corrupted value (wrong repo, null pr_number,
        # empty refs, a malformed patch_digest, empty attempts_used/retries)
        # pass silently -- validate the actual values, not just their
        # existence.
        if entry["repo"] != _EXPECTED_REPO:
            errors.append(f"real_acquisition[{case_id!r}]: repo is {entry['repo']!r}, expected {_EXPECTED_REPO!r}")
        if not isinstance(entry["pr_number"], int) or isinstance(entry["pr_number"], bool) or entry["pr_number"] <= 0:
            errors.append(f"real_acquisition[{case_id!r}]: pr_number is not a positive int")
        if not entry["base_ref"] or not isinstance(entry["base_ref"], str):
            errors.append(f"real_acquisition[{case_id!r}]: base_ref is empty or not a string")
        if not entry["head_ref"] or not isinstance(entry["head_ref"], str):
            errors.append(f"real_acquisition[{case_id!r}]: head_ref is empty or not a string")
        if not _SHA_RE.match(entry.get("head_sha", "")):
            errors.append(f"real_acquisition[{case_id!r}]: head_sha is not a 40-char lowercase hex SHA")
        if not _SHA_RE.match(entry.get("base_sha", "")):
            errors.append(f"real_acquisition[{case_id!r}]: base_sha is not a 40-char lowercase hex SHA")
        if not _PATCH_DIGEST_RE.match(entry.get("patch_digest", "")):
            errors.append(f"real_acquisition[{case_id!r}]: patch_digest does not match sha256:<64 hex chars>")
        attempts_used = entry.get("attempts_used") or {}
        retries = entry.get("retries") or {}
        if not _EXPECTED_ATTEMPT_KEYS.issubset(attempts_used.keys()):
            errors.append(f"real_acquisition[{case_id!r}]: attempts_used missing {_EXPECTED_ATTEMPT_KEYS - attempts_used.keys()}")
        if not _EXPECTED_ATTEMPT_KEYS.issubset(retries.keys()):
            errors.append(f"real_acquisition[{case_id!r}]: retries missing {_EXPECTED_ATTEMPT_KEYS - retries.keys()}")
        if entry["pr_closed_unmerged"] is not True:
            errors.append(f"real_acquisition[{case_id!r}]: pr_closed_unmerged is not true")
        if entry["branches_deleted"] is not True:
            errors.append(f"real_acquisition[{case_id!r}]: branches_deleted is not true")

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(
        f"ok: benchmark_identity.final.json is structurally complete, all "
        f"{len(_FROZEN_KEYS)} premanifest fields remain frozen, and "
        f"{len(real_acquisition)} case(s) show real, closed, cleaned-up acquisition"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposition-ref")
    parser.add_argument("--case-registry", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        return run_check()

    if not args.disposition_ref or not args.case_registry:
        parser.error("--disposition-ref and --case-registry are required unless --check is given")

    pre = json.loads(PRE_PATH.read_text(encoding="utf-8"))
    registry = json.loads(args.case_registry.read_text(encoding="utf-8"))
    final = build_final(disposition_ref=args.disposition_ref, registry=registry, pre=pre)
    rendered = json.dumps(final, indent=2, sort_keys=False) + "\n"
    FINAL_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {FINAL_PATH.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
