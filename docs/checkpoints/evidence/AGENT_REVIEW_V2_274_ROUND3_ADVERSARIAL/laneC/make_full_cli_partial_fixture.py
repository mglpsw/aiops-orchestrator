from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.agent_review.operational_run_v2 import prepare_operational_review_v2
from app.agent_review.semantic_grouping_policy_v2 import SemanticGroupingPolicyV2
from tests.agent_review.test_operational_run_blackbox_e2e_v2 import (
    _checks_snapshot_file,
    _grouping_policy_file,
    _make_target_repo,
    _make_trusted_profile_root,
    _run_origin_file,
    _write_offline_responses,
)


root = Path("/tmp/ar274-review-r3/laneC/q7/full-cli")
root.mkdir(parents=True, exist_ok=False)
source, base_sha, head_sha = _make_target_repo(root)
profile_root = _make_trusted_profile_root(root)
grouping_file = _grouping_policy_file(root)
origin_file = _run_origin_file(root)
snapshot_file, toolchain_digest = _checks_snapshot_file(root)
grouping_policy = SemanticGroupingPolicyV2.model_validate(
    json.loads(grouping_file.read_text(encoding="utf-8"))
)
toolrepo_sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd="/opt/agent-tools/ar-200d-successor",
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
prepared = prepare_operational_review_v2(
    repo_root=source,
    target_profile_root=profile_root,
    grouping_policy=grouping_policy,
    base_sha=base_sha,
    head_sha=head_sha,
    tested_merge_sha=head_sha,
    pr_number=1,
    toolrepo_sha=toolrepo_sha,
    evidence_hash="d" * 64,
    max_lines_per_chunk=1000,
)
responses = root / "responses"
_write_offline_responses(responses, content=prepared.content, manifest=prepared.manifest)
subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=source, check=True)
subprocess.run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=source, check=True)
print(f"SOURCE={source}")
print(f"BASE={base_sha}")
print(f"HEAD={head_sha}")
print(f"PROFILE={profile_root}")
print(f"GROUPING={grouping_file}")
print(f"ORIGIN={origin_file}")
print(f"SNAPSHOT={snapshot_file}")
print(f"TOOLCHAIN_DIGEST={toolchain_digest}")
print(f"TOOLREPO_SHA={toolrepo_sha}")
print(f"RESPONSES={responses}")
