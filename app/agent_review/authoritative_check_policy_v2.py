"""Base-owned policy naming which CI producer may speak for a required check.

`#201-C0`, C0-3.

## Why this file exists at all

`#201-B3` leaves subject-code checks (pytest and friends) permanently
advisory, so their authoritative verdict must come from deterministic CI. But
"a green GitHub check named pytest" is not an authority -- a pull request can
add a workflow, name a job `pytest`, and make it exit 0. Something base-owned
has to say, in advance, exactly which producer is entitled to speak for each
required check.

## A check name plus a workflow filename is not identity

`pytest: {workflow: ci.yml}` is spoofable three ways: a different job in the
same file, a different producer app, or the same file resolved from a ref the
PR controls. So an entry is a complete tuple -- `check_name`, `workflow_path`,
`workflow_ref`, `job_name`, `verifier_identity` -- with no defaults and no
wildcards. A snapshot record must match every field to survive; matching
`check_name` alone is the `#217` defect and is never sufficient.

## Target-owned instance, base-owned reading

The policy lives in the TARGET repository, next to the target profile, because
the engine must stay repository-neutral -- no table in this orchestrator keyed
by repository name (`#199` forbids exactly that). What makes it trustworthy is
not where it lives but WHEN it is read: `load_authoritative_check_policy_v2`
must be pointed at the trusted base/default checkout, never the PR head. A
pull request may therefore change the policy only for FUTURE runs, once the
change is itself part of the trusted base.

`#203` (target pack) owns the schema, template, upgrade and conformance path
for this file; each target maintains only its own instance.

## Two digests, both recorded

`policy_source_bytes_digest` is the raw file bytes; `policy_source_semantic_digest`
is the canonical JSON of the validated model. CAEM 3.0 vocabulary, adopted
rather than paraphrased: a reformat moves the byte digest only, a semantic
change moves both. The digests that reach the provenance sidecar are those of
the bytes ACTUALLY LOADED for the run, never of a canonical or expected copy.

## Not configurable here, on purpose

The GitHub-conclusion mapping is closed and lives in
`authoritative_ci_snapshot_v2`. A target cannot widen it. If
`permitted_conclusions` were a free set, a target could permit `neutral` or
`skipped` and quietly turn a non-verdict into a passing required check --
which is the failure mode this whole slice exists to remove.
"""

from __future__ import annotations

import json
from collections.abc import Hashable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    RelativePath,
    Repository,
    SafeIdentifier,
    SafeText,
    TargetProfileV2,
)
from app.agent_review.authoritative_producer_evidence_v2 import (
    ProducerKindV2,
    ProducerWorkflowIdentityV2,
)
from app.common.strict_json import canonical_json_digest_hex, raw_bytes_digest_hex

AUTHORITATIVE_CHECK_POLICY_SCHEMA_V2 = "agent-review.authoritative-check-policy.v2"

DEFAULT_AUTHORITATIVE_CHECK_POLICY_RELATIVE_PATH = Path(".aiops") / "authoritative-checks.v2.yaml"

POLICY_MISSING_REASON_V2 = "authoritative_check_policy_missing"
POLICY_UNREADABLE_REASON_V2 = "authoritative_check_policy_unreadable"
POLICY_INVALID_REASON_V2 = "authoritative_check_policy_invalid"
POLICY_PROFILE_MISMATCH_REASON_V2 = "authoritative_check_policy_profile_mismatch"
POLICY_REQUIRED_CHECK_UNCOVERED_REASON_V2 = "authoritative_check_policy_required_check_uncovered"
POLICY_ENTRY_NOT_REQUIRED_REASON_V2 = "authoritative_check_policy_entry_not_required"
POLICY_WORKFLOW_REF_NOT_BASE_OWNED_REASON_V2 = "authoritative_check_policy_workflow_ref_not_base_owned"


class AuthoritativeCheckPolicyErrorV2(ValueError):
    """Carries a stable `reason_code` only -- never raw YAML, the original
    exception text, or a local path."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _construct_mapping_rejecting_duplicates_v2(loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False):
    """Refuse a YAML mapping with a repeated key, instead of PyYAML's default
    of silently keeping the last one.

    This is an authorization document: an auditor, or a different YAML
    implementation, reading the same bytes could see a different value for a
    duplicated key than the one this loader used -- for example a different
    `verifier_identity` -- and end up validating a producer this loader did
    not.

    Keys are compared AFTER construction, so `yes:`/`true:` and `1:`/`1.0:`
    are already caught here -- they collapse to one key in `mapping`.

    The `isinstance` guard below is PyYAML's own, restored. Replacing the
    mapping constructor removed it, so an explicitly tagged non-mapping
    node (`identity: !!map foo`, `!!map [1, 2]`) reached the unpacking loop
    and raised a raw `ValueError`/`TypeError` that escaped this module's
    `(yaml.YAMLError, UnicodeDecodeError)` boundary entirely -- reaching
    `required_check_readiness_v2` as a traceback. Same escaping class the
    profile loader had; found by sweeping for it rather than by report.

    Merge keys remain UNSUPPORTED here, deliberately and unchanged: this
    loader registers no merge constructor, so `<<:` still fails closed with
    a typed `ConstructorError`. Making merges work is a capability decision
    about the authoritative-policy language, not a side effect of fixing an
    exception boundary."""

    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, f"expected a mapping node, but found {node.id}", node.start_mark
        )

    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        # PyYAML's second guard, also lost when the constructor was
        # replaced: an unhashable key (`? [a, b]`) otherwise reached the
        # membership test below and raised a raw `TypeError`. Found by the
        # totality family test, not by report -- which is the point of
        # asserting the boundary as a family rather than per named input.
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, "found unhashable key", key_node.start_mark
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


class _DuplicateKeyRejectingLoaderV2(yaml.SafeLoader):
    """`yaml.SafeLoader`, except every mapping refuses a duplicate key."""


_DuplicateKeyRejectingLoaderV2.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_rejecting_duplicates_v2
)


class ExecutedTreeRuleV2(str, Enum):
    """How the tree a CI run actually executed is bound to the run identity.

    `SYNTHETIC_MERGE_PARENTAGE` is GitHub's `pull_request` semantics: the run
    executes a synthetic merge commit whose parents are exactly
    `[base_sha, head_sha]`. `EXPLICIT_TESTED_TREE` covers every other origin,
    where that assumption does NOT hold and the binding must be stated rather
    than inherited."""

    SYNTHETIC_MERGE_PARENTAGE = "synthetic_merge_parentage"
    EXPLICIT_TESTED_TREE = "explicit_tested_tree"


ExecutedTreeRuleValueV2 = Annotated[ExecutedTreeRuleV2, Field(strict=False)]


class OriginRulesV2(ContractV2Model):
    """Which `RunOriginV2` event types this entry may be promoted under.

    An explicit, closed object rather than a free map: an open map would let a
    target introduce an origin key the engine has never heard of, and it cannot
    forbid unknown properties in the published schema. `None` means "this
    origin is not authorised here", which is the fail-closed default -- an
    origin is opted IN, never assumed."""

    pull_request: ExecutedTreeRuleValueV2 | None = None
    pull_request_target: ExecutedTreeRuleValueV2 | None = None
    manual: ExecutedTreeRuleValueV2 | None = None
    replay: ExecutedTreeRuleValueV2 | None = None

    def rule_for(self, event_type: str) -> ExecutedTreeRuleV2 | None:
        return getattr(self, event_type, None)

    @model_validator(mode="after")
    def validate_origin_rules(self) -> OriginRulesV2:
        declared = {
            "pull_request": self.pull_request,
            "pull_request_target": self.pull_request_target,
            "manual": self.manual,
            "replay": self.replay,
        }
        if all(rule is None for rule in declared.values()):
            raise ValueError("an entry must authorise at least one origin")

        # `pull_request` is the only origin with GitHub's synthetic-merge
        # semantics. Letting a target declare anything else for it, or claim
        # those semantics for an origin that does not have them, would reopen
        # the tested-tree hole through the policy file.
        for event_type, rule in declared.items():
            if rule is None:
                continue
            if event_type == "pull_request":
                if rule is not ExecutedTreeRuleV2.SYNTHETIC_MERGE_PARENTAGE:
                    raise ValueError("pull_request must use synthetic_merge_parentage")
            elif rule is ExecutedTreeRuleV2.SYNTHETIC_MERGE_PARENTAGE:
                raise ValueError("only pull_request has synthetic-merge semantics")

        # `explicit_tested_tree` is declarable but not yet verifiable: no
        # producer emits authenticated evidence of the tree it checked out, and
        # a second Codex review showed that accepting it meant trusting the
        # caller's own `--tested-merge-sha` echoed back. Refused at LOAD time
        # rather than at verdict time, so a target learns its policy cannot work
        # when it writes it, not when a review silently never becomes ready.
        if any(
            rule is ExecutedTreeRuleV2.EXPLICIT_TESTED_TREE
            for rule in declared.values()
            if rule is not None
        ):
            raise ValueError("explicit_tested_tree has no verifiable producer evidence yet")
        return self


class AuthoritativeCheckEntryV2(ContractV2Model):
    """One required check and the single producer entitled to speak for it."""

    check_name: SafeText
    workflow_path: RelativePath
    job_name: SafeText
    verifier_identity: SafeIdentifier
    # WHICH producer, immutably. A Codex review showed that using the run's
    # `workflow_ref` as proof of base ownership was unsatisfiable -- genuine
    # `pull_request` runs execute under a pull ref -- and that the only runs
    # recording a default-branch ref were `pull_request_target` runs, which
    # execute the base rather than the merge. Identity now comes from a
    # reusable workflow pinned by full SHA, which a pull request cannot swap.
    producer_kind: ProducerKindV2
    producer_workflow: ProducerWorkflowIdentityV2
    # The base-owned ref the producer workflow must execute under. Meaningful
    # only for `base_owned_workflow_run`, where GitHub loads the definition from
    # the default branch -- so unlike a `pull_request` run, the ref is not
    # PR-controlled and comparing it adds real assurance.
    producer_workflow_ref: SafeText
    # Structurally pinned rather than configurable -- see the module docstring.
    permitted_conclusions: tuple[Literal["success"], Literal["failure"]]
    origin_rules: OriginRulesV2

    @model_validator(mode="after")
    def validate_entry(self) -> AuthoritativeCheckEntryV2:
        # The producer must live in a repository, not in the pull request's own
        # tree by coincidence of path.
        if self.producer_workflow.repository == "":
            raise ValueError("a pinned producer workflow needs a repository")

        # Refused at LOAD time, the same way `explicit_tested_tree` is: a target
        # learns its policy cannot work when it writes the policy, not when a
        # review silently never becomes ready.
        #
        # The pin proves which reusable workflow a run LOADED. It does not bind
        # the uploaded attestation to that workflow's job, and inside a
        # PR-triggered run the pull request can upload one itself carrying every
        # field the verifier checks. Promotable again only once the
        # attestation's issuer is cryptographically authenticated.
        if self.producer_kind == "sha_pinned_reusable_workflow":
            raise ValueError(
                "sha_pinned_reusable_workflow has no unsigned evidence path: "
                "the attestation artifact is not bound to the pinned workflow's job"
            )

        # For a base-owned producer the run IS the producer, so the entry's
        # workflow path and the pinned producer path describe one workflow.
        # Allowing them to differ would leave the assembler matching the
        # observation against one path and the identity against another.
        if self.producer_kind == "base_owned_workflow_run":
            if self.workflow_path != self.producer_workflow.path:
                raise ValueError(
                    "a base_owned_workflow_run producer runs its own workflow: "
                    "workflow_path and producer_workflow.path must be identical"
                )
        return self


class AuthoritativeCheckPolicyIdentityV2(ContractV2Model):
    repo: Repository


class AuthoritativeCheckPolicyV2(ContractV2Model):
    schema_id: Literal["agent-review.authoritative-check-policy.v2"]
    schema_version: Literal[2]
    source: Literal["repo-policy"]
    identity: AuthoritativeCheckPolicyIdentityV2
    authoritative_checks: Annotated[tuple[AuthoritativeCheckEntryV2, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_unique_check_names(self) -> AuthoritativeCheckPolicyV2:
        names = [entry.check_name for entry in self.authoritative_checks]
        if len(names) != len(set(names)):
            # Two entries for one check means two producers could satisfy it,
            # and the assembler would have to guess. Refused at load time.
            raise ValueError("each check_name may appear at most once")
        return self

    def entry_for(self, check_name: str) -> AuthoritativeCheckEntryV2 | None:
        for entry in self.authoritative_checks:
            if entry.check_name == check_name:
                return entry
        return None


@dataclass(frozen=True)
class LoadedAuthoritativeCheckPolicyV2:
    """The policy together with the identity of the bytes it came from.

    Both digests travel into the provenance sidecar, so a verdict can always be
    traced back to the exact policy text that authorised it."""

    policy: AuthoritativeCheckPolicyV2
    policy_source_bytes_digest: str
    policy_source_semantic_digest: str


def compute_policy_semantic_digest_v2(policy: AuthoritativeCheckPolicyV2) -> str:
    """A change in MEANING moves this digest; a change in FILE LAYOUT does not.

    `authoritative_checks` is semantically a SET keyed by `check_name` --
    `validate_unique_check_names` already enforces that the key is unique,
    and `entry_for()` matches by name regardless of position. Reordering two
    entries changes no validation outcome and no producer binding, so it must
    not move this digest either; the entries are sorted by `check_name`
    before hashing so position in the source file is not semantic."""

    dumped = policy.model_dump(mode="json")
    dumped["authoritative_checks"] = sorted(
        dumped["authoritative_checks"], key=lambda entry: entry["check_name"]
    )
    return canonical_json_digest_hex(dumped)


def load_authoritative_check_policy_v2(
    base_checkout_root: Path | str,
    *,
    relative_path: Path = DEFAULT_AUTHORITATIVE_CHECK_POLICY_RELATIVE_PATH,
) -> LoadedAuthoritativeCheckPolicyV2:
    """Load the policy from a TRUSTED BASE/DEFAULT checkout.

    The parameter is named `base_checkout_root`, not `repo_root`, because the
    distinction is the entire security property: pointed at a PR working tree,
    this function would let the pull request authorise its own execution. This
    module cannot verify which checkout it was handed -- the caller must only
    ever pass base/default in a privileged workflow."""

    policy_path = Path(base_checkout_root) / relative_path
    if not policy_path.is_file():
        raise AuthoritativeCheckPolicyErrorV2(POLICY_MISSING_REASON_V2)

    try:
        raw_bytes = policy_path.read_bytes()
    except OSError as exc:
        raise AuthoritativeCheckPolicyErrorV2(POLICY_UNREADABLE_REASON_V2) from exc

    try:
        raw = yaml.load(raw_bytes.decode("utf-8"), Loader=_DuplicateKeyRejectingLoaderV2)
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError) as exc:
        # RecursionError: deeply nested target-authored YAML. Listed so the
        # boundary is total for the input classes this document can carry,
        # not widened one exception at a time as they are reported.
        raise AuthoritativeCheckPolicyErrorV2(POLICY_UNREADABLE_REASON_V2) from exc

    if not isinstance(raw, dict):
        raise AuthoritativeCheckPolicyErrorV2(POLICY_UNREADABLE_REASON_V2)

    try:
        # Round-trip through JSON text rather than `model_validate` on the raw
        # dict, matching `profile_loader_v2`: under strict mode a Python list
        # is not a tuple, but a JSON array is an equally valid source for a
        # tuple-typed field. Same strictness, without rejecting well-formed
        # YAML for a Python-level type distinction the file cannot express.
        policy = AuthoritativeCheckPolicyV2.model_validate_json(
            json.dumps(raw, ensure_ascii=False), strict=True
        )
    except (ValueError, TypeError) as exc:
        raise AuthoritativeCheckPolicyErrorV2(POLICY_INVALID_REASON_V2) from exc

    return LoadedAuthoritativeCheckPolicyV2(
        policy=policy,
        policy_source_bytes_digest=raw_bytes_digest_hex(raw_bytes),
        policy_source_semantic_digest=compute_policy_semantic_digest_v2(policy),
    )


def validate_policy_against_profile_v2(
    *, policy: AuthoritativeCheckPolicyV2, profile: TargetProfileV2
) -> None:
    """Cross-validate the policy against the target profile it must serve.

    Three independent failures, each fail-closed:

    - the two files must describe the same repository;
    - every `required_checks` name must have exactly one policy entry, and
      every entry must name a required check. A required check with no declared
      authoritative source is a LOAD FAILURE, not a check that silently
      degrades to "no source available" at verdict time;
    Base ownership is NOT checked here: it is established per-observation by
    the SHA-pinned producer workflow and its attestation, not by a ref the
    policy could merely assert.
    """

    if policy.identity.repo != profile.identity.repo:
        raise AuthoritativeCheckPolicyErrorV2(POLICY_PROFILE_MISMATCH_REASON_V2)

    required = set(profile.policies.required_checks)
    declared = {entry.check_name for entry in policy.authoritative_checks}

    if required - declared:
        raise AuthoritativeCheckPolicyErrorV2(POLICY_REQUIRED_CHECK_UNCOVERED_REASON_V2)
    if declared - required:
        raise AuthoritativeCheckPolicyErrorV2(POLICY_ENTRY_NOT_REQUIRED_REASON_V2)

    # NOTE: there is deliberately no `workflow_ref == refs/heads/<default>`
    # check any more. It could never be satisfied by a real `pull_request` run,
    # and the only runs that did satisfy it executed the base rather than the
    # merge. Base ownership is now established by the SHA-pinned producer
    # workflow and its attestation -- see `authoritative_producer_evidence_v2`.
