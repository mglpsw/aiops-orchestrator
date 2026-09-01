"""Sidecar contract for AgentReview v2's real, redacted hunk content (#200-A,
child of the distribution epic #199).

## Why a sidecar, not an extension of ``ChunkPayloadV2`` (D1)

``ChunkPayloadV2`` (``contracts_v2.py:608``) is a PUBLISHED, already-pinned
(``v0.21.0``) contract. Its ``payload_sha256`` is computed over every one of
its OWN fields (``contracts_v2.compute_payload_sha256_v2`` /
``_build_chunk_payload``, ``payload_builder_v2.py:131-150``) and is itself
the exact value ``consumer_v2.validate_response_binding_v2`` compares against
an incoming response. Adding a content field to ``ChunkPayloadV2`` would
change that hash for every chunk ever built -- invalidating every already-
pinned response binding and forcing a new ``schema_version`` on a contract
this codebase has explicitly decided never to change silently.

``ReviewContentV2`` is therefore a SEPARATE, independently versioned
artifact, bound to a specific manifest by ``run_id``/``manifest_hash`` --
exactly ``payload_set_v2.PayloadSetV2``'s own precedent for solving the same
problem (an artifact whose existence would otherwise force a circular
dependency into ``ManifestV2``/``RunIdentityV2``, see that module's
docstring). Not one byte of ``ChunkPayloadV2`` or its hash preimage changes
because this module exists.

## The second integrity anchor (D2)

``payload_sha256`` proves a response BELONGS to a given chunk. It does not
prove the response was produced over ONE SPECIFIC ``ReviewContentV2`` sidecar
rather than some other, equally ``payload_sha256``-matching one built for the
same chunk. Closing that gap is a TWO-part contract:

1. this module gives every fragment's redacted content, and therefore every
   chunk's content, a content-derived hash (``FragmentContentV2.
   content_sha256`` / ``ChunkContentV2.content_sha256``) computed from the
   actual bytes sent, not merely referenced;
2. ``review_transport_contract_v2.py`` (this same PR) wraps the outbound
   request and the untouched ``ChunkResponseEnvelopeValueV2`` in a NEW,
   independently versioned transport envelope that the far end must echo
   both hashes back through, verified BEFORE ``consumer_v2.
   bind_chunk_response_v2`` ever sees the inner response.

This module owns only half of that chain (the content hash itself); the echo
verification lives in the sibling transport module, deliberately kept
separate so a change to transport plumbing never has to touch this frozen
content contract.

## DLP is declarative or host-owned by construction (D3)

``DlpPolicyDeclarationV2`` below is a closed (``ContractV2Model``'s
``extra=\"forbid\"``) schema with no ``path``, ``module``, ``import``, or
``entrypoint`` field -- there is structurally nowhere in this contract for a
target to name code inside its own repository for the analysis job to
import or execute. A target's only options are inline declarative
``DlpPolicyRuleV2`` entries (pattern + action, interpreted by a host-owned
engine, never executed as code) or a reference to a host-owned detector by
name, pinned by digest. Loading, allowlisting, and executing an actual
detector is #200-B's job (a later PR in this DAG); this module only freezes
the shape a target may declare and the digest that pins it.

## ``must_review`` cannot be silently omitted (D4)

A fragment the manifest marks ``coverage_required=True``
(``manifest_v2.FragmentV2.coverage_required``) must never end up with
``policy`` other than ``INCLUDED`` -- ``FragmentContentV2``'s own validator
enforces this structurally (a ``ValidationError`` at construction, not a
``limitations`` entry), and ``bind_review_content_to_manifest_v2`` re-checks
it against the manifest's OWN truth as defense in depth. Replanning a
fragment that would otherwise be dropped for budget is #200-B's job
(the extractor); this module only refuses to represent the unsafe state.

## What ``ReviewContentPolicyV2`` values ARE, and are not

``OMITTED_OVER_BUDGET``, ``BLOCKED_BY_REDACTION``, ``BLOCKED_BY_TARGET_DLP``,
``OMITTED_BINARY``, ``OMITTED_SUBMODULE``, ``OMITTED_GENERATED``,
``OMITTED_MINIFIED``, and ``UNREPRESENTABLE`` are not exceptions this module
raises -- they are the reason record for one fragment, carried on the
fragment itself, exactly the shape ``ManifestDegradationV2.reason_code``
already uses for the manifest layer. A caller that needs a stable string for
telemetry/logging uses ``policy.value`` directly; no separate, duplicate
reason-code constant is defined for these four states.

## Deliberately out of scope here (left to #200-B / #200-C)

- extracting real hunk bytes from a diff (``diff_acquisition_v2`` already
  owns diff acquisition; turning that into ``FragmentContentV2`` instances is
  #200-B's job);
- running redaction or a DLP engine (#200-B);
- cross-validating ``ChunkContentV2.payload_sha256`` against a REAL, built
  ``ChunkPayloadV2`` -- this module's ``bind_review_content_to_manifest_v2``
  only has the (payload-agnostic) ``ManifestV2`` to check against, mirroring
  ``payload_set_v2.bind_payload_set_to_manifest_v2``'s own scope; the
  payload-level cross-check is #200-C's job, mirroring
  ``payload_set_emission_v2.bind_payload_set_to_payloads_v2``
  (``CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2`` is reserved for it here,
  not raised by this module);
- calling the Agent Router, or any transport (#200-C /
  ``review_transport_contract_v2.py``);
- allowlisting or loading a host-owned DLP detector by name (#200-B).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StrictBool, StrictStr, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    NonNegativeInt,
    RelativePath,
    SafeIdentifier,
    SafeText,
    Sha256,
)
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.redaction import sanitize_artifact_value
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

REVIEW_CONTENT_SCHEMA_V2 = "agent-review.review-content.v2"
DLP_POLICY_SCHEMA_V1 = "agent-review.dlp-policy.v1"

# -- Reason codes ------------------------------------------------------------
#
# Raised by bind_review_content_to_manifest_v2 (cross-object: content vs a
# SPECIFIC manifest it claims to describe -- mirrors
# run_fragment_coverage_v2.bind_coverage_report_to_manifest_v2 and
# payload_set_v2.bind_payload_set_to_manifest_v2's own reason-code style).
CONTENT_RUN_IDENTITY_MISMATCH_REASON_V2 = "content_run_identity_mismatch"
CONTENT_MANIFEST_HASH_MISMATCH_REASON_V2 = "content_manifest_hash_mismatch"
CONTENT_CHUNK_SET_MISMATCH_REASON_V2 = "content_chunk_set_mismatch"
CONTENT_FRAGMENT_NOT_IN_MANIFEST_REASON_V2 = "content_fragment_not_in_manifest"
CONTENT_PATH_MISMATCH_REASON_V2 = "content_path_mismatch"
CONTENT_DIFF_SHA256_MISMATCH_REASON_V2 = "content_diff_sha256_mismatch"
CONTENT_COVERAGE_REQUIRED_MISMATCH_REASON_V2 = "content_coverage_required_mismatch"
CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2 = "content_required_fragment_missing"
CONTENT_SET_HASH_MISMATCH_REASON_V2 = "content_set_hash_mismatch"
# Reserved for #200-C's cross-check against a REAL, built ChunkPayloadV2 --
# see the module docstring's "Deliberately out of scope" section. Not
# raised anywhere in this module.
CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2 = "content_payload_sha256_mismatch"

# Raised by load_dlp_policy_declaration_v2 / verify_dlp_policy_digest_v2.
DLP_POLICY_NOT_HOST_OWNED_REASON_V2 = "dlp_policy_not_host_owned"
DLP_POLICY_DIGEST_MISMATCH_REASON_V2 = "dlp_policy_digest_mismatch"

_MAX_FRAGMENT_CONTENT_CHARS_V2 = 262_144  # 256 KiB of characters; a hard
# contractual ceiling only. Real per-chunk/per-fragment budgets are derived
# from TargetBudgetsV2 (contracts_v2.py:911) and enforced by #200-B's
# extractor, which decides OMITTED_OVER_BUDGET/replanning long before a
# fragment would ever approach this ceiling.

_FORBIDDEN_DLP_POLICY_KEYS_V2 = frozenset(
    {"path", "module", "import", "entrypoint", "entry_point", "code", "script"}
)


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, run_fragment_coverage_v2.py, payload_set_v2.py, and
    # evidence_hash_v2.py -- not a new rule.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _validate_reviewable_content_text_v2(value: str) -> str:
    """Guard for ``FragmentContentV2.content``: real, multi-line source
    text, so ``SafeText`` (``contracts_v2.py:232``, ``max_length=512`` and
    rejecting EVERY control character including ``\\n``) is the wrong type
    here by design, not merely a stricter one -- it would reject a two-line
    diff hunk outright. This validator keeps the same control-character
    discipline ``_validate_safe_text`` applies, except newline and tab stay
    allowed, and reuses ``redaction.sanitize_artifact_value`` (the same
    public sanitizer ``contracts_v2.py`` itself imports) as a LAST-LINE
    guard: if anything the sanitizer would still touch is present, this
    fragment must not be constructed at all -- #200-B's extractor is
    required to redact BEFORE calling this constructor, never rely on it to
    redact silently."""

    if not value:
        raise ValueError("reviewable content must be non-empty when present")
    if len(value) > _MAX_FRAGMENT_CONTENT_CHARS_V2:
        raise ValueError("reviewable content exceeds the maximum contractual length")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("reviewable content must be valid UTF-8") from exc
    for character in value:
        if character in ("\n", "\t"):
            continue
        if unicodedata.category(character).startswith("C"):
            raise ValueError("reviewable content contains a forbidden control character")
    if sanitize_artifact_value(value) != value:
        raise ValueError("reviewable content still contains a secret-like token or local path")
    return value


ReviewableContentTextV2 = Annotated[
    StrictStr,
    Field(min_length=1, max_length=_MAX_FRAGMENT_CONTENT_CHARS_V2),
    AfterValidator(_validate_reviewable_content_text_v2),
]


class ReviewContentPolicyV2(str, Enum):
    INCLUDED = "included"
    OMITTED_BINARY = "omitted_binary"
    OMITTED_SUBMODULE = "omitted_submodule"
    OMITTED_GENERATED = "omitted_generated"
    OMITTED_MINIFIED = "omitted_minified"
    OMITTED_OVER_BUDGET = "omitted_over_budget"
    BLOCKED_BY_REDACTION = "blocked_by_redaction"
    BLOCKED_BY_TARGET_DLP = "blocked_by_target_dlp"
    UNREPRESENTABLE = "unrepresentable"


ReviewContentPolicyValueV2 = Annotated[ReviewContentPolicyV2, Field(strict=False)]


class FragmentContentV2(ContractV2Model):
    """One fragment's redacted, reviewable content -- or the typed reason it
    has none. Independently verifiable from its own fields alone; matching
    ``fragment_id``/``path``/``diff_sha256`` against a SPECIFIC manifest is
    ``bind_review_content_to_manifest_v2``'s job, not this model's (a bare
    Pydantic model cannot reach out to another object at construction time
    -- the same split every other v2 binder in this codebase already uses)."""

    fragment_id: Sha256
    path: RelativePath
    diff_sha256: Sha256
    policy: ReviewContentPolicyValueV2
    coverage_required: StrictBool
    content: ReviewableContentTextV2 | None
    content_sha256: Sha256 | None
    redaction_applied: StrictBool
    chars: NonNegativeInt

    @model_validator(mode="after")
    def validate_fragment_content(self) -> FragmentContentV2:
        included = self.policy is ReviewContentPolicyV2.INCLUDED
        if included != (self.content is not None):
            raise ValueError("content must be present if and only if policy is included")
        if included != (self.content_sha256 is not None):
            raise ValueError("content_sha256 must be present if and only if policy is included")
        if included:
            assert self.content is not None and self.content_sha256 is not None
            expected_sha256 = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            if self.content_sha256 != expected_sha256:
                raise ValueError("content_sha256 does not match the sha256 of content")
            if self.chars != len(self.content):
                raise ValueError("chars must equal the character length of content")
        elif self.chars != 0:
            raise ValueError("chars must be zero when content is absent")
        if self.coverage_required and not included:
            # D4 of the #199 execution plan: a must-review fragment can
            # never be represented as omitted/blocked -- this is a
            # structural refusal to construct the unsafe object, not a
            # limitation recorded on it. See CONTENT_REQUIRED_FRAGMENT_
            # MISSING_REASON_V2 for the matching reason code a caller that
            # detects this BEFORE attempting construction (#200-B) should
            # surface instead of catching a bare ValidationError.
            raise ValueError(
                f"a coverage_required fragment must not omit content "
                f"({CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2})"
            )
        return self


class ChunkContentV2(ContractV2Model):
    """One chunk's complete set of fragment contents, with its own
    content-derived hash (``content_sha256``) -- the per-chunk half of the
    second integrity anchor described in the module docstring (D2)."""

    chunk_id: SafeIdentifier
    payload_sha256: Sha256
    fragments: list[FragmentContentV2]
    content_sha256: Sha256

    @model_validator(mode="after")
    def validate_chunk_content(self) -> ChunkContentV2:
        if not self.fragments:
            raise ValueError("a chunk must carry at least one fragment content entry")
        fragment_ids = [fragment.fragment_id for fragment in self.fragments]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("fragment_id must be unique within a chunk's content")
        expected = compute_chunk_content_sha256_v2(self)
        if self.content_sha256 != expected:
            raise ValueError("content_sha256 does not match the canonical chunk content material")
        return self


def _chunk_content_preimage_v2(chunk: ChunkContentV2) -> dict[str, object]:
    """Hash preimage for ``ChunkContentV2.content_sha256``: every field
    except that hash itself, with ``fragments`` re-sorted by
    ``fragment_id`` -- the same canonical-ordering discipline every other
    list-of-entries hash in this v2 surface already applies (``payload_set_
    v2.canonical_payload_set_bytes_v2``'s ``payloads``, ``evidence_hash_v2.
    canonical_evidence_bundle_bytes_v2``'s ``references``). Fragments are
    already validated unique by ``fragment_id`` above, so sorting cannot
    silently collapse two distinct entries, and reordering the
    constructor's input never changes these bytes."""

    dumped = chunk.model_dump(mode="json", exclude={"content_sha256"})
    dumped["fragments"] = sorted(dumped["fragments"], key=lambda fragment: fragment["fragment_id"])
    return dumped


def compute_chunk_content_sha256_v2(chunk: ChunkContentV2) -> str:
    return hashlib.sha256(_canonical_json_bytes_v2(_chunk_content_preimage_v2(chunk))).hexdigest()


class ReviewContentMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.review-content.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-build-review-content"]
    run_id: Sha256
    manifest_hash: Sha256
    dlp_policy_digest: Sha256 | None
    chunks: list[ChunkContentV2]
    limitations: list[SafeIdentifier]

    @model_validator(mode="after")
    def validate_material(self) -> ReviewContentMaterialV2:
        if not self.chunks:
            raise ValueError("review content must cover at least one chunk")
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("chunk_id must be unique across the review content set")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("limitations must be unique")
        return self


def _review_content_preimage_v2(value: ReviewContentMaterialV2 | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, ReviewContentMaterialV2):
        dumped = value.model_dump(mode="json", exclude={"content_set_sha256"})
    elif isinstance(value, Mapping):
        raw = dict(value)
        raw.pop("content_set_sha256", None)
        # model_validate, not model_validate_json: mirrors payload_set_v2.
        # canonical_payload_set_bytes_v2 / evidence_hash_v2.
        # canonical_evidence_bundle_bytes_v2's own precedent, not
        # contracts_v2._chunk_payload_material's (that helper's
        # model_validate_json choice is specifically about ChunkCoverageV2's
        # tuple-typed fields, which nothing in this module's shape has).
        # model_validate accepts a mapping whose "chunks" entries are either
        # already-dumped dicts or real ChunkContentV2 instances -- both are
        # valid inputs for a caller assembling this material by hand before
        # its own hash exists yet (mirrors emit_payload_set_v2's own
        # "material" dict, which embeds real PayloadSetEntryV2 instances).
        dumped = ReviewContentMaterialV2.model_validate(raw).model_dump(mode="json")
    else:
        raise TypeError("review content must be a model or mapping")
    dumped["chunks"] = sorted(dumped["chunks"], key=lambda chunk: chunk["chunk_id"])
    return dumped


def canonical_review_content_bytes_v2(value: ReviewContentMaterialV2 | Mapping[str, object]) -> bytes:
    """Hash preimage: every validated field except ``content_set_sha256``
    itself, with ``chunks`` re-sorted by ``chunk_id`` regardless of
    construction order -- mirrors ``payload_set_v2.
    canonical_payload_set_bytes_v2`` exactly, reused as a pattern, not
    duplicated as shared code (this artifact's material shape differs)."""

    return _canonical_json_bytes_v2(_review_content_preimage_v2(value))


def compute_review_content_sha256_v2(value: ReviewContentMaterialV2 | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_review_content_bytes_v2(value)).hexdigest()


class ReviewContentV2(ReviewContentMaterialV2):
    content_set_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> ReviewContentV2:
        expected = compute_review_content_sha256_v2(self)
        if self.content_set_sha256 != expected:
            raise ValueError("content_set_sha256 does not match the canonical review content material")
        return self


def verify_review_content_sha256_v2(content: ReviewContentV2) -> None:
    """Revalidate ``content_set_sha256`` on every read, not just at
    construction time -- mirrors ``contracts_v2.verify_payload_sha256_v2`` /
    ``payload_set_v2.verify_payload_set_sha256_v2``: catches an object that
    bypassed ``validate_hash`` (for example via ``model_construct``),
    rather than trusting that the in-memory object was built through the
    normal constructor."""

    encoded = _canonical_json_bytes_v2(content.model_dump(mode="json"))
    ReviewContentV2.model_validate_json(encoded)


class ReviewContentBindingError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by ``bind_review_content_to_manifest_v2``. Carries a stable
    ``reason_code`` only -- never manifest or content material."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def bind_review_content_to_manifest_v2(content: ReviewContentV2, manifest: ManifestV2) -> None:
    """Cross-check ``content`` against the SPECIFIC ``ManifestV2`` it claims
    to describe. Raises ``ReviewContentBindingError`` fail-closed on any
    divergence; returns ``None`` on success. Mirrors ``payload_set_v2.
    bind_payload_set_to_manifest_v2`` and ``run_fragment_coverage_v2.
    bind_coverage_report_to_manifest_v2``'s own shape and discipline.

    Does NOT cross-check ``ChunkContentV2.payload_sha256`` against a real,
    built ``ChunkPayloadV2`` -- this function only has ``manifest`` (which
    carries no payload hashes at all: ``ManifestChunkV2`` has no
    ``payload_sha256`` field). That check is #200-C's job, exactly as
    ``payload_set_v2``'s own manifest-only bind function defers the
    real-payload cross-check to ``payload_set_emission_v2.
    bind_payload_set_to_payloads_v2``.
    """

    if content.run_id != manifest.run_id:
        raise ReviewContentBindingError(CONTENT_RUN_IDENTITY_MISMATCH_REASON_V2)
    if content.manifest_hash != manifest.identity.manifest_hash:
        raise ReviewContentBindingError(CONTENT_MANIFEST_HASH_MISMATCH_REASON_V2)

    manifest_chunk_ids = {chunk.chunk_id for chunk in manifest.chunks}
    content_chunk_ids = {chunk.chunk_id for chunk in content.chunks}
    if content_chunk_ids != manifest_chunk_ids:
        raise ReviewContentBindingError(CONTENT_CHUNK_SET_MISMATCH_REASON_V2)

    fragment_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
    manifest_chunks_by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}

    for chunk_content in content.chunks:
        manifest_chunk = manifest_chunks_by_id[chunk_content.chunk_id]
        expected_fragment_ids = set(manifest_chunk.fragment_ids)
        actual_fragment_ids = {fragment.fragment_id for fragment in chunk_content.fragments}
        if actual_fragment_ids != expected_fragment_ids:
            raise ReviewContentBindingError(CONTENT_FRAGMENT_NOT_IN_MANIFEST_REASON_V2)

        for fragment_content in chunk_content.fragments:
            manifest_fragment = fragment_by_id[fragment_content.fragment_id]
            if fragment_content.path != manifest_fragment.path:
                raise ReviewContentBindingError(CONTENT_PATH_MISMATCH_REASON_V2)
            if fragment_content.diff_sha256 != manifest_fragment.diff_sha256:
                raise ReviewContentBindingError(CONTENT_DIFF_SHA256_MISMATCH_REASON_V2)
            if fragment_content.coverage_required != manifest_fragment.coverage_required:
                raise ReviewContentBindingError(CONTENT_COVERAGE_REQUIRED_MISMATCH_REASON_V2)
            # Defense in depth: FragmentContentV2.validate_fragment_content
            # already forbids this combination structurally (D4) --
            # unreachable through this function's own call path today (a
            # content object that reached this point already passed its
            # own construction validator), kept in case a future change to
            # that validator reopens it. Mirrors run_fragment_coverage_v2's
            # own precedent for keeping provably-redundant checks.
            if manifest_fragment.coverage_required and fragment_content.policy is not ReviewContentPolicyV2.INCLUDED:
                raise ReviewContentBindingError(CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2)

    try:
        verify_review_content_sha256_v2(content)
    except ValueError as exc:
        # Narrowed from `except Exception` for the same reason as the digest
        # verifiers in `payload_set_emission_v2`: a `TypeError` from a defect
        # INSIDE the verifier would have been reported to an operator as a
        # content-set hash mismatch. Tampering surfaces as a contract or
        # serialization failure, both `ValueError`; a defect must stay a crash.
        raise ReviewContentBindingError(CONTENT_SET_HASH_MISMATCH_REASON_V2) from exc


# -- Declarative / host-owned DLP policy contract (D3) -----------------------


class DlpPolicyRuleV2(ContractV2Model):
    """One declarative DLP rule: a named pattern, evaluated by a host-owned
    engine (#200-B). ``pattern`` is data interpreted by that engine -- never
    executed as code, and never a path into the target repository."""

    rule_id: SafeIdentifier
    pattern: SafeText
    action: Literal["block"]
    detail: SafeText


class DlpPolicyDeclarationV2(ContractV2Model):
    """A target's declared DLP policy: EITHER inline declarative rules OR a
    reference to a host-owned, digest-pinned detector -- never a module
    path, import string, or entry point naming code inside the target
    repository. This is enforced structurally: ``ContractV2Model``'s
    ``extra=\"forbid\"`` means this class defines the COMPLETE set of fields
    a valid document may have, and none of them is ``path``, ``module``,
    ``import``, or ``entrypoint`` -- there is nowhere in this schema for a
    target to smuggle one in."""

    schema_id: Literal["agent-review.dlp-policy.v1"]
    schema_version: Literal[1]
    rules: list[DlpPolicyRuleV2]
    detector_name: SafeIdentifier | None
    detector_digest: Sha256 | None

    @model_validator(mode="after")
    def validate_declaration(self) -> DlpPolicyDeclarationV2:
        if not self.rules and self.detector_name is None:
            raise ValueError("a DLP policy must declare inline rules or a host-owned detector")
        if (self.detector_name is None) != (self.detector_digest is None):
            raise ValueError("detector_name and detector_digest must both be present or both absent")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_id must be unique within a DLP policy")
        return self


def compute_dlp_policy_digest_v2(declaration: DlpPolicyDeclarationV2) -> str:
    """The value meant for ``ReviewContentMaterialV2.dlp_policy_digest``:
    the exact digest a target pack's receipt (#203) pins so a later
    installation can prove which policy an analysis run actually applied."""

    return hashlib.sha256(_canonical_json_bytes_v2(declaration.model_dump(mode="json"))).hexdigest()


class DlpPolicyContractError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by ``load_dlp_policy_declaration_v2`` /
    ``verify_dlp_policy_digest_v2``. Carries a stable ``reason_code`` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def load_dlp_policy_declaration_v2(raw: Mapping[str, object]) -> DlpPolicyDeclarationV2:
    """Parse a target-declared DLP policy document strictly. A document
    carrying any of ``_FORBIDDEN_DLP_POLICY_KEYS_V2`` -- naming code inside
    the target repository -- is rejected as ``dlp_policy_not_host_owned``
    BEFORE validation is even attempted. ``DlpPolicyDeclarationV2``'s own
    closed schema (``extra=\"forbid\"``) already rejects any key this
    contract does not define, including this exact forbidden set, so the
    explicit check below is defense in depth, not the only guard -- kept
    separate so the specific, security-relevant reason code is never
    conflated with an ordinary shape error (a missing ``rules`` entry, an
    invalid ``rule_id``, ...), which is left to propagate as a plain
    ``pydantic.ValidationError``."""

    document = dict(raw)
    if _FORBIDDEN_DLP_POLICY_KEYS_V2 & set(document):
        raise DlpPolicyContractError(DLP_POLICY_NOT_HOST_OWNED_REASON_V2)
    return DlpPolicyDeclarationV2.model_validate(document)


def verify_dlp_policy_digest_v2(declaration: DlpPolicyDeclarationV2, *, expected_digest: str) -> None:
    """Fail-closed check that a loaded policy still matches the digest a
    target pack receipt (#203) pinned it to. Raises
    ``DlpPolicyContractError(DLP_POLICY_DIGEST_MISMATCH_REASON_V2)`` on any
    divergence."""

    if compute_dlp_policy_digest_v2(declaration) != expected_digest:
        raise DlpPolicyContractError(DLP_POLICY_DIGEST_MISMATCH_REASON_V2)
