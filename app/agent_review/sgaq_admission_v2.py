"""Turning neutral storage observations into a closed canonical-authority plan.

`#331`, SGAQ-S0. Additive and internal: this slice decides *what a private Git
authority would be allowed to contain*. It materialises nothing, rewires no
existing consumer, and opens no file.

## Observation and admission are different acts

Two earlier attempts at #331 failed the same way. Each enumerated the carriers
known to be dangerous, copied the rest into a private object store, and was then
defeated by a carrier nobody had enumerated -- four times, each time by a file
that was legitimate somewhere else. The last one carried no pathname at all: it
was a validity marker whose absence made the private store stop looking like a
repository, so the tool fell back to searching upward and bound to a repository
the confinement had nothing to do with.

The lesson is not "enumerate better". It is that copying bytes into a private
namespace does not confine authority, because the namespace is interpreted by
software whose interpretation those bytes participate in. So this module never
asks "is this carrier dangerous?". It asks "is this one of the representation
classes the supported claims actually require?", and everything else is observed
and left inert.

That inversion is why no historical carrier name appears below. A mechanism
written against the carriers it has already met cannot refuse the next one, and
`test_production_never_enumerates_historical_carrier_names` reads this file to
keep it that way.

## The contract is fixed before the observations

CAEM ADR 0015 (design reference; the AIOps consumer pin declares
`authority_effect: none`) permits restricting the domain a decision claims, but
only through a contract that is identified *before* the observations, declares
the restricted claim, and fails closed outside it -- and it explicitly forbids
meeting an unrecognised representation and then shrinking the domain so the
consumer's coverage looks complete.

`AdmissionContractV2` is that contract, and it is frozen. It carries the claim
scope `Q_B` (`SupportedGitClaimProfileV2.claims`), the representation classes
those claims require (`R_B`), and the toolchain the empirical basis was measured
against. Nothing in this module can widen it, which is checked by the contract
being unchanged across a plan build rather than by reading the code.

## The two directions of "unknown", which are not the same

This is the distinction the whole slice exists to hold:

* **unknown, not required** -- a carrier no supported claim needs. It is
  recorded and never consumed, and the plan is still complete for the domain it
  claims. An authority that refused every unfamiliar byte would be fail-closed
  and useless, which is how earlier heuristics died: they rejected legitimate
  stores.

* **unknown, required** -- a claim genuinely needs a representation class this
  contract cannot admit. There is no positive plan. The claim fails closed
  rather than being quietly narrowed to whatever happened to be admissible.

Getting one right while getting the other wrong is the characteristic failure,
so both are asserted in the corpus and neither is derivable from the other.

## What may be admitted, positively

Only two classes of source material, recognised by structure and never by
filename:

* a canonical loose object payload, at the location its own object id implies;
* a pack payload, identified by its leading magic.

Everything the authority needs beyond that it generates itself -- the validity
marker at `HEAD`, the empty `refs/` directory -- or derives locally from an
admitted payload, which is why a pack index is never taken from the source. An
index that arrives as input is an index an attacker can forge; an index derived
from an authenticated payload is not.

## What this slice does not do

It does not open, read, hash or copy anything: every field of
`StorageObservationV2` is supplied by a caller that did the looking. It does not
verify the payload identities it declares obligations for -- the plan says an
admitted loose object must re-derive to the object id its location claims, and a
later slice must actually do it. And a plan is not an authority: it is a
statement of what an authority would be permitted to contain, which is the same
separation `authoritative_ci_snapshot_v2` draws between an observation and a
credential.

The empirical basis is Git 2.39.5 only. `GitToolchainIdentityV2` is inside the
plan preimage so that drift stales the plan rather than silently reinterpreting
it, and a reported version string alone is not accepted as an identity.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "AdmissionContractV2",
    "AdmissionDispositionV2",
    "AdmittedPayloadV2",
    "CanonicalGitAuthorityPlanV2",
    "DerivationV2",
    "GeneratedEntryV2",
    "GitEntryKindV2",
    "GitRepresentationClassV2",
    "GitToolchainIdentityV2",
    "NonConsumedObservationV2",
    "PlanCompletenessV2",
    "StorageObservationV2",
    "SupportedGitClaimProfileV2",
    "build_canonical_authority_plan_v2",
    "decide_admission_v2",
    "recognise_representation_class_v2",
]


# --------------------------------------------------------------------------
# closed vocabularies
# --------------------------------------------------------------------------


class GitEntryKindV2(enum.Enum):
    """What the observer found, before anything is interpreted."""

    REGULAR_FILE = "regular_file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class GitRepresentationClassV2(enum.Enum):
    """The source payload classes this contract can admit, and nothing else.

    `UNRECOGNISED` is a full member rather than an error: not recognising a
    carrier is a normal, expected outcome, and it is the outcome that keeps the
    grammar positive.
    """

    LOOSE_OBJECT_PAYLOAD = "loose_object_payload"
    PACK_PAYLOAD = "pack_payload"
    UNRECOGNISED = "unrecognised"


class AdmissionDispositionV2(enum.Enum):
    """What may become of one observation. Closed, and not configurable."""

    #: A recognised payload class that a supported claim requires.
    ADMITTED_SOURCE = "admitted_source"
    #: The authority produces this itself, so the observed one is never an input.
    GENERATED_ONLY = "generated_only"
    #: Real, recorded, inert. The `unknown, not required` case.
    OBSERVED_NOT_CONSUMED = "observed_not_consumed"
    #: A supported claim needs a class this contract cannot admit. Fail closed.
    UNKNOWN_REQUIRED = "unknown_required"
    #: Not a payload class at all, by structure rather than by name.
    FORBIDDEN = "forbidden"


class PlanCompletenessV2(enum.Enum):
    """Whether the plan may claim totality for the domain it declares."""

    COMPLETE_FOR_SUPPORTED_DOMAIN = "complete_for_supported_domain"
    UNKNOWN_REQUIRED_BLOCKED = "unknown_required_blocked"


#: The class names this contract knows how to admit from source material. The
#: plan compares the claim profile's requirements against exactly this set, so a
#: requirement outside it is `unknown, required` rather than silently dropped.
_ADMISSIBLE_CLASS_NAMES = frozenset(
    {
        GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD.value,
        GitRepresentationClassV2.PACK_PAYLOAD.value,
    }
)

#: Locations the authority produces for itself. An observation landing here is
#: evidence, never an input -- which is what makes the historical validity-marker
#: carrier unreachable rather than merely unused.
_GENERATED_LOCATION = "HEAD"
_GENERATED_DIRECTORY_PREFIX = "refs/"

#: A canonical loose object lives at the location its own object id implies.
#: This is the class definition, not a path heuristic: the location IS the
#: identity claim that a later slice must re-derive from the payload.
_LOOSE_OBJECT_LOCATION = re.compile(r"objects/[0-9a-f]{2}/[0-9a-f]{38}\Z")

_PACK_MAGIC = b"PACK"
_PACK_SUPPORTED_VERSIONS = frozenset({2, 3})
#: zlib streams as Git writes loose objects. The second byte encodes the
#: compression level, so the set is enumerated rather than guessed at.
_ZLIB_FIRST_BYTE = 0x78
_ZLIB_SECOND_BYTES = frozenset({0x01, 0x5E, 0x9C, 0xDA})


# --------------------------------------------------------------------------
# the contract, fixed before observation
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GitToolchainIdentityV2:
    """Which Git actually produced the empirical basis for the supported claims.

    `reported_version` alone is not an identity -- two binaries can report the
    same string -- so the digest participates in the plan preimage as well.
    """

    executable_digest: str
    reported_version: str
    capability_profile_id: str


@dataclasses.dataclass(frozen=True)
class SupportedGitClaimProfileV2:
    """`Q_B` and `R_B`: what may be claimed, and what each claim requires."""

    profile_id: str
    claims: frozenset[str]
    required_classes: Mapping[str, frozenset[str]]

    def required_class_names(self) -> frozenset[str]:
        names: set[str] = set()
        for claim in self.claims:
            names |= set(self.required_classes.get(claim, frozenset()))
        return frozenset(names)


@dataclasses.dataclass(frozen=True)
class AdmissionContractV2:
    """The authoritative boundary contract, identified before any observation."""

    contract_id: str
    claim_profile: SupportedGitClaimProfileV2
    toolchain: GitToolchainIdentityV2

    def unknown_required_classes(self) -> tuple[str, ...]:
        """Required classes this contract has no way to admit."""
        return tuple(
            sorted(self.claim_profile.required_class_names() - _ADMISSIBLE_CLASS_NAMES)
        )


# --------------------------------------------------------------------------
# observations and plan entries
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StorageObservationV2:
    """One neutral thing seen in a source store. Carries no interpretation.

    `content_prefix` is a bounded leading slice, enough to recognise a payload
    class by structure. The full bytes are deliberately not here: this slice
    decides admissibility, and holding a carrier's contents is how a refused
    carrier becomes an input again.
    """

    snapshot_id: str
    location: str
    entry_kind: GitEntryKindV2
    size_bytes: int
    content_digest: str | None
    content_prefix: bytes = b""


@dataclasses.dataclass(frozen=True)
class GeneratedEntryV2:
    """Something the authority produces itself, owing nothing to the source."""

    location: str
    entry_kind: GitEntryKindV2
    rationale: str


@dataclasses.dataclass(frozen=True)
class AdmittedPayloadV2:
    """Source bytes the plan permits, and the proof still owed on them."""

    location: str
    representation_class: GitRepresentationClassV2
    content_digest: str | None
    verification_obligation: str


@dataclasses.dataclass(frozen=True)
class DerivationV2:
    """Something the authority computes locally from an admitted payload.

    An index that arrives as input can be forged; one derived from an
    authenticated payload cannot be, which is why this exists as a plan step
    rather than as another admissible class.
    """

    operation: str
    source_location: str
    produces: str


@dataclasses.dataclass(frozen=True)
class NonConsumedObservationV2:
    """Bounded evidence that something was seen and not used.

    The location is retained as evidence only. Nothing downstream may treat it
    as a path to open: that is exactly how a quarantined carrier becomes
    operational again.
    """

    location: str
    entry_kind: GitEntryKindV2
    size_bytes: int
    content_digest: str | None
    disposition: AdmissionDispositionV2


@dataclasses.dataclass(frozen=True)
class CanonicalGitAuthorityPlanV2:
    """What an authority would be permitted to contain. Not itself an authority."""

    contract_id: str
    claim_profile_id: str
    toolchain: GitToolchainIdentityV2
    generated: tuple[GeneratedEntryV2, ...]
    admitted: tuple[AdmittedPayloadV2, ...]
    derivations: tuple[DerivationV2, ...]
    not_consumed: tuple[NonConsumedObservationV2, ...]
    completeness: PlanCompletenessV2
    unknown_required: tuple[str, ...]
    plan_digest: str


# --------------------------------------------------------------------------
# recognition: structural, never by filename
# --------------------------------------------------------------------------


def recognise_representation_class_v2(
    observation: StorageObservationV2,
) -> GitRepresentationClassV2:
    """Classify one observation by its structure.

    Two rules, both positive. Neither consults a list of things to avoid, and
    neither trusts a filename: a pack is recognised by its magic whatever it is
    called, and a name that looks canonical over bytes that are not a pack is
    not a pack.
    """
    if observation.entry_kind is not GitEntryKindV2.REGULAR_FILE:
        return GitRepresentationClassV2.UNRECOGNISED

    prefix = observation.content_prefix
    if prefix[:4] == _PACK_MAGIC and len(prefix) >= 8:
        version = int.from_bytes(prefix[4:8], "big")
        if version in _PACK_SUPPORTED_VERSIONS:
            return GitRepresentationClassV2.PACK_PAYLOAD

    if (
        _LOOSE_OBJECT_LOCATION.fullmatch(observation.location)
        and len(prefix) >= 2
        and prefix[0] == _ZLIB_FIRST_BYTE
        and prefix[1] in _ZLIB_SECOND_BYTES
    ):
        return GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD

    return GitRepresentationClassV2.UNRECOGNISED


def _is_generated_location(location: str) -> bool:
    return location == _GENERATED_LOCATION or location.startswith(_GENERATED_DIRECTORY_PREFIX)


def decide_admission_v2(
    observation: StorageObservationV2, contract: AdmissionContractV2
) -> AdmissionDispositionV2:
    """Assign exactly one disposition to one observation.

    Ordered so that every way of *not* being admissible is settled before
    admission can be returned, and so that a contract which cannot serve its own
    claims fails closed before it decides anything about source material.
    """
    if contract.unknown_required_classes():
        return AdmissionDispositionV2.UNKNOWN_REQUIRED

    if observation.entry_kind in (GitEntryKindV2.SYMLINK, GitEntryKindV2.OTHER):
        return AdmissionDispositionV2.FORBIDDEN

    if _is_generated_location(observation.location):
        return AdmissionDispositionV2.GENERATED_ONLY

    recognised = recognise_representation_class_v2(observation)
    if (
        recognised is not GitRepresentationClassV2.UNRECOGNISED
        and recognised.value in contract.claim_profile.required_class_names()
    ):
        return AdmissionDispositionV2.ADMITTED_SOURCE

    return AdmissionDispositionV2.OBSERVED_NOT_CONSUMED


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


_VERIFICATION_OBLIGATIONS = {
    GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD: (
        "re-derive the object id from the payload and require it to equal the "
        "id its location claims, before the payload is used"
    ),
    GitRepresentationClassV2.PACK_PAYLOAD: (
        "verify the payload is internally consistent and self-contained before "
        "any index is derived from it"
    ),
}


def _generated_skeleton() -> tuple[GeneratedEntryV2, ...]:
    """The authority's own structure, owing nothing to any source store."""
    return (
        GeneratedEntryV2(
            location=_GENERATED_LOCATION,
            entry_kind=GitEntryKindV2.REGULAR_FILE,
            rationale=(
                "generated so the store's validity never depends on source bytes; "
                "a source-supplied marker is what made an earlier confinement "
                "fall back to searching outside itself"
            ),
        ),
        GeneratedEntryV2(
            location=_GENERATED_DIRECTORY_PREFIX,
            entry_kind=GitEntryKindV2.DIRECTORY,
            rationale=(
                "measured: the directory must exist and its contents are "
                "irrelevant for exact-object-id claims"
            ),
        ),
    )


def build_canonical_authority_plan_v2(
    observations: Iterable[StorageObservationV2], contract: AdmissionContractV2
) -> CanonicalGitAuthorityPlanV2:
    """Convert observations into a closed plan under a pre-existing contract.

    The contract is an input and is never modified: a carrier met during this
    call cannot join the contract that is judging it. That is asserted by the
    caller comparing the contract before and after, because a promise made in a
    docstring is not a mechanism.
    """
    seen = sorted(observations, key=lambda item: item.location)
    generated = _generated_skeleton()
    unknown_required = contract.unknown_required_classes()

    if unknown_required:
        # Fail closed. There is no positive plan: a claim requiring a class this
        # contract cannot admit may not be narrowed to whatever happened to be
        # admissible, so nothing is admitted and nothing is derived.
        not_consumed = tuple(
            NonConsumedObservationV2(
                location=item.location,
                entry_kind=item.entry_kind,
                size_bytes=item.size_bytes,
                content_digest=item.content_digest,
                disposition=AdmissionDispositionV2.UNKNOWN_REQUIRED,
            )
            for item in seen
        )
        return _sealed(
            contract,
            generated=generated,
            admitted=(),
            derivations=(),
            not_consumed=not_consumed,
            completeness=PlanCompletenessV2.UNKNOWN_REQUIRED_BLOCKED,
            unknown_required=unknown_required,
        )

    admitted: list[AdmittedPayloadV2] = []
    derivations: list[DerivationV2] = []
    not_consumed: list[NonConsumedObservationV2] = []

    for item in seen:
        disposition = decide_admission_v2(item, contract)
        if disposition is not AdmissionDispositionV2.ADMITTED_SOURCE:
            not_consumed.append(
                NonConsumedObservationV2(
                    location=item.location,
                    entry_kind=item.entry_kind,
                    size_bytes=item.size_bytes,
                    content_digest=item.content_digest,
                    disposition=disposition,
                )
            )
            continue

        recognised = recognise_representation_class_v2(item)
        admitted.append(
            AdmittedPayloadV2(
                location=item.location,
                representation_class=recognised,
                content_digest=item.content_digest,
                verification_obligation=_VERIFICATION_OBLIGATIONS[recognised],
            )
        )
        if recognised is GitRepresentationClassV2.PACK_PAYLOAD:
            derivations.append(
                DerivationV2(
                    operation="derive_pack_index",
                    source_location=item.location,
                    produces="authority-owned index for the admitted payload",
                )
            )

    return _sealed(
        contract,
        generated=generated,
        admitted=tuple(admitted),
        derivations=tuple(derivations),
        not_consumed=tuple(not_consumed),
        completeness=PlanCompletenessV2.COMPLETE_FOR_SUPPORTED_DOMAIN,
        unknown_required=(),
    )


def _sealed(
    contract: AdmissionContractV2,
    *,
    generated: tuple[GeneratedEntryV2, ...],
    admitted: tuple[AdmittedPayloadV2, ...],
    derivations: tuple[DerivationV2, ...],
    not_consumed: tuple[NonConsumedObservationV2, ...],
    completeness: PlanCompletenessV2,
    unknown_required: tuple[str, ...],
) -> CanonicalGitAuthorityPlanV2:
    """Attach the plan's identity over its full preimage.

    Following ADR 0008: a binding an author can change without changing the
    digest proves nothing, so the claim profile and the toolchain identity are
    inside the preimage rather than merely alongside the plan.
    """
    profile = contract.claim_profile
    preimage = {
        "contract_id": contract.contract_id,
        "claim_profile": {
            "profile_id": profile.profile_id,
            "claims": sorted(profile.claims),
            "required_classes": {
                claim: sorted(profile.required_classes.get(claim, frozenset()))
                for claim in sorted(profile.claims)
            },
        },
        "toolchain": dataclasses.asdict(contract.toolchain),
        "generated": [
            {"location": entry.location, "entry_kind": entry.entry_kind.value}
            for entry in generated
        ],
        "admitted": [
            {
                "location": entry.location,
                "representation_class": entry.representation_class.value,
                "content_digest": entry.content_digest,
            }
            for entry in admitted
        ],
        "derivations": [
            {
                "operation": entry.operation,
                "source_location": entry.source_location,
                "produces": entry.produces,
            }
            for entry in derivations
        ],
        "not_consumed": [
            {
                "location": entry.location,
                "entry_kind": entry.entry_kind.value,
                "size_bytes": entry.size_bytes,
                "content_digest": entry.content_digest,
                "disposition": entry.disposition.value,
            }
            for entry in not_consumed
        ],
        "completeness": completeness.value,
        "unknown_required": list(unknown_required),
    }
    digest = hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CanonicalGitAuthorityPlanV2(
        contract_id=contract.contract_id,
        claim_profile_id=profile.profile_id,
        toolchain=contract.toolchain,
        generated=generated,
        admitted=admitted,
        derivations=derivations,
        not_consumed=not_consumed,
        completeness=completeness,
        unknown_required=unknown_required,
        plan_digest=digest,
    )
