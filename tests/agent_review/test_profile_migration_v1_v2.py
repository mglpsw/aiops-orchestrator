from __future__ import annotations

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.profile_migration_v1_v2 import (
    ProfileMigrationDecisionV2,
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
