from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.profile_migration_v1_v2 import (
    ProfileMigrationDecisionV2,
    ProfileMigrationError,
    migrate_profile_v1_to_v2,
)
from app.agent_review.schemas import ArtifactDeclaration, TargetProfile


def _profile_v1(**overrides: object) -> TargetProfile:
    base: dict[str, object] = {
        "target_repo": "mglpsw/aiops-orchestrator",
        "artifacts": [
            ArtifactDeclaration(name="full-diff", path="artifacts/full.diff", kind="diff", required=True)
        ],
        "limitations": ["repo_profile_v1_baseline"],
    }
    base.update(overrides)
    return TargetProfile.model_validate(base)


def test_migrate_never_fabricates_required_checks_or_must_review() -> None:
    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.candidate["budgets"] is None
    assert report.candidate["must_review"] is None
    assert report.candidate["policies"]["required_checks"] is None  # type: ignore[index]
    assert report.candidate["contracts"] is None


def test_migrate_preserves_hard_boundaries_as_compliant_literals() -> None:
    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    policies = report.candidate["policies"]
    assert policies["network_policy"] == "forbidden"  # type: ignore[index]
    assert policies["fail_closed"] is True  # type: ignore[index]
    assert policies["redaction_required"] is True  # type: ignore[index]
    assert policies["allow_partial_coverage"] is False  # type: ignore[index]


def test_migrate_always_leaves_pending_decisions_for_a_v1_profile() -> None:
    """A v1 profile structurally lacks v2's required fields, so the
    candidate must never claim to be directly usable without human input."""

    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.pending_decisions
    assert report.candidate_is_directly_usable is False
    assert all(isinstance(item, ProfileMigrationDecisionV2) for item in report.pending_decisions)


def test_migrate_flags_max_bytes_and_maps_artifact_fields() -> None:
    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    artifacts = report.candidate["artifacts"]
    assert artifacts == [
        {
            "artifact_id": "full-diff",
            "path": "artifacts/full.diff",
            "kind": "diff",
            "required": True,
            "max_bytes": None,
        }
    ]
    assert any(
        decision.field == "artifacts[full-diff].max_bytes" for decision in report.pending_decisions
    )


def test_migrate_maps_every_v1_artifact_kind_directly_and_losslessly() -> None:
    """v1's ArtifactKind literal is exactly TargetArtifactV2.kind's literal
    set, so this mapping never needs a pending decision or a guess."""

    profile = TargetProfile.model_validate(
        {
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [
                {"name": f"artifact-{kind}", "path": f"a.{kind}", "kind": kind, "required": False}
                for kind in ("json", "yaml", "text", "markdown", "diff")
            ],
        }
    )
    report = migrate_profile_v1_to_v2(
        profile, repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    mapped_kinds = [entry["kind"] for entry in report.candidate["artifacts"]]  # type: ignore[index]
    assert mapped_kinds == ["json", "yaml", "text", "markdown", "diff"]
    assert not any(".kind" in decision.field for decision in report.pending_decisions)


def test_migrate_candidate_is_never_directly_valid_as_target_profile_v2() -> None:
    """Guards against a future regression where the candidate accidentally
    becomes 'complete enough' to slip past human review."""

    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    try:
        TargetProfileV2.model_validate(report.candidate)
    except Exception:
        pass
    else:
        raise AssertionError(
            "migration candidate unexpectedly validated as a complete TargetProfileV2"
        )


def test_migrate_never_touches_the_canonical_profile_on_disk(tmp_path) -> None:  # noqa: ANN001
    """migrate_profile_v1_to_v2 is a pure function: it takes no path and
    performs no I/O, so it cannot overwrite anything by construction."""

    import inspect

    source = inspect.getsource(migrate_profile_v1_to_v2)
    assert "open(" not in source
    assert "write_text" not in source
    assert "Path(" not in source


# -- post-merge findings (Codex review of PR #98) --------------------------


def test_migrate_never_leaks_a_secret_shaped_schema_version_in_the_error() -> None:
    """Post-merge finding (P2, Codex re-review of PR #101): the
    ProfileMigrationError raised for an unrecognized schema_version
    embedded the untrusted document's own value verbatim, and the CLI
    prints that message to stderr -- potentially persisting a
    secret-shaped value from an attacker- or accident-controlled profile
    into CI/operator logs. The error must describe the problem without
    echoing the rejected value."""

    unsafe_schema_version = "token=ghp_1234567890abcdef1234567890abcdef1234"
    profile = TargetProfile.model_validate(
        {
            "schema_version": unsafe_schema_version,
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [],
        }
    )
    with pytest.raises(ProfileMigrationError) as excinfo:
        migrate_profile_v1_to_v2(profile, repo="mglpsw/aiops-orchestrator", default_branch="master")
    assert unsafe_schema_version not in str(excinfo.value)


def test_migrate_rejects_an_unrecognized_v1_schema_version() -> None:
    """A profile claiming a schema_version other than the canonical v1
    identifier must be refused, not silently processed as if it were v1."""

    profile = TargetProfile.model_validate(
        {
            "schema_version": "some-other-schema.v7",
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [],
        }
    )
    with pytest.raises(ProfileMigrationError):
        migrate_profile_v1_to_v2(profile, repo="mglpsw/aiops-orchestrator", default_branch="master")


def test_migrate_flags_an_absolute_artifact_path_instead_of_carrying_it() -> None:
    profile = TargetProfile.model_validate(
        {
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [{"name": "full-diff", "path": "/etc/passwd", "kind": "diff", "required": True}],
        }
    )
    report = migrate_profile_v1_to_v2(
        profile, repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.candidate["artifacts"][0]["path"] is None  # type: ignore[index]
    assert any(
        decision.field == "artifacts[full-diff].path" for decision in report.pending_decisions
    )


def test_migrate_flags_a_traversal_artifact_path_instead_of_carrying_it() -> None:
    profile = TargetProfile.model_validate(
        {
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [{"name": "full-diff", "path": "../../etc/passwd", "kind": "diff", "required": True}],
        }
    )
    report = migrate_profile_v1_to_v2(
        profile, repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.candidate["artifacts"][0]["path"] is None  # type: ignore[index]
    assert any(
        decision.field == "artifacts[full-diff].path" for decision in report.pending_decisions
    )


def test_migrate_flags_an_unsafe_artifact_id_instead_of_carrying_it() -> None:
    profile = TargetProfile.model_validate(
        {
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [
                {"name": "full diff with spaces", "path": "artifacts/full.diff", "kind": "diff", "required": True}
            ],
        }
    )
    report = migrate_profile_v1_to_v2(
        profile, repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.candidate["artifacts"][0]["artifact_id"] is None  # type: ignore[index]
    # Referenced by position (#0), never by the unsafe raw name -- see the
    # dedicated leak-prevention test below.
    assert any(
        decision.field == "artifacts[#0].artifact_id" for decision in report.pending_decisions
    )


def test_migrate_never_leaks_an_unsafe_artifact_name_or_path_into_the_report() -> None:
    """Post-merge finding (P2, Codex review of PR #101): pending_decisions
    is persisted verbatim into the human-reviewable migration report. An
    unsafe artifact name/path could itself be a token-shaped credential or
    an absolute local path -- exactly the kind of value SafeIdentifier/
    RelativePath already reject -- so it must never be echoed back into
    `field` or `reason`."""

    # Contains "=", which SafeIdentifier's charset forbids -- unsafe, and
    # shaped like a leaked token/credential assignment.
    unsafe_name = "token=ghp_1234567890abcdef1234567890abcdef1234"
    unsafe_path = "/home/deploy/.ssh/id_rsa"
    profile = TargetProfile.model_validate(
        {
            "target_repo": "mglpsw/aiops-orchestrator",
            "artifacts": [
                {"name": unsafe_name, "path": unsafe_path, "kind": "diff", "required": True}
            ],
        }
    )
    report = migrate_profile_v1_to_v2(
        profile, repo="mglpsw/aiops-orchestrator", default_branch="master"
    )

    serialized = json.dumps(
        [asdict(decision) for decision in report.pending_decisions]
    )
    assert unsafe_name not in serialized
    assert unsafe_path not in serialized
    assert "ghp_1234567890abcdef1234567890abcdef1234" not in serialized
    # And the candidate itself must not carry the unsafe values either.
    assert report.candidate["artifacts"][0]["artifact_id"] is None  # type: ignore[index]
    assert report.candidate["artifacts"][0]["path"] is None  # type: ignore[index]


def test_migrate_accepts_a_safe_artifact_id_and_path_without_flagging_them() -> None:
    report = migrate_profile_v1_to_v2(
        _profile_v1(), repo="mglpsw/aiops-orchestrator", default_branch="master"
    )
    assert report.candidate["artifacts"][0]["artifact_id"] == "full-diff"  # type: ignore[index]
    assert report.candidate["artifacts"][0]["path"] == "artifacts/full.diff"  # type: ignore[index]
    assert not any(
        decision.field in ("artifacts[full-diff].artifact_id", "artifacts[full-diff].path")
        for decision in report.pending_decisions
    )
