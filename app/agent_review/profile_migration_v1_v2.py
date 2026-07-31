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

from pydantic import TypeAdapter, ValidationError

from app.agent_review.contracts_v2 import RelativePath, SafeIdentifier, TARGET_PROFILE_SCHEMA_V2
from app.agent_review.schemas import TARGET_PROFILE_SCHEMA as TARGET_PROFILE_SCHEMA_V1
from app.agent_review.schemas import TargetProfile

_RELATIVE_PATH_ADAPTER: TypeAdapter[str] = TypeAdapter(RelativePath)
_SAFE_IDENTIFIER_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)


class ProfileMigrationError(ValueError):
    """Raised when the input document is not recognizable as a v1 profile
    at all -- not merely incomplete, which is what ``pending_decisions``
    is for."""


def _is_valid_relative_path(value: str) -> bool:
    try:
        _RELATIVE_PATH_ADAPTER.validate_python(value)
    except ValidationError:
        return False
    return True


def _is_valid_safe_identifier(value: str) -> bool:
    try:
        _SAFE_IDENTIFIER_ADAPTER.validate_python(value)
    except ValidationError:
        return False
    return True


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

    Raises ``ProfileMigrationError`` if ``profile_v1.schema_version`` is not
    the canonical v1 identifier -- an unrecognized schema is refused, never
    silently processed as if it were v1.
    """

    if profile_v1.schema_version != TARGET_PROFILE_SCHEMA_V1:
        # Never echo the untrusted document's own schema_version value here:
        # the CLI prints this message to stderr, and an attacker- or
        # accident-controlled profile could put a secret-shaped value in
        # that field. Only the known-safe expected constant is reported.
        raise ProfileMigrationError(
            f"profile schema_version does not match the expected v1 identifier "
            f"{TARGET_PROFILE_SCHEMA_V1!r}"
        )

    pending: list[ProfileMigrationDecisionV2] = []

    # v1's ArtifactKind literal ("json"/"yaml"/"text"/"markdown"/"diff") is
    # exactly TargetArtifactV2.kind's literal set, so this mapping is always
    # direct and lossless -- there is no ambiguous or unknown kind to flag.
    # artifact_id/path are NOT assumed safe just because v1 accepted them:
    # v1's ArtifactDeclaration.name/path are plain, unconstrained strings,
    # while v2's SafeIdentifier/RelativePath forbid things v1 never
    # rejected (spaces, absolute paths, traversal, Windows separators). An
    # unsafe value is never silently carried into the candidate -- it is
    # nulled out and flagged as a pending human decision instead.
    artifacts: list[dict[str, object]] = []
    for index, artifact in enumerate(profile_v1.artifacts):
        id_valid = _is_valid_safe_identifier(artifact.name)
        path_valid = _is_valid_relative_path(artifact.path)
        artifacts.append(
            {
                "artifact_id": artifact.name if id_valid else None,
                "path": artifact.path if path_valid else None,
                "kind": artifact.kind,
                "required": artifact.required,
                "max_bytes": None,
            }
        )
        # Reference the artifact by its safe name when available, or by
        # position otherwise. Never interpolate the raw name/path into
        # `field` or `reason`: either could be a token-shaped credential or
        # an absolute local path -- exactly what an unsafe value might be
        # -- and this report is meant to be human-reviewable, potentially
        # shared or committed output, not a place to persist rejected
        # sensitive material.
        artifact_ref = artifact.name if id_valid else f"#{index}"
        if not id_valid:
            pending.append(
                ProfileMigrationDecisionV2(
                    field=f"artifacts[{artifact_ref}].artifact_id",
                    reason=(
                        "v1 artifact name is not a safe v2 identifier; a human "
                        "must supply a corrected artifact_id"
                    ),
                )
            )
        if not path_valid:
            pending.append(
                ProfileMigrationDecisionV2(
                    field=f"artifacts[{artifact_ref}].path",
                    reason=(
                        "v1 path is not a safe v2 relative path (absolute, "
                        "traversal, or Windows-style separators); a human must "
                        "supply a corrected path"
                    ),
                )
            )
        pending.append(
            ProfileMigrationDecisionV2(
                field=f"artifacts[{artifact_ref}].max_bytes",
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
