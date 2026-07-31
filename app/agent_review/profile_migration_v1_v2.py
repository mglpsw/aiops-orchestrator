"""Explicit, non-destructive v1 -> v2 target-profile migrator.

This module is a tool, never an automatic step of a review run. Given a
validated v1 ``TargetProfile``, it produces:

* a *candidate* v2-shaped mapping -- deliberately not guaranteed to
  validate as ``TargetProfileV2``, since v1 profiles structurally lack the
  fields v2 requires (budgets, must-review rules, required checks, typed
  contract references with hashes). Fields with no safe v1 equivalent are
  left ``None`` rather than fabricated or guessed;
* a report of every field that needs an explicit human decision before the
  candidate can become a real profile.

Nothing here writes to the canonical profile path, and nothing here is
invoked from the review pipeline itself -- only from
``scripts/migrate-agent-review-profile-v1-v2.py``, an explicit CLI tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent_review.contracts_v2 import TARGET_PROFILE_SCHEMA_V2
from app.agent_review.schemas import TargetProfile


@dataclass(frozen=True)
class ProfileMigrationDecisionV2:
    """One field the candidate cannot resolve automatically."""

    field: str
    reason: str


@dataclass(frozen=True)
class ProfileMigrationReportV2:
    source_schema_version: str
    candidate: dict[str, object]
    pending_decisions: tuple[ProfileMigrationDecisionV2, ...] = field(default_factory=tuple)

    @property
    def candidate_is_directly_usable(self) -> bool:
        """False whenever any pending decision remains -- a v1 profile
        never migrates directly into a usable v2 profile without human
        input, by design."""

        return len(self.pending_decisions) == 0


def migrate_profile_v1_to_v2(
    profile_v1: TargetProfile,
    *,
    repo: str,
    default_branch: str,
) -> ProfileMigrationReportV2:
    """Produce a v2 migration candidate and its pending-decision report.

    ``repo``/``default_branch`` must come from the trusted base/default
    checkout the migrator tool is run against -- v1's ``TargetProfile``
    carries only an optional, free-text ``target_repo`` that this function
    does not trust as a v2 ``TargetIdentityV2.repo`` value.
    """

    pending: list[ProfileMigrationDecisionV2] = []

    # v1's ArtifactKind literal ("json"/"yaml"/"text"/"markdown"/"diff") is
    # exactly TargetArtifactV2.kind's literal set, so this mapping is always
    # direct and lossless -- there is no ambiguous or unknown kind to flag.
    artifacts: list[dict[str, object]] = []
    for artifact in profile_v1.artifacts:
        artifacts.append(
            {
                "artifact_id": artifact.name,
                "path": artifact.path,
                "kind": artifact.kind,
                "required": artifact.required,
                "max_bytes": None,
            }
        )
        pending.append(
            ProfileMigrationDecisionV2(
                field=f"artifacts[{artifact.name}].max_bytes",
                reason="v1 never bounded artifact size; a human must set an explicit byte cap",
            )
        )

    for field_name, reason in (
        (
            "budgets",
            "v1 has no chunk-budget model; a human must size "
            "max_chunks/total_prompt_chars/max_chars_per_chunk/"
            "max_files_per_chunk/max_contracts_per_chunk",
        ),
        (
            "must_review",
            "v1 has no must-review path/pattern contract; a human must "
            "define paths, patterns, and the covering artifact_ids",
        ),
        (
            "policies.required_checks",
            "v1 has no required-checks contract; fabricating one is "
            "forbidden, and TargetPoliciesV2 requires at least one",
        ),
        (
            "policies.allowed_semantic_groups",
            "v1 does not scope semantic groups; a human must choose the "
            "allowed set",
        ),
        (
            "policies.coverage_failure_state",
            "no v1 equivalent; a human must choose blocked_pipeline or "
            "manual_required",
        ),
        (
            "policies.model_uncertainty_state",
            "no v1 equivalent",
        ),
        (
            "contracts",
            "v1 domain_contracts/review_packs are untyped blobs; each v2 "
            "contract reference needs an explicit contract_id, version, "
            "and sha256 that this tool will not compute unattended",
        ),
    ):
        pending.append(ProfileMigrationDecisionV2(field=field_name, reason=reason))

    candidate: dict[str, object] = {
        "schema_id": TARGET_PROFILE_SCHEMA_V2,
        "schema_version": 2,
        "source": "repo-profile",
        "identity": {"repo": repo, "default_branch": default_branch},
        "artifacts": artifacts,
        "budgets": None,
        "must_review": None,
        "policies": {
            "network_policy": "forbidden",
            "fail_closed": True,
            "redaction_required": True,
            "allow_partial_coverage": False,
            "required_checks": None,
            "allowed_semantic_groups": None,
            "coverage_failure_state": None,
            "model_uncertainty_state": None,
        },
        "contracts": None,
        "limitations": list(profile_v1.limitations),
    }

    return ProfileMigrationReportV2(
        source_schema_version=profile_v1.schema_version,
        candidate=candidate,
        pending_decisions=tuple(pending),
    )
