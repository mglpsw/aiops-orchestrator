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
marker at `HEAD`, the empty `refs/` and `objects/` directories -- or derives
locally from an admitted payload, which is why a pack index is never taken from
the source. An index that arrives as input is an index an attacker can forge; an
index derived from an authenticated payload is not.

All three generated entries are load-bearing together. Measured on 2.39.5:
`is_git_directory()` is a three-way AND, so a store carrying only `HEAD` and
`refs/` is *not* a repository, and a tool run inside it falls back to searching
upward and binds to whatever encloses it -- which is the round-9 escape above,
reproduced from an incomplete skeleton rather than from a copied carrier.

Naming those three locations is not a denylist. Recognition of *source payload*
consults no filename at all; what is named here is what the authority
**generates**, and generating them is precisely what makes a source-supplied
copy unreachable.

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

Every restriction the mechanism applies is declared in the contract rather than
hardcoded here, because a restriction the contract does not carry is a domain
the consumer narrowed by itself. The object-format set is the case that made
this concrete: while the accepted object-id length lived in a module constant, a
stock `--object-format=sha256` store had every loose payload silently dropped
while the plan still claimed totality -- ADR 0015's forbidden move, performed by
omission.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

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
    "verify_plan_digest_v2",
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
    #: The observer supplied too little to classify. Not the same as "refused":
    #: a truncated prefix silently becoming `observed_not_consumed` would let a
    #: sloppy observer produce an empty plan that still claimed totality.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PlanCompletenessV2(enum.Enum):
    """Whether the plan may claim totality for the domain it declares."""

    COMPLETE_FOR_SUPPORTED_DOMAIN = "complete_for_supported_domain"
    UNKNOWN_REQUIRED_BLOCKED = "unknown_required_blocked"
    #: Nothing was admitted while the claims require payload. A plan over an
    #: empty admitted set is vacuous, not complete, and saying "complete" there
    #: is how a silent over-refusal looks exactly like success.
    NO_ADMITTED_PAYLOAD = "no_admitted_payload"


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
_GENERATED_FILES = frozenset({"HEAD"})
_GENERATED_DIRECTORY_PREFIXES = ("refs/",)

#: Enough bytes to classify either payload class (pack magic + version).
_MINIMUM_CLASSIFYING_PREFIX = 8

#: A canonical loose object lives at the location its own object id implies.
#: This is the class definition, not a path heuristic: the location IS the
#: identity claim that a later slice must re-derive from the payload. The
#: accepted id length comes from the contract's declared object formats, never
#: from a constant here -- see the module docstring on why that distinction is
#: the difference between a restriction and a retrospective narrowing.
_LOOSE_OBJECT_SHAPE = re.compile(r"objects/[0-9a-f]{2}/([0-9a-f]+)\Z")

#: Measured on git 2.39.5, which supports both.
_OBJECT_FORMAT_ID_HEX = {"sha1": 40, "sha256": 64}

#: A pack payload is authority-placed, and the only location real stores use.
_PACK_LOCATION = re.compile(r"objects/pack/[^/]+\Z")

#: A location must be a relative, forward-slash, GIT_DIR-rooted path. Anything
#: else is a placement instruction the observer had no authority to give, and
#: admitting it would let a payload legitimate in one place become operational
#: somewhere else -- the shape of the original defeat.
_FORBIDDEN_IN_LOCATION = ("\\", "\x00", "\n", "\r")

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
    """`Q_B` and `R_B`: what may be claimed, and what each claim requires.

    Sealed at construction. `frozen=True` stops attribute rebinding but not
    mutation of a mapping held by reference, and that gap was enough to perform
    ADR 0015's forbidden move on an unchanged contract object: meet an
    unadmittable requirement, delete it from `required_classes`, build again,
    receive a complete plan. So the mapping is normalised into an immutable one
    and every claim must declare a non-empty requirement -- an unmapped claim
    used to contribute the empty set, which let a plan assert totality over a
    claim whose requirements had never been stated at all.
    """

    profile_id: str
    claims: frozenset[str]
    required_classes: Mapping[str, frozenset[str]]
    #: Which object-id formats the claims are declared over. In the contract
    #: rather than in a module constant, so a store this profile cannot
    #: represent blocks instead of being quietly dropped.
    object_formats: frozenset[str] = frozenset({"sha1"})

    def __post_init__(self) -> None:
        if not self.claims:
            raise ValueError("a claim profile with no claims cannot support a plan")
        missing = sorted(set(self.claims) - set(self.required_classes))
        if missing:
            raise ValueError(f"claims with no declared representation classes: {missing}")
        sealed: dict[str, frozenset[str]] = {}
        for claim in sorted(self.claims):
            required = self.required_classes[claim]
            if isinstance(required, str) or not required:
                raise ValueError(f"claim {claim!r} must declare a non-empty set of class names")
            if not all(isinstance(name, str) for name in required):
                raise ValueError(f"claim {claim!r} declares a non-string class name")
            sealed[claim] = frozenset(required)
        unknown_formats = sorted(set(self.object_formats) - set(_OBJECT_FORMAT_ID_HEX))
        if unknown_formats or not self.object_formats:
            raise ValueError(f"unsupported object formats: {unknown_formats or 'none declared'}")
        object.__setattr__(self, "required_classes", MappingProxyType(sealed))

    def required_class_names(self) -> frozenset[str]:
        names: set[str] = set()
        for claim in self.claims:
            names |= set(self.required_classes[claim])
        return frozenset(names)

    def accepted_id_hex_lengths(self) -> frozenset[int]:
        return frozenset(_OBJECT_FORMAT_ID_HEX[name] for name in self.object_formats)


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
    snapshot_id: str
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


def _location_is_well_formed(location: str) -> bool:
    """A GIT_DIR-rooted relative path, and nothing that could redirect placement."""
    if not location or location.startswith("/"):
        return False
    if any(bad in location for bad in _FORBIDDEN_IN_LOCATION):
        return False
    return all(segment not in ("", ".", "..") for segment in location.split("/"))


def recognise_representation_class_v2(
    observation: StorageObservationV2,
    *,
    accepted_id_hex_lengths: frozenset[int] | None = None,
) -> GitRepresentationClassV2:
    """Classify one observation by its structure.

    Two rules, both positive. Neither consults a list of things to avoid. A pack
    is recognised by its magic and never by its extension -- but it must still
    sit where a pack sits, because `AdmittedPayloadV2.location` is the only
    location the plan carries, and an unconstrained one is a placement
    instruction supplied by whatever wrote the store.
    """
    lengths = (
        accepted_id_hex_lengths
        if accepted_id_hex_lengths is not None
        else frozenset({_OBJECT_FORMAT_ID_HEX["sha1"]})
    )
    if observation.entry_kind is not GitEntryKindV2.REGULAR_FILE:
        return GitRepresentationClassV2.UNRECOGNISED
    if not _location_is_well_formed(observation.location):
        return GitRepresentationClassV2.UNRECOGNISED

    prefix = observation.content_prefix
    if _PACK_LOCATION.fullmatch(observation.location):
        if prefix[:4] == _PACK_MAGIC and len(prefix) >= 8:
            version = int.from_bytes(prefix[4:8], "big")
            if version in _PACK_SUPPORTED_VERSIONS:
                return GitRepresentationClassV2.PACK_PAYLOAD

    shape = _LOOSE_OBJECT_SHAPE.fullmatch(observation.location)
    if (
        shape
        and (len(shape.group(1)) + 2) in lengths
        and len(prefix) >= 2
        and prefix[0] == _ZLIB_FIRST_BYTE
        and prefix[1] in _ZLIB_SECOND_BYTES
    ):
        return GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD

    return GitRepresentationClassV2.UNRECOGNISED


def _is_generated_location(location: str) -> bool:
    return location in _GENERATED_FILES or any(
        location == prefix.rstrip("/") or location.startswith(prefix)
        for prefix in _GENERATED_DIRECTORY_PREFIXES
    )


def _looks_like_object_payload(observation: StorageObservationV2) -> bool:
    """Object-shaped, but in a format this contract did not declare.

    Distinguished from an unrecognised carrier on purpose: this is payload the
    store genuinely holds and the contract genuinely cannot represent, so it
    must block rather than be dropped into the inert pile.
    """
    if observation.entry_kind is not GitEntryKindV2.REGULAR_FILE:
        return False
    shape = _LOOSE_OBJECT_SHAPE.fullmatch(observation.location)
    prefix = observation.content_prefix
    return bool(
        shape
        and len(prefix) >= 2
        and prefix[0] == _ZLIB_FIRST_BYTE
        and prefix[1] in _ZLIB_SECOND_BYTES
    )


def decide_admission_v2(
    observation: StorageObservationV2, contract: AdmissionContractV2
) -> AdmissionDispositionV2:
    """Assign exactly one disposition to one observation.

    Ordered so that every way of *not* being admissible is settled before
    admission can be returned. The contract-level block is deliberately NOT
    first: a blocked contract used to stamp `unknown_required` over every
    observation, destroying the per-observation structural verdicts exactly when
    an operator most needs them. The structural verdict is computed first and
    the block is applied by the plan.
    """
    if observation.entry_kind in (GitEntryKindV2.SYMLINK, GitEntryKindV2.OTHER):
        return AdmissionDispositionV2.FORBIDDEN
    if not _location_is_well_formed(observation.location):
        return AdmissionDispositionV2.FORBIDDEN
    if _is_generated_location(observation.location):
        return AdmissionDispositionV2.GENERATED_ONLY

    profile = contract.claim_profile
    if contract.unknown_required_classes():
        return AdmissionDispositionV2.UNKNOWN_REQUIRED

    recognised = recognise_representation_class_v2(
        observation, accepted_id_hex_lengths=profile.accepted_id_hex_lengths()
    )
    if recognised is not GitRepresentationClassV2.UNRECOGNISED:
        if recognised.value not in profile.required_class_names():
            return AdmissionDispositionV2.OBSERVED_NOT_CONSUMED
        if observation.content_digest is None:
            # A payload with no content binding cannot carry a verification
            # obligation, so admitting it would be an obligation nobody can discharge.
            return AdmissionDispositionV2.INSUFFICIENT_EVIDENCE
        return AdmissionDispositionV2.ADMITTED_SOURCE

    if _looks_like_object_payload(observation):
        return AdmissionDispositionV2.UNKNOWN_REQUIRED

    # Only where a payload class could actually live. Elsewhere the prefix
    # length is irrelevant, and flagging it would refuse ordinary metadata for
    # the crime of being short.
    prefix_length = len(observation.content_prefix)
    at_pack_location = bool(_PACK_LOCATION.fullmatch(observation.location))
    shape = _LOOSE_OBJECT_SHAPE.fullmatch(observation.location)
    at_object_location = bool(
        shape and (len(shape.group(1)) + 2) in profile.accepted_id_hex_lengths()
    )
    if observation.size_bytes > 0 and (
        (at_pack_location and prefix_length < _MINIMUM_CLASSIFYING_PREFIX)
        or (at_object_location and prefix_length < 2)
    ):
        return AdmissionDispositionV2.INSUFFICIENT_EVIDENCE

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
    """The authority's own structure, owing nothing to any source store.

    All three entries are load-bearing together, which is why `objects/` is here
    rather than being assumed. Measured on git 2.39.5: `is_git_directory()` is a
    three-way AND over HEAD, objects and refs. A store carrying only HEAD and
    refs/ is not a repository at all, so a tool run inside it searches upward
    and binds to whatever encloses it -- reproducing the round-9 escape from an
    incomplete skeleton instead of from a copied carrier.
    """
    return (
        GeneratedEntryV2(
            location="HEAD",
            entry_kind=GitEntryKindV2.REGULAR_FILE,
            rationale=(
                "generated so the store's validity never depends on source bytes; "
                "a source-supplied marker is what made an earlier confinement "
                "fall back to searching outside itself"
            ),
        ),
        GeneratedEntryV2(
            location="objects/",
            entry_kind=GitEntryKindV2.DIRECTORY,
            rationale=(
                "measured: required by the three-way directory test, and its "
                "absence causes upward discovery to bind an unrelated repository"
            ),
        ),
        GeneratedEntryV2(
            location="refs/",
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
    call cannot join the contract that is judging it.

    The domain is resolved ONCE, at the top, rather than re-read per
    observation. It used to be consulted six times in a single build, so a
    stateful mapping -- fully conformant with the declared type -- could admit
    payload under one `R_B` and then have a different one sealed into the plan
    digest, leaving the plan attesting a profile that had not made the decision.
    """
    seen = sorted(
        observations,
        key=lambda item: (item.location, item.content_digest or "", item.entry_kind.value),
    )
    generated = _generated_skeleton()
    unknown_required = contract.unknown_required_classes()
    required_names = contract.claim_profile.required_class_names()

    snapshots = {item.snapshot_id for item in seen}
    if len(snapshots) > 1:
        raise ValueError(
            f"observations span {len(snapshots)} snapshots; a plan is a "
            f"single-snapshot decision and mixing them is not resolvable here"
        )
    snapshot_id = next(iter(snapshots), "")

    locations = [item.location for item in seen]
    duplicated = sorted({name for name in locations if locations.count(name) > 1})
    if duplicated:
        raise ValueError(
            f"two observations claim the same location {duplicated}; the plan "
            f"cannot state both, and choosing one would be resolving ambiguity "
            f"by preference"
        )

    if unknown_required:
        # Fail closed. A claim requiring a class this contract cannot admit may
        # not be narrowed to whatever happened to be admissible, so nothing is
        # admitted and nothing is derived. The per-observation STRUCTURAL verdict
        # is still recorded: an operator diagnosing a blocked plan needs to know
        # which entries were forbidden and which were merely unfamiliar.
        not_consumed = tuple(
            NonConsumedObservationV2(
                location=item.location,
                entry_kind=item.entry_kind,
                size_bytes=item.size_bytes,
                content_digest=item.content_digest,
                disposition=decide_admission_v2(item, contract),
            )
            for item in seen
        )
        return _sealed(
            contract,
            snapshot_id=snapshot_id,
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
    blocking: list[str] = []

    for item in seen:
        disposition = decide_admission_v2(item, contract)
        if disposition is not AdmissionDispositionV2.ADMITTED_SOURCE:
            if disposition in (
                AdmissionDispositionV2.UNKNOWN_REQUIRED,
                AdmissionDispositionV2.INSUFFICIENT_EVIDENCE,
            ):
                blocking.append(item.location)
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

        recognised = recognise_representation_class_v2(
            item, accepted_id_hex_lengths=contract.claim_profile.accepted_id_hex_lengths()
        )
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

    if blocking:
        # Payload this store genuinely holds and this contract cannot represent.
        # Dropping it into the inert pile is the retrospective narrowing ADR 0015
        # forbids: it would look exactly like a complete plan over a smaller store.
        return _sealed(
            contract,
            snapshot_id=snapshot_id,
            generated=generated,
            admitted=(),
            derivations=(),
            not_consumed=tuple(not_consumed) + tuple(
                NonConsumedObservationV2(
                    location=entry.location,
                    entry_kind=GitEntryKindV2.REGULAR_FILE,
                    size_bytes=0,
                    content_digest=entry.content_digest,
                    disposition=AdmissionDispositionV2.UNKNOWN_REQUIRED,
                )
                for entry in admitted
            ),
            completeness=PlanCompletenessV2.UNKNOWN_REQUIRED_BLOCKED,
            unknown_required=tuple(sorted(blocking)),
        )

    completeness = (
        PlanCompletenessV2.COMPLETE_FOR_SUPPORTED_DOMAIN
        if admitted or not required_names
        else PlanCompletenessV2.NO_ADMITTED_PAYLOAD
    )
    return _sealed(
        contract,
        snapshot_id=snapshot_id,
        generated=generated,
        admitted=tuple(admitted),
        derivations=tuple(derivations),
        not_consumed=tuple(not_consumed),
        completeness=completeness,
        unknown_required=(),
    )


def _plan_preimage(
    *,
    contract_id: str,
    profile: SupportedGitClaimProfileV2,
    snapshot_id: str,
    toolchain: GitToolchainIdentityV2,
    generated: tuple[GeneratedEntryV2, ...],
    admitted: tuple[AdmittedPayloadV2, ...],
    derivations: tuple[DerivationV2, ...],
    not_consumed: tuple[NonConsumedObservationV2, ...],
    completeness: PlanCompletenessV2,
    unknown_required: tuple[str, ...],
) -> dict:
    """Everything the plan asserts, and nothing it does not.

    Following ADR 0008: a binding an author can change without changing the
    digest proves nothing. That is not a style point here -- `size_bytes`,
    `verification_obligation`, `rationale` and `snapshot_id` were all outside
    the preimage, so a plan could be edited to say "no verification required",
    or two plans built from entirely different stores could share an identity.
    """
    return {
        "contract_id": contract_id,
        "snapshot_id": snapshot_id,
        "claim_profile": {
            "profile_id": profile.profile_id,
            "claims": sorted(profile.claims),
            "object_formats": sorted(profile.object_formats),
            "required_classes": {
                claim: sorted(profile.required_classes[claim])
                for claim in sorted(profile.claims)
            },
        },
        "toolchain": dataclasses.asdict(toolchain),
        "generated": [
            {
                "location": entry.location,
                "entry_kind": entry.entry_kind.value,
                "rationale": entry.rationale,
            }
            for entry in generated
        ],
        "admitted": [
            {
                "location": entry.location,
                "representation_class": entry.representation_class.value,
                "content_digest": entry.content_digest,
                "verification_obligation": entry.verification_obligation,
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


def verify_plan_digest_v2(plan: CanonicalGitAuthorityPlanV2, contract: AdmissionContractV2) -> bool:
    """Recompute a plan's identity from its own fields.

    Exported deliberately. A digest nobody downstream can recompute is a claim
    on trust, not evidence, and S1 consists entirely of holders of plan objects.
    """
    preimage = _plan_preimage(
        contract_id=plan.contract_id,
        profile=contract.claim_profile,
        snapshot_id=plan.snapshot_id,
        toolchain=plan.toolchain,
        generated=plan.generated,
        admitted=plan.admitted,
        derivations=plan.derivations,
        not_consumed=plan.not_consumed,
        completeness=plan.completeness,
        unknown_required=plan.unknown_required,
    )
    return plan.plan_digest == _digest(preimage)


def _digest(preimage: dict) -> str:
    return hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sealed(
    contract: AdmissionContractV2,
    *,
    snapshot_id: str,
    generated: tuple[GeneratedEntryV2, ...],
    admitted: tuple[AdmittedPayloadV2, ...],
    derivations: tuple[DerivationV2, ...],
    not_consumed: tuple[NonConsumedObservationV2, ...],
    completeness: PlanCompletenessV2,
    unknown_required: tuple[str, ...],
) -> CanonicalGitAuthorityPlanV2:
    """Attach the plan's identity over its full preimage."""
    preimage = _plan_preimage(
        contract_id=contract.contract_id,
        profile=contract.claim_profile,
        snapshot_id=snapshot_id,
        toolchain=contract.toolchain,
        generated=generated,
        admitted=admitted,
        derivations=derivations,
        not_consumed=not_consumed,
        completeness=completeness,
        unknown_required=unknown_required,
    )
    return CanonicalGitAuthorityPlanV2(
        contract_id=contract.contract_id,
        claim_profile_id=contract.claim_profile.profile_id,
        snapshot_id=snapshot_id,
        toolchain=contract.toolchain,
        generated=generated,
        admitted=admitted,
        derivations=derivations,
        not_consumed=not_consumed,
        completeness=completeness,
        unknown_required=unknown_required,
        plan_digest=_digest(preimage),
    )
