"""Host-owned assembly and verification of promotable required checks.

`#201-C0`, C0-5. This module is the authority boundary.

## The union, and why the two halves never touch

```text
RequiredCheck = TrustedHostPromotion  U  AuthoritativeCIPromotion
```

`#201-B3` proved that isolating PR-controlled code does not make its success
signal authoritative: `controls(subject, success_signal) => not
authoritative(success_signal)`. So pytest -- and every other `subject_code`
check -- is permanently advisory from the isolated executor, and its
authoritative verdict has to come from deterministic CI instead.

The two paths are validated by separate functions with separate inputs, and
nothing joins them by `check_name`. A green GitHub check called `pytest` is not
evidence about an advisory executor result called `pytest`; they are different
events with different trust arguments. Conflating them would rebuild, in this
module, exactly the hole `#201-B3` closed in the executor.

## Authority is derived, never declared

There is no `authoritative=True` parameter anywhere, and no caller can hand a
sidecar to the gate and have it believed. `authority_effect="promotable"` is
the OUTPUT of the checks below -- base-owned policy, full producer identity,
run identity, origin-specific tested-tree binding, resolved conclusion -- not
an input. A `RequiredCheckProvenanceV2` that validates against its own digest
is merely well-formed; being well-formed is not being entitled.

## Binding is 1:1, in both directions, and the digest is only the join key

`verify_required_check_provenance_set_v2` requires exactly one provenance
record per check and exactly one check per provenance record, matched by
`required_check_digest`. It then re-checks `run_id`, `head_sha`, `repository`
and `source_kind` on every matched pair -- a record whose digest agrees but
whose run or head does not is still refused. Matching by `check_name` alone is
the `#217` defect and appears nowhere in this module.

## HEAD is not the tested tree

`RequiredCheckResultV2.head_sha` means "the PR this result belongs to" and
stays frozen. Which tree actually ran is a different fact, proven in the
sidecar: GitHub Actions `pull_request` workflows execute a synthetic merge
commit whose parents must be exactly `[base_sha, head_sha]`. That rule is
origin-specific and never applied blindly -- `pull_request_target`, `manual`
and `replay` have no such semantics and must carry an explicit binding
declared in the base-owned policy, or they are ineligible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.agent_review.authoritative_check_policy_v2 import (
    AuthoritativeCheckEntryV2,
    ExecutedTreeRuleV2,
    LoadedAuthoritativeCheckPolicyV2,
)
from app.agent_review.authoritative_ci_snapshot_v2 import (
    AuthoritativeCheckSnapshotV2,
    compute_observation_digest_v2,
    resolve_conclusion_v2,
    select_observation_v2,
)
from app.agent_review.contracts_v2 import (
    RequiredCheckResultV2,
    RunIdentityV2,
    RunOriginV2,
    compute_run_id,
)
from app.agent_review.required_check_provenance_v2 import (
    PROVENANCE_CONFIG_UNVERIFIED_REASON_V2,
    PROVENANCE_HEAD_MISMATCH_REASON_V2,
    PROVENANCE_INVALID_REASON_V2,
    PROVENANCE_MISSING_REASON_V2,
    PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2,
    PROVENANCE_PARENTAGE_MISMATCH_REASON_V2,
    PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2,
    PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2,
    PROVENANCE_REPOSITORY_MISMATCH_REASON_V2,
    PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2,
    PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2,
    PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2,
    PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2,
    PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    AuthorityEffectV2,
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
    RequiredCheckSourceKindV2,
    SemanticClassV2,
    build_required_check_provenance_v2,
    compute_required_check_digest_v2,
)
from app.agent_review.trusted_checks_v2 import (
    TrustedCheckPromotionError,
    TrustedCheckResultV2,
    promote_trusted_check_to_required_v2,
)
from app.common.strict_json import canonical_json_digest_hex

PROVENANCE_SCHEMA_FIELDS_V2 = {
    "schema_id": "agent-review.required-check-provenance.v2",
    "schema_version": 2,
    "source": "aiops-review-check-provenance",
}


@dataclass(frozen=True)
class PromotedRequiredCheckV2:
    """A required check together with the proof of where it came from.

    They travel as a pair because neither is meaningful alone: the result
    without provenance is the `#217` defect, and provenance without its result
    is an assertion about nothing."""

    result: RequiredCheckResultV2
    provenance: RequiredCheckProvenanceV2


# -- shared identity checks ---------------------------------------------------


def _require_run_identity_binding(
    *, identity: RunIdentityV2, snapshot_repository: str, snapshot_head_sha: str
) -> None:
    if snapshot_repository != identity.repo:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_REPOSITORY_MISMATCH_REASON_V2)
    if snapshot_head_sha != identity.head_sha:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_HEAD_MISMATCH_REASON_V2)


def _require_executed_tree_binding(
    *,
    snapshot: AuthoritativeCheckSnapshotV2,
    identity: RunIdentityV2,
    origin: RunOriginV2,
    entry: AuthoritativeCheckEntryV2,
) -> None:
    """Prove which tree the CI run actually executed -- per origin.

    Applying `pull_request`'s synthetic-merge parentage rule to every origin
    would be wrong in both directions: it would reject legitimate results whose
    origin has different semantics, and it would accept `replay`/`manual`
    results whose tested tree nobody ever bound. So the rule is looked up in
    the base-owned policy, and an origin the policy does not mention is
    unsupported rather than assumed."""

    rule = entry.origin_rules.get(origin.event_type)
    if rule is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2)

    if snapshot.tested_merge_sha != identity.tested_merge_sha:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2)
    # The tree that ran must be the tree the identity claims was tested. A
    # check attached to the right HEAD but executed against a different tree is
    # the subtlest version of this whole class of attack.
    if snapshot.executed_tree_sha != identity.tested_merge_sha:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2)

    if rule is ExecutedTreeRuleV2.SYNTHETIC_MERGE_PARENTAGE:
        # Order matters: [base, head], exactly. A merge whose parents are
        # reversed, or which has a third parent, or whose base is not the base
        # this run was computed against, is not the merge this review is about.
        if tuple(snapshot.tested_merge_parents) != (identity.base_sha, identity.head_sha):
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_PARENTAGE_MISMATCH_REASON_V2)


def _require_policy_entry(
    *, loaded_policy: LoadedAuthoritativeCheckPolicyV2, identity: RunIdentityV2, check_name: str
) -> AuthoritativeCheckEntryV2:
    if loaded_policy.policy.identity.repo != identity.repo:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_REPOSITORY_MISMATCH_REASON_V2)

    entry = loaded_policy.policy.entry_for(check_name)
    if entry is None:
        # No base-owned statement that anything may speak for this check.
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2)
    return entry


# -- Path B: authoritative CI -------------------------------------------------


def assemble_authoritative_ci_promotion_v2(
    *,
    check_name: str,
    snapshot: AuthoritativeCheckSnapshotV2,
    loaded_policy: LoadedAuthoritativeCheckPolicyV2,
    identity: RunIdentityV2,
    origin: RunOriginV2,
    toolchain_digest: str,
) -> PromotedRequiredCheckV2:
    """Derive a promotable required check from an observed CI run.

    Every step can only narrow: policy entry, repository/head binding,
    executed-tree binding, producer match, deterministic attempt selection,
    resolved conclusion. There is no branch that widens what is accepted, and
    no default that fills in a fact that was not proven."""

    entry = _require_policy_entry(loaded_policy=loaded_policy, identity=identity, check_name=check_name)

    _require_run_identity_binding(
        identity=identity,
        snapshot_repository=snapshot.acquisition.repository,
        snapshot_head_sha=snapshot.acquisition.head_sha,
    )
    _require_executed_tree_binding(snapshot=snapshot, identity=identity, origin=origin, entry=entry)

    observation = select_observation_v2(
        snapshot=snapshot, entry=entry, repository=identity.repo, head_sha=identity.head_sha
    )
    conclusion = resolve_conclusion_v2(observation)

    result = RequiredCheckResultV2(
        check_name=check_name,
        required=True,
        # Honest for deterministic CI, and the same value the trusted-host path
        # produces -- the frozen contract's meaning is unchanged by C0.
        deterministic=True,
        conclusion=conclusion,
        head_sha=identity.head_sha,
    )

    provenance = build_required_check_provenance_v2(
        **PROVENANCE_SCHEMA_FIELDS_V2,
        check_name=check_name,
        required_check_digest=compute_required_check_digest_v2(result),
        source_kind=RequiredCheckSourceKindV2.AUTHORITATIVE_CI,
        semantic_class=SemanticClassV2.AUTHORITATIVE,
        authority_effect=AuthorityEffectV2.PROMOTABLE,
        authority_transfer=False,
        repository=identity.repo,
        run_id=compute_run_id(identity),
        base_sha=identity.base_sha,
        head_sha=identity.head_sha,
        tested_merge_sha=identity.tested_merge_sha,
        event_type=origin.event_type,
        event_action=origin.event_action,
        verifier_identity=observation.app_slug,
        toolchain_digest=toolchain_digest,
        workflow_path=observation.workflow_path,
        workflow_ref=observation.workflow_ref,
        job_name=observation.check_run_name,
        ci_run_id=observation.workflow_run_id,
        ci_run_attempt=observation.run_attempt,
        observed_status=observation.status,
        observed_conclusion=observation.conclusion,
        observation_digest=compute_observation_digest_v2(observation),
        policy_source_bytes_digest=loaded_policy.policy_source_bytes_digest,
        policy_source_semantic_digest=loaded_policy.policy_source_semantic_digest,
    )

    return PromotedRequiredCheckV2(result=result, provenance=provenance)


# -- Path A: trusted host promotion -------------------------------------------


def assemble_trusted_host_promotion_v2(
    *,
    trusted_result: TrustedCheckResultV2,
    loaded_policy: LoadedAuthoritativeCheckPolicyV2,
    identity: RunIdentityV2,
    origin: RunOriginV2,
    toolchain_digest: str | None,
    host_owned_config_digest: str | None,
) -> PromotedRequiredCheckV2:
    """Derive a promotable required check from a `data_only_host_tool` result.

    `#201-B3` deliberately left two premises for C0 to close. Its own
    `classify_command_spec_v2` records that `host_owned_config: true` and an
    absolute executable are NECESSARY but not sufficient -- the config bytes
    and toolchain actually consumed must reproduce the digests the inventory is
    bound to. A declaration is not a proof, so a missing digest is refused here
    rather than assumed to have been checked upstream.

    The `TrustedCheckAuthorityV2.TRUSTED` and resolved-outcome requirements are
    NOT re-implemented: `promote_trusted_check_to_required_v2` remains the sole
    authority on those, and this function delegates to it."""

    if toolchain_digest is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2)
    if host_owned_config_digest is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_CONFIG_UNVERIFIED_REASON_V2)

    if trusted_result.head_sha != identity.head_sha:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_HEAD_MISMATCH_REASON_V2)
    if trusted_result.run_id != compute_run_id(identity):
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2)

    try:
        result = promote_trusted_check_to_required_v2(trusted_result)
    except TrustedCheckPromotionError as exc:
        # An advisory or environmental result is not promotable by any route.
        # Re-raised in this module's own family so the gate reports one
        # vocabulary, without inventing a new meaning for the refusal.
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2) from exc

    provenance = build_required_check_provenance_v2(
        **PROVENANCE_SCHEMA_FIELDS_V2,
        check_name=result.check_name,
        required_check_digest=compute_required_check_digest_v2(result),
        source_kind=RequiredCheckSourceKindV2.TRUSTED_HOST_PROMOTION,
        semantic_class=SemanticClassV2.AUTHORITATIVE,
        authority_effect=AuthorityEffectV2.PROMOTABLE,
        authority_transfer=False,
        repository=identity.repo,
        run_id=compute_run_id(identity),
        base_sha=identity.base_sha,
        head_sha=identity.head_sha,
        tested_merge_sha=identity.tested_merge_sha,
        event_type=origin.event_type,
        event_action=origin.event_action,
        verifier_identity="aiops-trusted-check-host",
        toolchain_digest=toolchain_digest,
        workflow_path=None,
        workflow_ref=None,
        job_name=None,
        ci_run_id=None,
        ci_run_attempt=None,
        observed_status=None,
        observed_conclusion=None,
        # For this path the "observation" is the trusted result together with
        # the host-owned config actually consumed. Both are folded in, so the
        # sidecar records the config proof `#201-B3` deferred here rather than
        # merely having required it at assembly time and then forgetting it.
        observation_digest=canonical_json_digest_hex(
            {
                "result_sha256": trusted_result.result_sha256,
                "host_owned_config_digest": host_owned_config_digest,
            }
        ),
        policy_source_bytes_digest=loaded_policy.policy_source_bytes_digest,
        policy_source_semantic_digest=loaded_policy.policy_source_semantic_digest,
    )

    return PromotedRequiredCheckV2(result=result, provenance=provenance)


# -- the gate-facing verifier -------------------------------------------------


def verify_required_check_provenance_set_v2(
    *,
    checks: Sequence[RequiredCheckResultV2],
    provenance: Sequence[RequiredCheckProvenanceV2],
    identity: RunIdentityV2,
    loaded_policy: LoadedAuthoritativeCheckPolicyV2 | None = None,
) -> None:
    """Refuse any required check that is not covered by authorised provenance.

    This is the check `#217` found missing: the quality gate matched required
    checks by NAME, so any object called `pytest` with `conclusion=success`
    satisfied it regardless of who built it.

    Raises `RequiredCheckProvenanceErrorV2` on the first failure. Deliberately
    total in both directions -- an unmatched check and an unmatched provenance
    record are both errors, because a spare record means the caller believes
    something about this run that the check set does not reflect.
    """

    run_id = compute_run_id(identity)

    digests = [compute_required_check_digest_v2(check) for check in checks]
    if len(set(digests)) != len(digests):
        # Two identical checks cannot be told apart by the join key, so a 1:1
        # binding is not expressible. Refused rather than silently deduplicated.
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2)

    by_digest: dict[str, RequiredCheckProvenanceV2] = {}
    for record in provenance:
        if record.required_check_digest in by_digest:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2)
        by_digest[record.required_check_digest] = record

    if len(by_digest) != len(checks):
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2)

    for check, digest in zip(checks, digests):
        record = by_digest.get(digest)
        if record is None:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_MISSING_REASON_V2)

        # The digest is the join key, not the whole binding. A record that
        # agrees on the check's bytes but describes a different run, head or
        # repository is describing someone else's evidence.
        if record.run_id != run_id:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2)
        if record.head_sha != identity.head_sha or record.head_sha != check.head_sha:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_HEAD_MISMATCH_REASON_V2)
        if record.repository != identity.repo:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_REPOSITORY_MISMATCH_REASON_V2)
        if record.base_sha != identity.base_sha or record.tested_merge_sha != identity.tested_merge_sha:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2)
        if record.check_name != check.check_name:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2)

        if record.authority_effect is not AuthorityEffectV2.PROMOTABLE:
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2)

        if loaded_policy is not None:
            _verify_against_policy(record=record, loaded_policy=loaded_policy)


def _verify_against_policy(
    *, record: RequiredCheckProvenanceV2, loaded_policy: LoadedAuthoritativeCheckPolicyV2
) -> None:
    """Re-check a CI record against the policy the gate itself loaded.

    The digests inside the record must be those of the policy bytes this run
    actually loaded -- otherwise a record assembled under a permissive older
    policy could be replayed against a tightened one."""

    if (
        record.policy_source_bytes_digest != loaded_policy.policy_source_bytes_digest
        or record.policy_source_semantic_digest != loaded_policy.policy_source_semantic_digest
    ):
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2)

    if record.source_kind is not RequiredCheckSourceKindV2.AUTHORITATIVE_CI:
        return

    entry = loaded_policy.policy.entry_for(record.check_name)
    if entry is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2)

    # Two distinct diagnoses, kept apart on purpose: the wrong APP produced it
    # (`producer_not_allowlisted`) is a different finding from the right app
    # running the wrong WORKFLOW or job (`workflow_identity_mismatch`), and
    # collapsing them would make a real attack harder to read in the logs.
    if record.verifier_identity != entry.verifier_identity:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2)
    if (
        record.workflow_path != entry.workflow_path
        or record.workflow_ref != entry.workflow_ref
        or record.job_name != entry.job_name
    ):
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2)
