"""The admission contract, attacked before it exists.

Written before `sgaq_admission_v2` did, because a corpus written after an
implementation tends to describe it rather than test it. Every case here is a
proposition about the contract, not about the code that will satisfy it.

WHAT IS BEING FIXED, AND WHEN

ADR 0015 permits restricting the domain a decision claims -- but only through a
contract that is identified *before* the observations, declares the restricted
claim, and fails closed outside it. "Encountering a representation the consumer
does not recognize and then shrinking the domain retrospectively is forbidden."

So the interesting cases are not the legitimate carriers. They are:

* a carrier nobody had thought of, which must be observable without becoming
  operational (`unknown_not_required`);
* a claim that genuinely needs a class the contract cannot admit, which must
  block rather than quietly narrow (`unknown_required`).

Those two are the same mechanism read in opposite directions, and getting one
right while getting the other wrong is the failure this file exists to catch.

WHY THE HISTORICAL NAMES APPEAR HERE AND NOWHERE ELSE

`multi-pack-index` and `info/grafts` were the carriers that defeated two earlier
attempts at #331. They appear in this file as fixtures. If they appear in the
production module -- even in a check that excludes them -- the mechanism has
been written against a list of past defeats rather than a grammar, and the next
carrier will not be on the list. `test_production_never_enumerates_historical_carrier_names`
asserts that by reading the module's own source.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app.agent_review.sgaq_admission_v2 import (
    AdmissionContractV2,
    AdmissionDispositionV2,
    GitEntryKindV2,
    GitRepresentationClassV2,
    GitToolchainIdentityV2,
    PlanCompletenessV2,
    StorageObservationV2,
    SupportedGitClaimProfileV2,
    build_canonical_authority_plan_v2,
    decide_admission_v2,
    recognise_representation_class_v2,
)

# --------------------------------------------------------------------------
# fixtures: neutral observations, never a filesystem
# --------------------------------------------------------------------------

_ZLIB = b"\x78\x9c"
_PACK = b"PACK\x00\x00\x00\x02"
_SNAPSHOT = "snapshot-0001"

#: A loose object id, and the canonical location that id implies.
_OID = "a" * 40
_LOOSE_LOCATION = f"objects/{_OID[:2]}/{_OID[2:]}"


def _observe(
    location: str,
    *,
    kind: GitEntryKindV2 = GitEntryKindV2.REGULAR_FILE,
    prefix: bytes = b"",
    size: int = 128,
) -> StorageObservationV2:
    return StorageObservationV2(
        snapshot_id=_SNAPSHOT,
        location=location,
        entry_kind=kind,
        size_bytes=size,
        content_digest=hashlib.sha256(location.encode() + prefix).hexdigest(),
        content_prefix=prefix,
    )


def _loose() -> StorageObservationV2:
    return _observe(_LOOSE_LOCATION, prefix=_ZLIB)


def _pack() -> StorageObservationV2:
    return _observe("objects/pack/pack-" + "b" * 40 + ".pack", prefix=_PACK)


def _toolchain() -> GitToolchainIdentityV2:
    return GitToolchainIdentityV2(
        executable_digest="sha256:" + "c" * 64,
        reported_version="2.39.5",
        capability_profile_id="measured-2.39.5-exact-oid",
    )


#: The claim scope `Q_B` the #331 measurement actually supports, and the
#: representation classes `R_B(q)` each claim requires. Both are declared here,
#: before any observation is interpreted.
_SUPPORTED_CLAIMS = (
    "resolve_exact_oid",
    "read_object_content",
    "list_tree",
    "prove_ancestry",
)


def _profile(
    *, extra_required: frozenset[str] = frozenset(), profile_id: str = "measured-exact-oid-v1"
) -> SupportedGitClaimProfileV2:
    required = frozenset({"loose_object_payload", "pack_payload"}) | extra_required
    return SupportedGitClaimProfileV2(
        profile_id=profile_id,
        claims=frozenset(_SUPPORTED_CLAIMS),
        required_classes={claim: required for claim in _SUPPORTED_CLAIMS},
    )


def _contract(**kwargs) -> AdmissionContractV2:
    return AdmissionContractV2(
        contract_id="sgaq-s0-admission-v1",
        claim_profile=_profile(**kwargs),
        toolchain=_toolchain(),
    )


# --------------------------------------------------------------------------
# G, H, I -- the legitimate carriers must survive
# --------------------------------------------------------------------------


def test_a_canonical_loose_object_is_admitted() -> None:
    """G. The positive control. A contract that admits nothing is trivially safe
    and completely useless, so the legitimate cases are asserted first."""
    assert recognise_representation_class_v2(_loose()) is (
        GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD
    )
    assert decide_admission_v2(_loose(), _contract()) is AdmissionDispositionV2.ADMITTED_SOURCE


def test_a_pack_payload_is_admitted_and_recognised_by_content_not_by_name() -> None:
    """H. Recognition reads the magic bytes; the filename is not consulted.

    Asserted by giving the same bytes a name that says nothing, which must be
    admitted identically -- and by giving the canonical name to bytes that are
    not a pack, which must not.
    """
    assert decide_admission_v2(_pack(), _contract()) is AdmissionDispositionV2.ADMITTED_SOURCE

    renamed = _observe("objects/pack/no-extension-at-all", prefix=_PACK)
    assert decide_admission_v2(renamed, _contract()) is AdmissionDispositionV2.ADMITTED_SOURCE

    impostor = _observe("objects/pack/pack-" + "d" * 40 + ".pack", prefix=b"NOTAPACK")
    assert recognise_representation_class_v2(impostor) is GitRepresentationClassV2.UNRECOGNISED


def test_a_source_pack_index_is_not_admitted_and_the_plan_derives_one_instead() -> None:
    """I. The measurement's central structural result.

    An `.idx` present in the source is not admitted merely because it exists.
    The plan must instead declare that an index is DERIVED from the admitted
    pack, which is what stops a forged index from being authority-bearing.
    """
    source_idx = _observe("objects/pack/pack-" + "b" * 40 + ".idx", prefix=b"\xfftOc\x00\x00\x00\x02")
    assert decide_admission_v2(source_idx, _contract()) is (
        AdmissionDispositionV2.OBSERVED_NOT_CONSUMED
    )

    plan = build_canonical_authority_plan_v2([_pack(), source_idx], _contract())
    assert source_idx.location not in {entry.location for entry in plan.admitted}
    derived_from = {derivation.source_location for derivation in plan.derivations}
    assert _pack().location in derived_from, "the plan must derive an index from the admitted pack"


def test_zlib_bytes_outside_the_canonical_object_location_are_not_a_loose_object() -> None:
    """The location IS the identity claim, so it is part of the class.

    A loose object's path states the object id its payload must re-derive to.
    Compressed bytes sitting somewhere else make no such claim and cannot be
    admitted as that class, however well-formed they look.
    """
    misplaced = _observe("objects/info/an-ordinary-looking-file", prefix=_ZLIB)
    assert recognise_representation_class_v2(misplaced) is GitRepresentationClassV2.UNRECOGNISED
    assert decide_admission_v2(misplaced, _contract()) is (
        AdmissionDispositionV2.OBSERVED_NOT_CONSUMED
    )

    wrong_shape = _observe(f"objects/{_OID[:3]}/{_OID[3:]}", prefix=_ZLIB)
    assert recognise_representation_class_v2(wrong_shape) is (
        GitRepresentationClassV2.UNRECOGNISED
    )


def test_a_recognised_class_no_supported_claim_requires_is_not_admitted() -> None:
    """Recognition is necessary for admission and not sufficient for it.

    `R_B` decides what gets in, not the recogniser. Under a profile whose claims
    need only pack payloads, a perfectly valid loose object is observed and left
    inert -- otherwise the admitted set would be whatever the code can parse
    rather than what the contract requires.
    """
    pack_only = AdmissionContractV2(
        contract_id="sgaq-s0-admission-v1",
        claim_profile=SupportedGitClaimProfileV2(
            profile_id="pack-only-v1",
            claims=frozenset({"verify_pack"}),
            required_classes={"verify_pack": frozenset({"pack_payload"})},
        ),
        toolchain=_toolchain(),
    )
    assert recognise_representation_class_v2(_loose()) is (
        GitRepresentationClassV2.LOOSE_OBJECT_PAYLOAD
    )
    assert decide_admission_v2(_loose(), pack_only) is (
        AdmissionDispositionV2.OBSERVED_NOT_CONSUMED
    )
    assert decide_admission_v2(_pack(), pack_only) is AdmissionDispositionV2.ADMITTED_SOURCE


# --------------------------------------------------------------------------
# A, B, C -- the unknown carrier, read in both directions
# --------------------------------------------------------------------------


def _future_carrier() -> StorageObservationV2:
    """A carrier whose name appears nowhere in the production module.

    Deliberately given Git-shaped magic, a path-like payload and a plausible
    location, so that anything short of a positive grammar would let it in.
    """
    return _observe(
        "objects/pack/this-format-did-not-exist-when-the-code-was-written.v99",
        prefix=b"MIDXsomething\x00../victim\x00refs/heads/main\x00" + b"e" * 40,
        size=4096,
    )


def test_an_unknown_future_carrier_is_observed_but_never_admitted() -> None:
    """A. Observation and admission are different acts.

    The carrier is real, it is recorded, and it acquires no operational meaning.
    """
    carrier = _future_carrier()
    assert recognise_representation_class_v2(carrier) is GitRepresentationClassV2.UNRECOGNISED
    assert decide_admission_v2(carrier, _contract()) is (
        AdmissionDispositionV2.OBSERVED_NOT_CONSUMED
    )

    plan = build_canonical_authority_plan_v2([_loose(), _pack(), carrier], _contract())
    assert carrier.location not in {entry.location for entry in plan.admitted}
    assert carrier.location in {entry.location for entry in plan.not_consumed}


def test_an_unknown_carrier_that_no_claim_requires_does_not_block_the_plan() -> None:
    """B. `unknown_not_required` != `unknown_required`.

    An instrument that refuses everything it has not seen before is fail-closed
    and useless. The supported domain is still fully served.
    """
    plan = build_canonical_authority_plan_v2(
        [_loose(), _pack(), _future_carrier()], _contract()
    )
    assert plan.completeness is PlanCompletenessV2.COMPLETE_FOR_SUPPORTED_DOMAIN
    assert plan.admitted, "the legitimate carriers must still be admitted"


def test_a_claim_requiring_an_unadmittable_class_blocks_the_plan() -> None:
    """C. The other direction, and the one a narrowing consumer gets wrong.

    Here the claim profile genuinely requires a representation class the
    contract cannot admit. ADR 0015 forbids resolving that by shrinking the
    claim: it must fail closed, and no positive complete plan may be produced.
    """
    contract = _contract(extra_required=frozenset({"a_class_this_contract_cannot_admit"}))
    plan = build_canonical_authority_plan_v2([_loose(), _pack()], contract)
    assert plan.completeness is PlanCompletenessV2.UNKNOWN_REQUIRED_BLOCKED
    assert plan.unknown_required == ("a_class_this_contract_cannot_admit",)


def test_an_unadmittable_required_class_is_reported_per_observation_too() -> None:
    """C, at the disposition level rather than the plan level."""
    contract = _contract(extra_required=frozenset({"a_class_this_contract_cannot_admit"}))
    assert decide_admission_v2(_loose(), contract) is AdmissionDispositionV2.UNKNOWN_REQUIRED


# --------------------------------------------------------------------------
# D, E, F -- the historical carriers, refused without being named
# --------------------------------------------------------------------------


def test_the_historical_multi_pack_index_witness_is_refused_without_a_named_rule() -> None:
    """D. The round-7 carrier of #331.

    It must be refused because it is not a recognised class, not because a
    production branch checks for its name.
    """
    midx = _observe("objects/pack/multi-pack-index", prefix=b"MIDX\x00\x00\x00\x01")
    assert decide_admission_v2(midx, _contract()) is AdmissionDispositionV2.OBSERVED_NOT_CONSUMED


def test_the_historical_graft_witness_never_enters_the_plan() -> None:
    """E. The carrier that changes ancestry ANSWERS rather than speed.

    The measurement showed grafts adding a parent can make a divergent
    non-ancestor reachable, and that `--no-replace-objects` does not cover it.
    Under a positive grammar it is simply not a class that can be admitted.
    """
    graft = _observe("info/grafts", prefix=b"f" * 40 + b" " + b"e" * 40 + b"\n")
    plan = build_canonical_authority_plan_v2([_loose(), graft], _contract())
    assert graft.location not in {entry.location for entry in plan.admitted}
    assert plan.completeness is PlanCompletenessV2.COMPLETE_FOR_SUPPORTED_DOMAIN


def test_a_hostile_config_never_enters_the_plan() -> None:
    """F. The measurement found no config file is required at all."""
    config = _observe("config", prefix=b"[core]\n\thooksPath = /tmp/evil\n")
    plan = build_canonical_authority_plan_v2([_loose(), config], _contract())
    assert config.location not in {entry.location for entry in plan.admitted}


def test_a_source_head_is_never_consumed_because_the_authority_generates_one() -> None:
    """The round-9 carrier of #331, which carried no pathname at all.

    A one-byte `HEAD` made the private store stop satisfying git's
    `is_git_directory()`, so git fell back to upward discovery and bound to an
    unrelated repository. Copying `HEAD` is what made that reachable. Here the
    authority generates its own, so a source `HEAD` is an observation and never
    an input.
    """
    head = _observe("HEAD", prefix=b"ref: refs/heads/attacker\n")
    assert decide_admission_v2(head, _contract()) is AdmissionDispositionV2.GENERATED_ONLY
    plan = build_canonical_authority_plan_v2([_loose(), head], _contract())
    assert head.location not in {entry.location for entry in plan.admitted}
    assert "HEAD" in {entry.location for entry in plan.generated}


def test_a_symlink_is_refused_structurally_whatever_it_points_at() -> None:
    """Not a name rule: a symlink is not a payload class the contract can admit."""
    link = _observe(_LOOSE_LOCATION, kind=GitEntryKindV2.SYMLINK, prefix=_ZLIB)
    assert decide_admission_v2(link, _contract()) is AdmissionDispositionV2.FORBIDDEN


# --------------------------------------------------------------------------
# J, K -- plan identity
# --------------------------------------------------------------------------


def test_the_same_observations_and_contract_produce_the_same_plan() -> None:
    """J. Deterministic replay, including under reordered observations."""
    observations = [_loose(), _pack(), _future_carrier()]
    first = build_canonical_authority_plan_v2(observations, _contract())
    second = build_canonical_authority_plan_v2(list(reversed(observations)), _contract())
    assert first.plan_digest == second.plan_digest


def test_a_changed_toolchain_identity_changes_the_plan_identity() -> None:
    """K. The measurements are Git-2.39.5-specific, so the plan binds to the
    toolchain that produced them. Drift must stale the claim, and a version
    string alone is not an identity."""
    base = build_canonical_authority_plan_v2([_loose(), _pack()], _contract())

    other_binary = AdmissionContractV2(
        contract_id="sgaq-s0-admission-v1",
        claim_profile=_profile(),
        toolchain=GitToolchainIdentityV2(
            executable_digest="sha256:" + "9" * 64,   # different binary
            reported_version="2.39.5",                # SAME version string
            capability_profile_id="measured-2.39.5-exact-oid",
        ),
    )
    assert build_canonical_authority_plan_v2([_loose(), _pack()], other_binary).plan_digest != (
        base.plan_digest
    ), "same version string must not imply same toolchain identity"


def test_a_changed_claim_profile_changes_the_plan_identity() -> None:
    """The claim profile is part of the preimage, following ADR 0008: a binding
    an author can swap without changing the digest proves nothing."""
    base = build_canonical_authority_plan_v2([_loose(), _pack()], _contract())
    other = build_canonical_authority_plan_v2(
        [_loose(), _pack()], _contract(profile_id="a-different-profile")
    )
    assert base.plan_digest != other.plan_digest


# --------------------------------------------------------------------------
# the rules that keep the mechanism a grammar rather than a list
# --------------------------------------------------------------------------


def test_building_a_plan_does_not_widen_the_contract_that_produced_it() -> None:
    """No same-run widening: a carrier observed during run N cannot be added to
    run N's contract. Asserted structurally, by equality of the contract before
    and after, not by inspecting the implementation."""
    contract = _contract()
    before = (
        contract.contract_id,
        contract.claim_profile,
        contract.toolchain,
    )
    build_canonical_authority_plan_v2([_loose(), _future_carrier()], contract)
    assert (contract.contract_id, contract.claim_profile, contract.toolchain) == before

    with pytest.raises((AttributeError, TypeError)):
        contract.contract_id = "widened"  # type: ignore[misc]


def test_production_never_enumerates_historical_carrier_names() -> None:
    """The mechanism must be a positive grammar, not a list of past defeats.

    Each name below defeated an earlier attempt at #331 or was measured to be
    authority-affecting. A production module that mentions them -- even to
    exclude them -- is written against the carriers it has already met.
    """
    source = Path(
        "app/agent_review/sgaq_admission_v2.py"
    ).read_text() if Path("app/agent_review/sgaq_admission_v2.py").exists() else ""
    assert source, "the production module must exist"
    forbidden = (
        "multi-pack-index", "multi_pack_index", "midx",
        "grafts", "shallow", "commit-graph", "commit_graph",
        "bitmap", "promisor", ".keep", "alternates", "commondir",
    )
    mentioned = [name for name in forbidden if name in source.lower()]
    assert not mentioned, f"production enumerates historical carriers: {mentioned}"


def test_the_admissible_class_vocabulary_is_closed_and_small() -> None:
    """A vocabulary that grows to meet each new input is not a contract."""
    assert {member.value for member in GitRepresentationClassV2} == {
        "loose_object_payload",
        "pack_payload",
        "unrecognised",
    }
    assert {member.value for member in AdmissionDispositionV2} == {
        "admitted_source",
        "generated_only",
        "observed_not_consumed",
        "unknown_required",
        "forbidden",
    }


def test_every_observation_receives_exactly_one_disposition() -> None:
    """Totality over the observations, so nothing is silently dropped."""
    observations = [
        _loose(), _pack(), _future_carrier(),
        _observe("HEAD", prefix=b"ref: refs/heads/x\n"),
        _observe("config", prefix=b"[core]\n"),
        _observe("objects/info/whatever", prefix=b"\x00"),
        _observe(_LOOSE_LOCATION, kind=GitEntryKindV2.SYMLINK, prefix=_ZLIB),
        _observe("objects", kind=GitEntryKindV2.DIRECTORY),
    ]
    plan = build_canonical_authority_plan_v2(observations, _contract())
    accounted = (
        {entry.location for entry in plan.admitted}
        | {entry.location for entry in plan.not_consumed}
    )
    assert accounted == {observation.location for observation in observations}


def test_a_plan_retains_only_bounded_metadata_for_what_it_refused() -> None:
    """S0 does not build a Git-shaped quarantine, and never retains a carrier's
    bytes. Keeping the original pathname operational is how a refused carrier
    becomes an input again."""
    carrier = _future_carrier()
    plan = build_canonical_authority_plan_v2([_loose(), carrier], _contract())
    (refused,) = [entry for entry in plan.not_consumed if entry.location == carrier.location]
    assert not hasattr(refused, "content")
    assert not hasattr(refused, "payload")
    assert refused.content_digest == carrier.content_digest
    assert re.fullmatch(r"[0-9a-f]{64}", refused.content_digest)
