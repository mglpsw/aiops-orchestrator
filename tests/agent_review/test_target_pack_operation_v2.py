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
import inspect
import re
from pathlib import Path

import pytest

from app.agent_review.schema_export_v2 import render_v2_json_schemas
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_operation_v2 import compute_target_pack_operation_plan_v2
from app.agent_review.target_pack_plan_v2 import (
    PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    PlanError,
    compute_install_plan_v2,
)

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


# --- H1A-R1: symlink escape on the init/preview read path ------------------


def _plan(target_root: Path):
    return compute_target_pack_operation_plan_v2(
        manifest=_manifest(),
        target_root=target_root,
        target_repo="acme/widget",
        rollout="off",
        seed_content_by_path={".aiops/target-profile.v2.yaml": _VALID_PROFILE_YAML},
        previous_receipt=None,
    )


def test_init_preview_refuses_a_target_owned_file_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The independent review of PR #230 correctly noted that protecting
    only `run_doctor_v2` would be insufficient: `init`'s own preview reads
    observed TARGET_OWNED bytes through the same unprotected composition.
    Reproduced before the fix; refused after."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_bytes(_VALID_PROFILE_YAML)
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)

    with pytest.raises(PlanError) as exc_info:
        _plan(target_root)
    assert exc_info.value.reason_code == PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_install_plan_refuses_a_generated_path_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`compute_install_plan_v2`'s drift-hash read is the third reachable
    site of the same class -- also named in the independent review."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_bytes(_VALID_PROFILE_YAML)
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)

    with pytest.raises(PlanError) as exc_info:
        compute_install_plan_v2(manifest=_manifest(), target_root=target_root, previous_receipt=None)
    assert exc_info.value.reason_code == PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_one_operation_binds_the_install_plan_and_its_evidence_to_a_single_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED, round 3. `compute_target_pack_operation_plan_v2` used to
    resolve `target_root` for its own TARGET_OWNED loop AND separately
    call `compute_install_plan_v2`, which resolved again -- two
    independent resolutions inside one logical operation, contradicting
    the "resolved exactly once per operation" property
    `resolve_within_target_root_v2`'s docstring claims.

    Reproduced before the fix: swapping `target_root` between the two
    resolutions produced ONE preview whose `install_plan.target_root_real`
    named rootA while its `after_hashes` -- and therefore the
    `target_profile_hash` and receipt built from them -- were read from
    rootB. An install description and the evidence describing it
    disagreeing about which target they refer to is precisely the identity
    property `#203`'s receipt contract exists to guarantee.

    After the fix the operation resolves once and binds the plan to that
    resolution, so a mid-operation root swap makes the subsequent read
    fall outside the bound root and fail closed instead of silently
    producing a self-inconsistent plan."""
    root_a = tmp_path_factory.mktemp("root-a")
    root_b = tmp_path_factory.mktemp("root-b")
    for root, suffix in ((root_a, b""), (root_b, b"\n# divergent-marker\n")):
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_bytes(_VALID_PROFILE_YAML + suffix)
    live = tmp_path_factory.mktemp("live-parent") / "live"
    live.symlink_to(root_a, target_is_directory=True)

    import app.agent_review.target_pack_operation_v2 as operation_module

    real_compute = operation_module.compute_install_plan_v2

    def racing_compute(**kwargs: object):
        # Root points at B for exactly the duration of the inner plan
        # computation, then back at A. If the inner call resolves
        # independently it captures B; if it is bound to the root this
        # operation already resolved, it stays A. The surrounding loop
        # sees A either way, so the ONLY observable difference is whether
        # the plan and the evidence agree.
        live.unlink()
        live.symlink_to(root_b, target_is_directory=True)
        try:
            return real_compute(**kwargs)  # type: ignore[arg-type]
        finally:
            live.unlink()
            live.symlink_to(root_a, target_is_directory=True)

    monkeypatch.setattr(operation_module, "compute_install_plan_v2", racing_compute)

    # Bound to a single root, the mid-operation swap is DETECTED and the
    # whole preview fails closed. Unbound (the pre-fix behaviour), the
    # inner call silently resolves root B, the outer loop reads root A,
    # and a self-inconsistent plan is returned with no error at all --
    # which is exactly what makes this a real defect rather than a
    # cosmetic one.
    with pytest.raises(PlanError) as exc_info:
        compute_target_pack_operation_plan_v2(
            manifest=_manifest(),
            target_root=live,
            target_repo="acme/widget",
            rollout="off",
            seed_content_by_path={".aiops/target-profile.v2.yaml": _VALID_PROFILE_YAML},
            previous_receipt=None,
        )
    assert exc_info.value.reason_code == PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_the_containment_boundary_is_never_a_caller_supplied_value(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """RED, round 4. The first version of the round-3 fix let a caller pass
    an already-resolved `target_root_real` into `compute_install_plan_v2`
    so the operation could bind the plan to its own resolution. That made
    the containment BOUNDARY itself caller-supplied, and it was
    reproducibly widenable: passing any ancestor of the real
    `target_root` (its parent directory) made every containment check
    evaluate against the wider root, so a symlink inside `target_root`
    pointing at a SIBLING directory passed containment and its content was
    read -- while `plan.target_root_real` recorded the wider root.

    Reproduced before the fix: with the correct root the layout below is
    refused; with the parent passed as `target_root_real`, the read of
    `<parent>/sibling/leak.txt` succeeded.

    A security primitive must not depend on callers passing the right
    boundary, so the parameter no longer exists at all -- this test locks
    that structurally, and the layout below stays refused because the
    function resolves its own root."""
    parent = tmp_path_factory.mktemp("parent")
    target = parent / "target"
    (target / ".aiops").mkdir(parents=True)
    sibling = parent / "sibling"
    sibling.mkdir()
    (sibling / "leak.txt").write_bytes(b"sibling content outside the target")
    (target / ".aiops" / "owned.txt").symlink_to(sibling / "leak.txt")

    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/owned.txt",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=(),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )

    # Structural: there is no boundary parameter to widen.
    assert "target_root_real" not in inspect.signature(compute_install_plan_v2).parameters

    # Behavioural: the sibling-escaping symlink is refused on its own merits.
    with pytest.raises(PlanError) as exc_info:
        compute_install_plan_v2(manifest=manifest, target_root=target, previous_receipt=None)
    assert exc_info.value.reason_code == PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_init_preview_allows_a_symlink_resolving_back_inside_target_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Containment, not symlink prohibition -- the same single policy the
    writer (`target_pack_install_v2`) enforces."""
    target_root = tmp_path_factory.mktemp("target")
    (target_root / ".aiops").mkdir()
    real_profile = target_root / ".aiops" / "real-profile.yaml"
    real_profile.write_bytes(_VALID_PROFILE_YAML)
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(real_profile)

    result = _plan(target_root)

    assert result.plan.after_hashes == {
        ".aiops/target-profile.v2.yaml": hashlib.sha256(_VALID_PROFILE_YAML).hexdigest()
    }
