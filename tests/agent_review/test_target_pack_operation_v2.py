"""Focused unit coverage for `compute_target_pack_operation_plan_v2`
(`#203`, `target_pack_operation_v2.py`). Previously only exercised via CLI
subprocess E2E (`test_agent_review_target_pack_v2_cli.py`); this file adds
direct library-level coverage for the identity boundary the CLI wraps.

Post-merge review debt (aiops-orchestrator#205, C1): `before_hashes`/
`after_hashes` used `SafeText` keys with an explicit
`additionalProperties: false` schema override and no `patternProperties`
-- the exported schema could only ever validate `{}`, while
`compute_target_pack_operation_plan_v2` populates both maps on every real
preview. See `test_target_pack_receipt_v2.py` for the equivalent coverage
on `TargetInstallReceiptV2.target_owned_file_hashes`, which had the
identical defect.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.agent_review.schema_export_v2 import render_v2_json_schemas
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_operation_v2 import compute_target_pack_operation_plan_v2

_VALID_PROFILE_YAML = b"""
schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: acme/widget
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
budgets:
  max_chunks: 16
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths: []
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


def _manifest() -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


def test_a_fresh_init_preview_produces_a_non_empty_after_hashes(tmp_path: Path) -> None:
    """RED for C1 at the source: this is the exact code path that writes
    `after_hashes` on every real `init` preview."""

    result = compute_target_pack_operation_plan_v2(
        manifest=_manifest(),
        target_root=tmp_path / "fresh-target",
        target_repo="acme/widget",
        rollout="off",
        seed_content_by_path={".aiops/target-profile.v2.yaml": _VALID_PROFILE_YAML},
        previous_receipt=None,
    )
    assert result.plan.after_hashes == {
        ".aiops/target-profile.v2.yaml": hashlib.sha256(_VALID_PROFILE_YAML).hexdigest()
    }
    assert result.plan.before_hashes == {}


def test_operation_plan_schema_can_represent_the_real_non_empty_after_hashes(tmp_path: Path) -> None:
    """Same schema-representability proof as the receipt's equivalent
    test, at the operation-plan level."""

    result = compute_target_pack_operation_plan_v2(
        manifest=_manifest(),
        target_root=tmp_path / "fresh-target",
        target_repo="acme/widget",
        rollout="off",
        seed_content_by_path={".aiops/target-profile.v2.yaml": _VALID_PROFILE_YAML},
        previous_receipt=None,
    )
    plan_json = result.plan.model_dump(mode="json")
    schema = render_v2_json_schemas()["agent-review.target-pack-operation-plan.v2.schema.json"]

    for field_name in ("before_hashes", "after_hashes"):
        field_schema = schema["properties"][field_name]
        assert field_schema.get("additionalProperties") is False
        assert "patternProperties" in field_schema
        key_pattern = next(iter(field_schema["patternProperties"]))
        value_pattern = field_schema["patternProperties"][key_pattern]["pattern"]
        for key, value in plan_json[field_name].items():
            assert re.fullmatch(key_pattern, key)
            assert re.fullmatch(value_pattern, value)


def test_operation_plan_schema_still_closed_to_a_key_outside_the_pattern() -> None:
    schema = render_v2_json_schemas()["agent-review.target-pack-operation-plan.v2.schema.json"]
    key_pattern = next(iter(schema["properties"]["after_hashes"]["patternProperties"]))
    assert not re.fullmatch(key_pattern, "a[b].py")
