from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)


def _entry(path: str = "target-profile.v2.yaml") -> GeneratedFileEntryV2:
    return GeneratedFileEntryV2(
        path=path, ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="a" * 64
    )


def _manifest(**overrides: object) -> TargetPackManifestV2:
    fields = {
        "schema_id": "agent-review.target-pack-manifest.v2",
        "schema_version": 2,
        "pack_version": "0.1.0",
        "toolrepo_sha": "1" * 40,
        "generated_files": (_entry(),),
        "schema_digests": {"agent-review.run.v2.schema.json": "b" * 64},
        "required_capabilities": ("router_transport",),
        "min_engine_contract_version": 2,
        "max_supported_rollout_mode": "shadow_minimal",
    }
    fields.update(overrides)
    return TargetPackManifestV2(**fields)


def test_a_valid_manifest_constructs() -> None:
    manifest = _manifest()
    assert manifest.pack_version == "0.1.0"


def test_duplicate_generated_paths_are_refused() -> None:
    with pytest.raises(ValidationError):
        _manifest(generated_files=(_entry("a.yaml"), _entry("a.yaml")))


def test_empty_generated_files_is_refused() -> None:
    with pytest.raises(ValidationError):
        _manifest(generated_files=())


def test_empty_schema_digests_is_refused() -> None:
    with pytest.raises(ValidationError):
        _manifest(schema_digests={})


def test_digest_is_deterministic_and_order_independent_over_dict_field() -> None:
    a = _manifest(schema_digests={"x.json": "b" * 64, "y.json": "c" * 64})
    b = _manifest(schema_digests={"y.json": "c" * 64, "x.json": "b" * 64})
    assert compute_target_pack_manifest_digest_v2(a) == compute_target_pack_manifest_digest_v2(b)


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.yaml", "/etc/passwd", "a/../../b", "~/config", "a//b", "a/./b"],
)
def test_a_generated_file_entry_rejects_a_traversal_or_absolute_path(bad_path: str) -> None:
    """P-T2 (spec `§10`): reuses `contracts_v2.RelativePath`'s own
    fail-closed normalized-POSIX-relative-path validator verbatim -- this
    contract never re-implements path safety, it only inherits it."""

    with pytest.raises(ValidationError):
        GeneratedFileEntryV2(
            path=bad_path, ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="a" * 64
        )


def test_digest_changes_when_content_changes() -> None:
    a = _manifest(pack_version="0.1.0")
    b = _manifest(pack_version="0.2.0")
    assert compute_target_pack_manifest_digest_v2(a) != compute_target_pack_manifest_digest_v2(b)
