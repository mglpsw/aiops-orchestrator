"""Safe review material disposition -- #200-G2, Authority E replacement.

Successor primitive to #277's frozen, never-merged "suspect unless benign"
quoted-secret redesign of ``redaction.py`` (branch
``feat/200-f-derivable-operational-boundary``, `STOP_200F_ARCHITECTURE_NOT_CONVERGING`,
`DO_NOT_PORT`). See
``docs/checkpoints/AGENT_REVIEW_V2_200G2_SAFE_REVIEW_MATERIAL.md`` for the
full design rationale, differential-oracle evidence and review history.

This module does not replace ``redaction.py`` -- it composes on top of it
(the underlying detectors/transformers) and adds the disposition contract
the mission requires:

    SAFE_UNCHANGED | SAFELY_TRANSFORMED | BLOCKED_UNSAFE_TO_TRANSFORM

Core rule: material suspected sensitive AND whose safe transformation
cannot be PROVEN safe is never handed to a downstream model -- it is
blocked to manual/DLP handling rather than passed through on a "best
effort" basis. Concretely:

* if nothing suspicious was found, the material is unchanged
  (``SAFE_UNCHANGED``);
* if something was found and removed, EVERY witness value that triggered a
  transformation must be independently verified absent from the output
  before the disposition is allowed to claim success
  (``SAFELY_TRANSFORMED``) -- this is the postcondition-verification
  invariant the #277 lineage broke on every round ("claims `[REDACTED]`
  while leaking");
* if the underlying scanner could not bound a construct (an unterminated
  triple-quoted value, or a value whose extent exceeds the length circuit
  breaker), the disposition is ``BLOCKED_UNSAFE_TO_TRANSFORM`` -- it does
  NOT return best-effort transformed text, because "best effort" here is
  exactly the same shape of overclaim as the postcondition violation above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.agent_review.redaction import RedactionState, redact_text

# Defense in depth only -- `redact_text`'s scanner is linear (see
# redaction.py's module docstring and the ReDoS regression tests), so this
# should never fire on legitimate input. If it ever does, the mission is
# explicit that the response must be BLOCKED_UNSAFE_TO_TRANSFORM, never a
# silent pass-through that pretends nothing suspicious was there.
_MAX_MATERIAL_LENGTH = 5_000_000


class MaterialDisposition(str, Enum):
    SAFE_UNCHANGED = "safe_unchanged"
    SAFELY_TRANSFORMED = "safely_transformed"
    BLOCKED_UNSAFE_TO_TRANSFORM = "blocked_unsafe_to_transform"


@dataclass(frozen=True)
class DLPOverrideConfig:
    """Explicit target-owned override surface.

    Per ``docs/engineering/PROJECT_OVERLAY.md``, a target repository owns
    its own DLP policy surface; the engine itself lives in the toolrepo.
    This is the extension point a target-owned config would populate --
    wiring a concrete loader (profile/policy file -> this dataclass) is
    target-pack surface, out of scope for this primitive slice.

    ``additional_blocked_substrings``: literal substrings that, if present
    anywhere in the material, always force ``BLOCKED_UNSAFE_TO_TRANSFORM``
    regardless of what the generic detectors decide -- for material a
    target knows is sensitive by domain knowledge the generic engine can't
    have (e.g. an internal hostname fragment, a project-specific credential
    label).
    ``additional_safe_substrings``: literal values a target has reviewed
    and knows are safe (e.g. a fixture/test constant that happens to look
    like a secret). Wired into the redactor's OWN placeholder check
    (`RedactionState.extra_safe_values`) before it runs, so a listed value
    is never treated as suspect in the first place -- it is simply never a
    witness, and every value that IS a witness is still verified with no
    exemptions. #200-G2 round 2: an earlier version filtered this OUT of
    the post-hoc witness list instead, which silently stopped verifying
    ANY occurrence of that literal anywhere in the material once ONE
    occurrence was declared safe -- including a second, never-redacted
    occurrence elsewhere (e.g. repeated in a comment) that the postcondition
    check would otherwise have caught. Independent review reproduced that
    as a live leak; this is the fix, not a follow-up.
    """

    additional_blocked_substrings: frozenset[str] = field(default_factory=frozenset)
    additional_safe_substrings: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SafeMaterialResult:
    disposition: MaterialDisposition
    output: str | None
    original_length: int
    replacements_by_type: dict[str, int]
    secret_like_values_found: int
    blocked_reasons: tuple[str, ...] = ()
    postcondition_verified: bool = True

    @property
    def redaction_applied(self) -> bool:
        return self.disposition is MaterialDisposition.SAFELY_TRANSFORMED


def derive_safe_review_material(
    text: str,
    *,
    dlp_config: DLPOverrideConfig | None = None,
) -> SafeMaterialResult:
    """Derive the disposition for a single unit of review material.

    Operates on one string (a file's content, or a chunk) -- there is no
    cross-chunk context here, which is why an unterminated multiline
    construct at the end of the given text is treated as unboundable rather
    than assumed to end at end-of-text.
    """
    original_length = len(text)

    if dlp_config is not None:
        for forbidden in dlp_config.additional_blocked_substrings:
            if forbidden and forbidden in text:
                return SafeMaterialResult(
                    disposition=MaterialDisposition.BLOCKED_UNSAFE_TO_TRANSFORM,
                    output=None,
                    original_length=original_length,
                    replacements_by_type={},
                    secret_like_values_found=0,
                    blocked_reasons=("target_dlp_override_blocked_substring",),
                )

    if original_length > _MAX_MATERIAL_LENGTH:
        return SafeMaterialResult(
            disposition=MaterialDisposition.BLOCKED_UNSAFE_TO_TRANSFORM,
            output=None,
            original_length=original_length,
            replacements_by_type={},
            secret_like_values_found=0,
            blocked_reasons=("material_exceeds_length_circuit_breaker",),
        )

    state = RedactionState()
    if dlp_config is not None and dlp_config.additional_safe_substrings:
        state.extra_safe_values = dlp_config.additional_safe_substrings
    output = redact_text(text, state)
    # No post-hoc witness filtering: every witness the scanner actually
    # recorded is verified, unconditionally. A DLP-declared-safe value
    # never becomes a witness in the first place (see `state.
    # extra_safe_values` above), so there is nothing to exempt here -- see
    # the round-2 correction note on `DLPOverrideConfig.additional_safe_
    # substrings` for why a separate exemption step here was the bug.
    witnesses = list(state.redacted_witnesses)

    if state.unbounded_construct_present:
        return SafeMaterialResult(
            disposition=MaterialDisposition.BLOCKED_UNSAFE_TO_TRANSFORM,
            output=None,
            original_length=original_length,
            replacements_by_type=dict(state.replacements_by_type),
            secret_like_values_found=state.secret_like_values_found,
            blocked_reasons=("unbounded_construct_present",),
            postcondition_verified=False,
        )

    if state.secret_like_values_found == 0:
        # Nothing was flagged. `output` should be byte-identical to `text`;
        # if it is not, something changed material we did not account for,
        # which is itself a bug this asserts against rather than silently
        # returning a divergent "unchanged" claim.
        if output != text:
            return SafeMaterialResult(
                disposition=MaterialDisposition.BLOCKED_UNSAFE_TO_TRANSFORM,
                output=None,
                original_length=original_length,
                replacements_by_type=dict(state.replacements_by_type),
                secret_like_values_found=state.secret_like_values_found,
                blocked_reasons=("unaccounted_output_divergence",),
                postcondition_verified=False,
            )
        return SafeMaterialResult(
            disposition=MaterialDisposition.SAFE_UNCHANGED,
            output=output,
            original_length=original_length,
            replacements_by_type=dict(state.replacements_by_type),
            secret_like_values_found=0,
        )

    verified = _verify_postcondition(output, witnesses)
    if not verified:
        # The single highest-priority invariant per the mission: a claimed
        # transformation whose witnesses are not actually gone must never
        # be reported as a success. Fail closed instead.
        return SafeMaterialResult(
            disposition=MaterialDisposition.BLOCKED_UNSAFE_TO_TRANSFORM,
            output=None,
            original_length=original_length,
            replacements_by_type=dict(state.replacements_by_type),
            secret_like_values_found=state.secret_like_values_found,
            blocked_reasons=("postcondition_verification_failed",),
            postcondition_verified=False,
        )

    return SafeMaterialResult(
        disposition=MaterialDisposition.SAFELY_TRANSFORMED,
        output=output,
        original_length=original_length,
        replacements_by_type=dict(state.replacements_by_type),
        secret_like_values_found=state.secret_like_values_found,
        postcondition_verified=True,
    )


def _verify_postcondition(output: str, witnesses: list[str]) -> bool:
    """Every witness value that triggered a redaction must be absent from
    the final output.

    A witness shorter than 4 characters is skipped: a witness that short
    (e.g. an empty/near-empty matched value) is not a meaningful leak
    signal and could produce spurious "still present" hits against
    unrelated short substrings of ordinary text (e.g. a 2-character
    witness matching inside an unrelated word) -- this bound is named and
    tested, not silently assumed.
    """
    for witness in witnesses:
        if len(witness) < 4:
            continue
        if witness in output:
            return False
    return True
