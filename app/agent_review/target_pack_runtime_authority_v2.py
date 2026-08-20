"""`#203` -- INTERNAL runtime authorities for the AgentReview v2 Target Pack.

This module is the single place where two *runtime* relations are declared,
and it is the module the runtime itself consumes. It is deliberately NOT a
target-facing contract: nothing here is installed into a target, serialized
into a receipt, or covered by `manifest_digest`.

## Why this module exists

`#203-D0` tried three times to let documentation recover these two relations
from the implementation *after the fact*. The first two attempts failed on
forge identity; the third failed on runtime identity, because what static
analysis can actually establish is

```
RecognizedByStaticGrammar(cmd, Source)   NOT   ExposedByCli(cmd)
RecognizedLiteralAssignments(Source)     NOT   ValidateCheckDomain
```

and those are different propositions. The decisive counterexample came from
this codebase, not a synthetic one: `target_owned_integrity` and
`generated_file_integrity` reach the runtime through a dict lookup
(`_CHECK_NAME_BY_LEDGER_V2`), so no reader of the source -- human or parser --
can determine the emitted set from occurrences alone.

The fix is to stop inferring and invert the dependency:

```
                  Authority (this module)
                    /              \\
                   v                v
           runtime behaviour     generated view  -> documentation

Runtime       = Consumer(Authority)
Documentation = Projection(Authority)
never           Documentation = Infer(RuntimeSource)
```

## Two subjects, one location

There is deliberately no single `TargetPackImplementationAuthorityV2` object.
CLI exposure and the validate check domain are different relations about
different subjects and must not acquire a shared identity merely because they
share a file:

```
A_CLI      != A_VALIDATE        even though     location(A_CLI) == location(A_VALIDATE)
```

## Domain, never "inventory"

`validate` returns early on the first blocking condition, so a real run emits
a duplicate-free *subsequence* of the locally evaluable checks, plus every
structurally unvalidated one. What is declared here is therefore the DOMAIN of
possible check identities:

```
D = L (+) U          L = locally evaluable, U = structurally unvalidated
O(x) subset of L     observed local checks for input x
R(x) = Canonicalize(O(x) union U)
U subset of R(x)     and    R(x) subset of D
```

"Inventory" would name the contents of one execution and is not what this
declares.

## Invariants raise, never assert

`python -O` strips `assert`, so every load-bearing invariant here raises
`TargetPackRuntimeAuthorityError`. The checks below run at import time: a
malformed authority must fail before any consumer can read a partial domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TargetPackRuntimeAuthorityError(Exception):
    """A declared authority is internally malformed -- a duplicate identity, a
    missing structural reason, a reason attached to a locally evaluable check.
    Raised at import time so no consumer can observe a partial domain."""


# --- A_CLI: which commands the target-pack CLI exposes --------------------


@dataclass(frozen=True)
class TargetPackCliCommandSpecV2:
    """One command identity plus the help text argparse shows for it. Argument
    wiring is NOT declared here: the CLI keeps ordinary per-command Python
    configurators. What this owns is the command DOMAIN, so the parser can be
    built by iterating it instead of being discovered from scattered
    `add_parser()` calls."""

    name: str
    help: str


INIT_COMMAND_V2 = "init"
DOCTOR_COMMAND_V2 = "doctor"
VALIDATE_COMMAND_V2 = "validate"

TARGET_PACK_CLI_COMMANDS_V2: tuple[TargetPackCliCommandSpecV2, ...] = (
    TargetPackCliCommandSpecV2(
        INIT_COMMAND_V2, "seed a target repository (TARGET_OWNED files only, once)"
    ),
    TargetPackCliCommandSpecV2(
        DOCTOR_COMMAND_V2, "read-only diagnostics; never mutates the target"
    ),
    TargetPackCliCommandSpecV2(
        VALIDATE_COMMAND_V2, "target-only, read-only validation; requires no toolrepo checkout"
    ),
)


def cli_command_names_v2() -> tuple[str, ...]:
    """`C` -- the command domain, in declaration order."""

    return tuple(spec.name for spec in TARGET_PACK_CLI_COMMANDS_V2)


# --- A_VALIDATE: the domain of validate check identities ------------------


class ValidateEvaluationClassV2(str, Enum):
    """How a check identity relates to what `validate` can observe.

    `LOCALLY_EVALUABLE` -- may be emitted with a real status when the run
    reaches it; a run that returns early simply omits it.

    `UNVALIDATED` -- structurally unestablishable from the target alone, so it
    is always emitted as `unavailable` with a reason naming the OWNER of the
    question. This is an authority boundary, not unfinished work, and it says
    nothing about future target-pack versions.
    """

    LOCALLY_EVALUABLE = "locally_evaluable"
    UNVALIDATED = "unvalidated"


@dataclass(frozen=True)
class TargetPackValidateCheckSpecV2:
    """One check identity. `unvalidated_reason_code` is part of the identity --
    it names why the relation is structurally unestablishable -- and is
    required exactly when the class is `UNVALIDATED`.

    Ordinary evaluation outcome reasons (`..._drift`, `..._missing`,
    `..._mismatch`) are NOT declared here: those are results of evaluating a
    check, not the identity of one, and stay with the runtime that produces
    them."""

    name: str
    evaluation_class: ValidateEvaluationClassV2
    unvalidated_reason_code: str | None = None


# Check-name identities. The runtime re-exports these so existing imports keep
# working, but this module owns them.
TARGET_ROOT_CHECK_V2 = "target_root"
AIOPS_SNAPSHOT_CHECK_V2 = "aiops_snapshot"
RECEIPT_CHECK_V2 = "receipt"
PROFILE_CHECK_V2 = "profile"
PROFILE_HASH_CHECK_V2 = "profile_hash"
PROFILE_IDENTITY_CHECK_V2 = "profile_identity"
ROOT_IDENTITY_CHECK_V2 = "root_identity"
OBSERVATION_BUDGET_CHECK_V2 = "observation_budget"
TARGET_OWNED_INTEGRITY_CHECK_V2 = "target_owned_integrity"
GENERATED_FILE_INTEGRITY_CHECK_V2 = "generated_file_integrity"
# `#203-C2` Codex Round 1, P2-A: a relation `target_owned_integrity`/
# `generated_file_integrity` do NOT own -- both verify only "the byte claims
# the receipt actually makes", never ownership-class separation. `#203-C1`
# proves textual/declared-key disjointness between the two ledgers; it says
# nothing about RUNTIME RESOLVED-FILESYSTEM identity -- two distinct,
# C1-legal `RelativePath` keys, one per ledger, can still resolve to the SAME
# physical file (an in-root symlink). That is a genuinely different,
# only-locally-observable relation, so it gets its own dedicated identity
# rather than silently redefining either integrity check's documented scope.
CROSS_LEDGER_ALIAS_CHECK_V2 = "cross_ledger_alias_separation"
TARGET_OWNED_SET_CHECK_V2 = "target_owned_set"
GENERATED_FILE_SET_CHECK_V2 = "generated_file_set"
ROLLOUT_CAPABILITY_CHECK_V2 = "rollout_capability"
PREVIOUS_INSTALL_LINEAGE_CHECK_V2 = "previous_install_lineage"
TRUSTED_CHECK_INVENTORY_CHECK_V2 = "trusted_check_inventory"
# ONE identity for the whole installed-state-to-upstream-pack RELATION,
# deliberately not three (`pack_version`, `toolrepo_sha`, `manifest_digest`
# are the receipt's three CLAIMS about that single relation, and no consumer
# can act on them separately when all three are equally unestablished). It
# exists because `valid` answers only "of the relations I can independently
# observe locally, is any violated?" -- a stale or fabricated receipt whose
# local relations are all self-coherent is locally valid AND says nothing true
# about which upstream pack it came from. Without this identity a consumer
# could read `valid: true` and infer provenance was checked.
UPSTREAM_PACK_IDENTITY_CHECK_V2 = "upstream_pack_identity"

# Structural reasons for the UNVALIDATED identities. Each names the OWNER of
# the question, so the code cannot be misread as "the receipt is malformed",
# "the receipt is missing" or "the local bytes drifted" -- all separate,
# locally decidable failures with their own outcome reasons in the runtime.
TARGET_OWNED_SET_REASON_V2 = "target_pack_validate_target_owned_set_requires_the_upstream_manifest"
GENERATED_FILE_SET_REASON_V2 = "target_pack_validate_generated_file_set_requires_the_upstream_manifest"
ROLLOUT_CAPABILITY_REASON_V2 = "target_pack_validate_rollout_capability_requires_the_upstream_manifest"
PREVIOUS_INSTALL_LINEAGE_REASON_V2 = (
    "target_pack_validate_previous_install_lineage_not_verifiable_from_current_target_state"
)
TRUSTED_CHECK_INVENTORY_REASON_V2 = "target_pack_validate_trusted_check_inventory_requires_an_unshipped_contract"
UPSTREAM_PACK_IDENTITY_REASON_V2 = (
    "target_pack_validate_upstream_pack_identity_requires_the_upstream_manifest_and_toolrepo"
)


def _local(name: str) -> TargetPackValidateCheckSpecV2:
    return TargetPackValidateCheckSpecV2(name, ValidateEvaluationClassV2.LOCALLY_EVALUABLE)


def _unvalidated(name: str, reason: str) -> TargetPackValidateCheckSpecV2:
    return TargetPackValidateCheckSpecV2(name, ValidateEvaluationClassV2.UNVALIDATED, reason)


# `D`, in canonical report order. Declaration order IS the ordering authority:
# the runtime's finalizer canonicalizes every report against this sequence, so
# there is no second place where check ordering is decided.
TARGET_PACK_VALIDATE_CHECKS_V2: tuple[TargetPackValidateCheckSpecV2, ...] = (
    _local(TARGET_ROOT_CHECK_V2),
    _local(AIOPS_SNAPSHOT_CHECK_V2),
    _local(RECEIPT_CHECK_V2),
    _local(PROFILE_CHECK_V2),
    _local(PROFILE_HASH_CHECK_V2),
    _local(PROFILE_IDENTITY_CHECK_V2),
    _local(ROOT_IDENTITY_CHECK_V2),
    _local(OBSERVATION_BUDGET_CHECK_V2),
    _local(TARGET_OWNED_INTEGRITY_CHECK_V2),
    _local(GENERATED_FILE_INTEGRITY_CHECK_V2),
    _local(CROSS_LEDGER_ALIAS_CHECK_V2),
    _unvalidated(UPSTREAM_PACK_IDENTITY_CHECK_V2, UPSTREAM_PACK_IDENTITY_REASON_V2),
    _unvalidated(TARGET_OWNED_SET_CHECK_V2, TARGET_OWNED_SET_REASON_V2),
    _unvalidated(GENERATED_FILE_SET_CHECK_V2, GENERATED_FILE_SET_REASON_V2),
    _unvalidated(ROLLOUT_CAPABILITY_CHECK_V2, ROLLOUT_CAPABILITY_REASON_V2),
    _unvalidated(PREVIOUS_INSTALL_LINEAGE_CHECK_V2, PREVIOUS_INSTALL_LINEAGE_REASON_V2),
    _unvalidated(TRUSTED_CHECK_INVENTORY_CHECK_V2, TRUSTED_CHECK_INVENTORY_REASON_V2),
)


# --- Projections of D. Derived, never independently maintained. -----------


def validate_check_domain_names_v2() -> tuple[str, ...]:
    """`D` as names, in canonical order."""

    return tuple(spec.name for spec in TARGET_PACK_VALIDATE_CHECKS_V2)


def validate_locally_evaluable_names_v2() -> tuple[str, ...]:
    """`L`, in canonical order."""

    return tuple(
        spec.name
        for spec in TARGET_PACK_VALIDATE_CHECKS_V2
        if spec.evaluation_class is ValidateEvaluationClassV2.LOCALLY_EVALUABLE
    )


def validate_unvalidated_specs_v2() -> tuple[TargetPackValidateCheckSpecV2, ...]:
    """`U`, in canonical order."""

    return tuple(
        spec
        for spec in TARGET_PACK_VALIDATE_CHECKS_V2
        if spec.evaluation_class is ValidateEvaluationClassV2.UNVALIDATED
    )


def validate_check_index_v2() -> dict[str, int]:
    """Canonical position per identity -- the ordering the finalizer applies."""

    return {spec.name: position for position, spec in enumerate(TARGET_PACK_VALIDATE_CHECKS_V2)}


def validate_spec_by_name_v2() -> dict[str, TargetPackValidateCheckSpecV2]:
    return {spec.name: spec for spec in TARGET_PACK_VALIDATE_CHECKS_V2}


# --- Import-time invariants ----------------------------------------------


def _verify_cli_authority_v2() -> None:
    names = [spec.name for spec in TARGET_PACK_CLI_COMMANDS_V2]
    if not names:
        raise TargetPackRuntimeAuthorityError("TARGET_PACK_CLI_COMMANDS_V2 is empty")
    if len(names) != len(set(names)):
        raise TargetPackRuntimeAuthorityError(
            f"duplicate CLI command identity in TARGET_PACK_CLI_COMMANDS_V2: {sorted(names)}"
        )
    for spec in TARGET_PACK_CLI_COMMANDS_V2:
        if not spec.name or not spec.name.strip():
            raise TargetPackRuntimeAuthorityError("a CLI command identity is empty")
        if not spec.help or not spec.help.strip():
            raise TargetPackRuntimeAuthorityError(f"CLI command {spec.name!r} declares no help text")


def _verify_validate_authority_v2() -> None:
    specs = TARGET_PACK_VALIDATE_CHECKS_V2
    if not specs:
        raise TargetPackRuntimeAuthorityError("TARGET_PACK_VALIDATE_CHECKS_V2 is empty")
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise TargetPackRuntimeAuthorityError(
            f"duplicate check identity in TARGET_PACK_VALIDATE_CHECKS_V2: {sorted(names)}"
        )
    for spec in specs:
        if not spec.name or not spec.name.strip():
            raise TargetPackRuntimeAuthorityError("a validate check identity is empty")
        if not isinstance(spec.evaluation_class, ValidateEvaluationClassV2):
            raise TargetPackRuntimeAuthorityError(
                f"check {spec.name!r} has a non-ValidateEvaluationClassV2 evaluation_class"
            )
        if spec.evaluation_class is ValidateEvaluationClassV2.UNVALIDATED:
            if not spec.unvalidated_reason_code:
                raise TargetPackRuntimeAuthorityError(
                    f"UNVALIDATED check {spec.name!r} declares no unvalidated_reason_code"
                )
        elif spec.unvalidated_reason_code is not None:
            raise TargetPackRuntimeAuthorityError(
                f"LOCALLY_EVALUABLE check {spec.name!r} declares an unvalidated_reason_code"
            )
    # D = L (+) U: the two classes partition the domain, so no identity can be
    # both, and none can be neither.
    local = set(validate_locally_evaluable_names_v2())
    unvalidated = {spec.name for spec in validate_unvalidated_specs_v2()}
    if local & unvalidated:
        raise TargetPackRuntimeAuthorityError(f"check identities in both classes: {sorted(local & unvalidated)}")
    if local | unvalidated != set(names):
        raise TargetPackRuntimeAuthorityError("L (+) U does not cover the declared check domain")


_verify_cli_authority_v2()
_verify_validate_authority_v2()
