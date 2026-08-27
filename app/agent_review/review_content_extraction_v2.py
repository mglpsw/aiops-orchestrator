"""Real hunk-content extraction for AgentReview v2 (#200-B, second slice of
#200, child of the distribution epic #199).

Turns a REAL diff acquired via ``diff_acquisition_v2.acquire_authoritative_
diff_v2`` (#103/#84) into a ``review_content_v2.ReviewContentV2`` sidecar
bound to an ALREADY-ASSEMBLED ``ManifestV2`` (produced by ``run_assembly_v2``,
#129) -- reusing every existing primitive named by issue #200's own
"Ownership" section, never a second engine:

``diff_acquisition_v2.acquire_authoritative_diff_v2`` (path/hunk identity),
``diff_acquisition_v2.extract_hunk_bodies_v2`` (#200-B's own addition to that
SAME parser, not a new one -- see its docstring), ``redaction.redact_text``
(generic redaction), ``review_content_v2`` (#200-A's sidecar contract, DLP
declaration, and manifest binding), and ``manifest_v2``/``planner_v2``'s
existing ``LineRangeV2``/``FragmentV2`` shapes (never redefined here).

## The flow (mirrors the #199 execution plan's D-series exactly)

```text
authoritative diff (acquire_authoritative_diff_v2 + extract_hunk_bodies_v2)
  -> per-fragment body slice (slice_hunk_body_by_range_v2)
  -> exact recomposition check for whole-hunk fragments (diff_sha256)
  -> classification (binary / submodule / hunkless -> typed omission policy)
  -> generic redaction (redaction.redact_text)
  -> declarative DLP (review_content_v2.DlpPolicyDeclarationV2)
  -> per-chunk char budget (TargetBudgetsV2.max_chars_per_chunk)
  -> ReviewContentV2, bound to the manifest via
     review_content_v2.bind_review_content_to_manifest_v2
```

## The line-selection rule (why a "window" fragment is not a naive substring)

A fragment's ``old_range``/``new_range`` are computed by ``planner_v2.
_proportional_window`` INDEPENDENTLY per side (issue #84's own windowing).
For a hunk with a long uninterrupted run of deletions followed by a long run
of insertions, "window k" on the old side and "window k" on the new side can
correspond to physically disjoint stretches of the hunk body -- there is no
single contiguous substring that represents a window fragment in general.

The correct, lossless reconstruction is therefore per LINE, not per
substring: walk the hunk body once, assign each line the absolute old/new
line number(s) it represents (a context line has both; a deletion only an
old number; an insertion only a new number), and select a line if EITHER
its old line number falls inside the fragment's ``old_range`` OR its new
line number falls inside the fragment's ``new_range`` -- in original body
order. This is provably lossless (every line the fragment's declared range
claims to cover is included exactly once) and, for the common
whole-hunk-as-one-fragment case (the fragment's range IS the hunk's full
range), it reproduces the entire body and independently re-derives
``diff_sha256`` -- verified below, not assumed.

## What this module deliberately does NOT do

- call the Agent Router or any transport -- #200-C;
- automatically re-plan a chunk whose extracted content exceeds
  ``TargetBudgetsV2.max_chars_per_chunk`` by invoking ``planner_v2`` with a
  smaller line budget and retrying. That loop needs the ORIGINAL
  ``HunkInputV2`` list (this module only has the already-assembled
  ``ManifestV2``), and reaching into ``run_assembly_v2``'s territory to
  rebuild it would blur this module's one job (extraction) with
  ``run_assembly_v2``'s (planning). A ``must_review`` fragment whose real
  content does not fit its budget therefore blocks the WHOLE extraction
  with ``CONTENT_OVER_BUDGET_REQUIRES_REPLAN_REASON_V2`` -- explicit,
  fail-closed, zero Router calls -- rather than a same-call automatic
  replan loop. Closing that loop is named as a limitation, not silently
  implemented as if it already existed.
"""

from __future__ import annotations

import re
import hashlib
from typing import Mapping, Sequence

from pydantic import ValidationError

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.diff_acquisition_v2 import (
    DiffAcquisitionError,
    HunkBodyV2,
    ParsedFileDiffV2,
    acquire_authoritative_diff_v2,
    acquire_diff_v2,
    compute_hunk_diff_sha256_v2,
    extract_hunk_bodies_v2,
)
from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, ManifestV2
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2
from app.agent_review.redaction import _redact_local_paths, redact_text
from app.agent_review.review_content_v2 import (
    ChunkContentV2,
    DlpPolicyDeclarationV2,
    FragmentContentV2,
    ReviewContentPolicyV2,
    ReviewContentV2,
    ReviewContentBindingError,
    bind_review_content_to_manifest_v2,
    compute_chunk_content_sha256_v2,
    compute_dlp_policy_digest_v2,
    compute_review_content_sha256_v2,
)
from app.agent_review.redaction import RedactionState

CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2 = "no_reviewable_chunks"
CONTENT_REASON_HUNK_BODY_UNAVAILABLE_V2 = "hunk_body_unavailable"
CONTENT_REASON_HUNK_RECOMPOSITION_FAILED_V2 = "hunk_recomposition_failed"
CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2 = "content_over_budget_requires_replan"
CONTENT_REASON_DIFF_ACQUISITION_FAILED_V2 = "diff_acquisition_failed"
CONTENT_REASON_WINDOW_OWNS_NO_LINES_V2 = "window_owns_no_real_lines"
CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2 = "dlp_detector_not_executed"
CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2 = "chunk_content_over_budget_requires_replan"
CONTENT_REASON_PROFILE_HASH_MISMATCH_V2 = "target_profile_hash_mismatch"

_GENERATED_PATH_MARKERS_V2: tuple[str, ...] = (
    "/generated/",
    ".generated.",
    "-lock.",
    ".lock.json",
)
_MINIFIED_PATH_MARKERS_V2: tuple[str, ...] = (".min.js", ".min.css")


CONTENT_REASON_CONTRACT_INVALID_V2 = "content_contract_invalid"


class ExtractionBlockedError(ValueError):
    """Raised by ``extract_review_content_v2`` when the WHOLE extraction
    must be refused fail-closed (a ``must_review`` fragment cannot be
    represented safely). Carries a stable ``reason_code`` and the
    ``fragment_id`` it applies to (never raw content)."""

    def __init__(self, reason_code: str, *, fragment_id: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.fragment_id = fragment_id


def _walk_hunk_body_lines_v2(
    body: HunkBodyV2,
) -> tuple[tuple[str, int | None, int | None], ...]:
    """One entry per body line: ``(line_text, old_line_number, new_line_number)``,
    exactly one of which is ``None`` for a pure +/- line, neither ``None``
    for a context line. Mirrors the SAME counting rule ``_FileBlockBuilder``
    used to build ``old_lines``/``new_lines`` when this body was parsed."""

    lines: list[tuple[str, int | None, int | None]] = []
    old_line = body.old_start
    new_line = body.new_start
    for raw_line in body.body_text.split("\n"):
        marker = raw_line[:1]
        if marker == " ":
            lines.append((raw_line, old_line, new_line))
            old_line += 1
            new_line += 1
        elif marker == "-":
            lines.append((raw_line, old_line, None))
            old_line += 1
        elif marker == "+":
            lines.append((raw_line, None, new_line))
            new_line += 1
        else:
            # Defensive only: extract_hunk_bodies_v2's own recomposition
            # check (against diff_sha256) already guarantees every line in
            # a real hunk body starts with one of the three markers above --
            # unreachable through that path, kept so a body constructed by
            # a future caller some other way fails loudly instead of
            # silently mis-tagging a line as contextless.
            raise ExtractionBlockedError(CONTENT_REASON_HUNK_RECOMPOSITION_FAILED_V2)
    return tuple(lines)


def slice_hunk_body_by_range_v2(
    body: HunkBodyV2, *, old_range: LineRangeV2, new_range: LineRangeV2
) -> str:
    """The one, lossless reconstruction rule described in the module
    docstring: every body line whose old or new line number falls inside
    the requested range, in original order, joined by ``\\n``. For a
    fragment whose range IS the hunk's own full range, this reproduces the
    hunk's entire body byte-for-byte (verified by the caller via
    ``compute_hunk_diff_sha256_v2``, not assumed here)."""

    selected = [
        line_text
        for line_text, old_line, new_line in _walk_hunk_body_lines_v2(body)
        if (old_line is not None and old_range.start <= old_line <= old_range.end)
        or (new_line is not None and new_range.start <= new_line <= new_range.end)
    ]
    return "\n".join(selected)


def _assign_hunk_line_ownership_v2(
    fragments_for_hunk: Sequence[FragmentV2], hunk_body: HunkBodyV2
) -> dict[int, str]:
    """Map body-line-index -> the ONE fragment_id that owns it, so a
    windowed hunk's real content is never sent twice.

    ``planner_v2._proportional_window`` documents its own exception: when
    one side of a hunk has fewer real lines than the window count (e.g. 1
    old line replaced by hundreds of new lines), the starved side's excess
    windows all collapse to the SAME single-point ``old_range``/
    ``new_range`` -- "a safe, valid placeholder, not a coverage
    requirement", per that module's own comment. ``slice_hunk_body_by_
    range_v2``'s pure range-membership rule does not know that: called
    independently per fragment, every window sharing that repeated anchor
    would select the SAME real line, sending one real line of code to the
    model multiple times under multiple fragment identities.

    This function is the fix, using only information ``planner_v2``/
    ``manifest_v2`` already produce -- no second parser, no second
    planner. Ownership is resolved deterministically: for each real body
    line, the CANDIDATE fragments are every fragment (for this hunk) whose
    range covers that line; the owner is the one with the smallest
    ``(old_range.start, new_range.start, fragment_id)`` tuple -- fully
    order-independent (does not rely on ``fragments_for_hunk``'s input
    order, on manifest fragment-list order, or on any planner-internal
    window-construction order the caller might not have preserved)."""

    lines = _walk_hunk_body_lines_v2(hunk_body)
    ordered_fragments = sorted(
        fragments_for_hunk,
        key=lambda fragment: (fragment.old_range.start, fragment.new_range.start, fragment.fragment_id),
    )
    owner_by_index: dict[int, str] = {}
    for index, (_line_text, old_line, new_line) in enumerate(lines):
        for fragment in ordered_fragments:
            covers_old = old_line is not None and fragment.old_range.start <= old_line <= fragment.old_range.end
            covers_new = new_line is not None and fragment.new_range.start <= new_line <= fragment.new_range.end
            if covers_old or covers_new:
                owner_by_index[index] = fragment.fragment_id
                break
    return owner_by_index


def slice_hunk_body_by_owned_lines_v2(body: HunkBodyV2, *, owned_line_indices: frozenset[int]) -> str:
    """Like ``slice_hunk_body_by_range_v2``, but selecting by explicit,
    pre-resolved line-index ownership (``_assign_hunk_line_ownership_v2``)
    instead of raw range membership -- the fix for the repeated-anchor
    duplication case. For a hunk with exactly one fragment, ownership
    trivially assigns every matching line to it, so this degenerates to
    the identical output ``slice_hunk_body_by_range_v2`` would have
    produced -- proven directly by this module's own test suite."""

    lines = _walk_hunk_body_lines_v2(body)
    selected = [line_text for index, (line_text, _, _) in enumerate(lines) if index in owned_line_indices]
    return "\n".join(selected)


def _classify_unrepresentable_v2(file_diff: ParsedFileDiffV2 | None) -> ReviewContentPolicyV2 | None:
    """Classify a fragment's OWN file as binary/submodule/generated/
    minified, or ``None`` if it is an ordinary text file eligible for
    ``INCLUDED``.

    ``is_binary``/``is_submodule`` are, as of this slice, DEFENSE IN DEPTH,
    not a reachable path through today's real pipeline: a binary file
    NEVER produces a ``ParsedHunkV2`` (git emits ``Binary files ... differ``
    or a ``GIT binary patch`` block, never a unified-diff hunk for it), so
    it never produces a ``FragmentV2``/``fragment_id`` at all --
    ``run_assembly_v2`` either silently excludes it (not ``must_review``)
    or blocks the WHOLE assembly before extraction is ever reached (IS
    ``must_review``; see its own "Paths that never produce a fragment at
    all" docstring section). This function is still exercised directly by
    ``test_review_content_extraction_v2.py`` against a hand-built
    ``FragmentV2``, matching this codebase's own established "kept in case
    a future change reopens it" precedent (``review_content_v2.
    bind_review_content_to_manifest_v2``'s coverage_required re-check is
    the same shape). ``generated``/``minified`` classification (by PATH
    pattern) has no such gap: a generated or minified TEXT file can and
    does have real hunks reaching this function through the real pipeline.
    """
    if file_diff is None:
        return ReviewContentPolicyV2.UNREPRESENTABLE
    if file_diff.is_binary:
        return ReviewContentPolicyV2.OMITTED_BINARY
    if file_diff.is_submodule:
        return ReviewContentPolicyV2.OMITTED_SUBMODULE
    path = file_diff.path
    if any(marker in path for marker in _GENERATED_PATH_MARKERS_V2):
        return ReviewContentPolicyV2.OMITTED_GENERATED
    if any(path.endswith(marker) for marker in _MINIFIED_PATH_MARKERS_V2):
        return ReviewContentPolicyV2.OMITTED_MINIFIED
    return None


class DlpEvaluationV2:
    """Result of ``_apply_dlp_v2``. ``blocked`` is ``True`` if the content
    must not transport; ``reason_code`` distinguishes an actual inline-rule
    match (``transport_blocked_by_dlp``, the pre-existing behavior) from a
    ``detector_name``-declared host-owned detector this engine cannot
    execute (``CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2``) -- the two
    are not interchangeable: one proves the content is unsafe, the other
    proves nothing at all about it."""

    __slots__ = ("blocked", "reason_code")

    def __init__(self, *, blocked: bool, reason_code: str | None) -> None:
        self.blocked = blocked
        self.reason_code = reason_code


def _apply_dlp_v2(text: str, *, dlp_policy: DlpPolicyDeclarationV2 | None) -> DlpEvaluationV2:
    """Declarative-rule DLP evaluation: ``pattern`` is data the host engine
    interprets as a regular expression, matched against the fragment's own
    (already generically-redacted) text -- never executed as code, never a
    path into the target repository (``DlpPolicyDeclarationV2`` is
    structurally incapable of naming one; see #200-A).

    A ``detector_name``-declared policy (host-owned digest-pinned
    detector) names a detector THIS engine does not execute -- #200-B/C
    implement no out-of-process detector runner. Per the #199 execution
    plan's own absolute rule ("suspeita inconclusiva não é 'limpa
    parcialmente e envia'"), a target that declared a detector expects its
    coverage; silently treating that coverage as "zero matches, therefore
    clean" would report a false negative as a pass. So `blocked=True` is
    returned UNCONDITIONALLY whenever `detector_name` is set -- regardless
    of whether inline `rules` also matched or not -- with a DISTINCT reason
    code from an actual rule match, so a caller can tell "this content is
    unsafe" apart from "this content was never actually checked"."""

    if dlp_policy is None:
        return DlpEvaluationV2(blocked=False, reason_code=None)
    if dlp_policy.detector_name is not None:
        return DlpEvaluationV2(blocked=True, reason_code=CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2)
    for rule in dlp_policy.rules:
        try:
            if re.search(rule.pattern, text):
                return DlpEvaluationV2(blocked=True, reason_code="transport_blocked_by_dlp")
        except re.error:
            # An unparseable declarative pattern is a policy authoring
            # error, not absence of risk -- fail closed rather than skip
            # the rule silently.
            return DlpEvaluationV2(blocked=True, reason_code="transport_blocked_by_dlp")
    return DlpEvaluationV2(blocked=False, reason_code=None)


def _build_fragment_content_v2(
    fragment: FragmentV2,
    *,
    file_diff: ParsedFileDiffV2 | None,
    hunk_body: HunkBodyV2 | None,
    dlp_policy: DlpPolicyDeclarationV2 | None,
    max_chars_per_chunk: int,
    owned_line_indices: frozenset[int] | None,
) -> FragmentContentV2:
    unrepresentable = _classify_unrepresentable_v2(file_diff)
    if unrepresentable is not None:
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                CONTENT_REASON_HUNK_BODY_UNAVAILABLE_V2, fragment_id=fragment.fragment_id
            )
        return FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=unrepresentable, coverage_required=False, content=None, content_sha256=None,
            redaction_applied=False, chars=0,
        )

    if hunk_body is None:
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                CONTENT_REASON_HUNK_BODY_UNAVAILABLE_V2, fragment_id=fragment.fragment_id
            )
        return FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.UNREPRESENTABLE, coverage_required=False, content=None,
            content_sha256=None, redaction_applied=False, chars=0,
        )

    # Exact recomposition check (D-series "hard stop", not a soft warning):
    # for a whole-hunk fragment (its range equals the hunk's own range,
    # EXACTLY -- not merely "starts at the hunk's start", which a windowed
    # fragment's first window also does) the slice must reproduce
    # diff_sha256 exactly, proving nothing was lost or corrupted between
    # acquisition and extraction. A windowed fragment's range is a strict
    # sub-range of the hunk's, so it is NOT expected to reproduce the
    # hunk-level hash -- only the whole-hunk case is checked here, and only
    # when it fails is this a stop condition, per the #199 execution plan:
    # "não normalize até passar".
    hunk_old_end = max(hunk_body.old_start + hunk_body.old_lines - 1, hunk_body.old_start)
    hunk_new_end = max(hunk_body.new_start + hunk_body.new_lines - 1, hunk_body.new_start)
    is_whole_hunk_fragment = (
        fragment.old_range.start == hunk_body.old_start
        and fragment.old_range.end == hunk_old_end
        and fragment.new_range.start == hunk_body.new_start
        and fragment.new_range.end == hunk_new_end
    )
    # Whole-hunk fragments (the overwhelmingly common case, and the ONLY
    # case that needs to reproduce diff_sha256 below) always use the plain
    # range slice -- there is exactly one fragment for that hunk, so
    # ownership is trivially "all of it" and computing it would be pure
    # overhead. Windowed multi-fragment hunks use the ownership-resolved
    # slice instead: the fix for the repeated-anchor duplication case (see
    # _assign_hunk_line_ownership_v2's own docstring).
    sliced = (
        slice_hunk_body_by_range_v2(hunk_body, old_range=fragment.old_range, new_range=fragment.new_range)
        if is_whole_hunk_fragment or owned_line_indices is None
        else slice_hunk_body_by_owned_lines_v2(hunk_body, owned_line_indices=owned_line_indices)
    )
    if is_whole_hunk_fragment:
        recomposed_sha256 = compute_hunk_diff_sha256_v2(
            sliced,
            old_no_newline_at_eof=hunk_body.old_no_newline_at_eof,
            new_no_newline_at_eof=hunk_body.new_no_newline_at_eof,
        )
        if recomposed_sha256 != hunk_body.diff_sha256 or recomposed_sha256 != fragment.diff_sha256:
            raise ExtractionBlockedError(
                CONTENT_REASON_HUNK_RECOMPOSITION_FAILED_V2, fragment_id=fragment.fragment_id
            )
    elif owned_line_indices is not None and not sliced:
        # Not reachable through today's real planner_v2 windowing (the
        # DRIVING side -- whichever of old/new is larger -- always
        # partitions into >=1 real, distinct line per window; only the
        # STARVED side ever repeats an anchor, and ownership only removes
        # duplicate copies of THAT side's line, never a window's entire
        # content). Kept as an explicit, honestly-labeled outcome rather
        # than silently falling into the redaction/budget branch below and
        # being reported as "blocked_by_redaction" for a fragment redaction
        # never touched -- mirrors this codebase's own "defense in depth,
        # kept in case a future change reopens it" precedent.
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                CONTENT_REASON_WINDOW_OWNS_NO_LINES_V2, fragment_id=fragment.fragment_id
            )
        return FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.UNREPRESENTABLE, coverage_required=False, content=None,
            content_sha256=None, redaction_applied=False, chars=0,
        )

    redaction_state = RedactionState()
    secret_redacted = redact_text(sliced, redaction_state)
    # sanitize_artifact_value's own two-stage discipline (redaction.py):
    # redact_value (secrets, tracked in `redaction_state`) THEN
    # _redact_local_paths (untracked -- that helper carries no counter of
    # its own). Applying both here, not just redact_text, matches review_
    # content_v2's own documented contract ("#200-B's extractor is
    # required to redact BEFORE calling this constructor") -- omitting the
    # local-path pass left FragmentContentV2's own last-line
    # sanitize_artifact_value guard as the only thing catching a leaked
    # local path, surfacing a raw, unhandled pydantic.ValidationError
    # instead of this module's own typed, redacted, fail-closed path.
    redacted = _redact_local_paths(secret_redacted)
    redaction_applied = redaction_state.secret_like_values_found > 0 or redacted != secret_redacted

    dlp_evaluation = _apply_dlp_v2(redacted, dlp_policy=dlp_policy)
    if dlp_evaluation.blocked:
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                dlp_evaluation.reason_code, fragment_id=fragment.fragment_id
            )
        return FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.BLOCKED_BY_TARGET_DLP, coverage_required=False,
            content=None, content_sha256=None, redaction_applied=redaction_applied, chars=0,
        )

    if not redacted or len(redacted) > max_chars_per_chunk:
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2, fragment_id=fragment.fragment_id
            )
        policy = (
            ReviewContentPolicyV2.OMITTED_OVER_BUDGET
            if redacted
            else ReviewContentPolicyV2.BLOCKED_BY_REDACTION
        )
        return FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=policy, coverage_required=False, content=None, content_sha256=None,
            redaction_applied=redaction_applied, chars=0,
        )

    return FragmentContentV2(
        fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
        policy=ReviewContentPolicyV2.INCLUDED, coverage_required=fragment.coverage_required,
        content=redacted, content_sha256=hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
        redaction_applied=redaction_applied, chars=len(redacted),
    )


def _enforce_chunk_budget_v2(
    fragment_contents: Sequence[FragmentContentV2], *, max_chars_per_chunk: int
) -> list[FragmentContentV2]:
    """Chunk-level budget enforcement -- the fix for the per-fragment-only
    check that let several individually-small fragments collectively
    exceed ``max_chars_per_chunk`` without ever being caught.

    Mirrors ``planner_v2``'s own documented doctrine exactly ("Auxiliary
    (non-``must_review``) hunks are context, not required coverage... are
    dropped first and silently... ``must_review`` fragments are never
    dropped -- either they all fit... or planning is refused"), applied
    here at the CONTENT layer instead of the line-planning layer:

    - if the chunk's total ``INCLUDED`` chars fit the budget, nothing
      changes;
    - if they do not, but every ``coverage_required`` fragment's chars
      alone (without ANY auxiliary content) fit the budget, the largest
      ``INCLUDED`` auxiliary fragments are degraded to
      ``OMITTED_OVER_BUDGET`` one at a time (largest first, ties broken by
      ``fragment_id`` for determinism) until the chunk fits -- auxiliary
      content is dropped BEFORE blocking, never the other way round;
    - only when the ``coverage_required`` fragments alone already exceed
      the budget -- so no amount of auxiliary-dropping can ever make the
      chunk fit -- does the WHOLE extraction block fail-closed
      (``CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2``); a
      ``must_review`` fragment is never itself dropped to make room.
    """

    included = [f for f in fragment_contents if f.policy is ReviewContentPolicyV2.INCLUDED]
    total_chars = sum(f.chars for f in included)
    if total_chars <= max_chars_per_chunk:
        return list(fragment_contents)

    required_chars = sum(f.chars for f in included if f.coverage_required)
    if required_chars > max_chars_per_chunk:
        raise ExtractionBlockedError(CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2, fragment_id=None)

    by_id = {f.fragment_id: f for f in fragment_contents}
    droppable_order = sorted(
        (f for f in included if not f.coverage_required),
        key=lambda f: (-f.chars, f.fragment_id),
    )
    for fragment in droppable_order:
        if total_chars <= max_chars_per_chunk:
            break
        total_chars -= fragment.chars
        by_id[fragment.fragment_id] = FragmentContentV2(
            fragment_id=fragment.fragment_id, path=fragment.path, diff_sha256=fragment.diff_sha256,
            policy=ReviewContentPolicyV2.OMITTED_OVER_BUDGET, coverage_required=False,
            content=None, content_sha256=None, redaction_applied=fragment.redaction_applied, chars=0,
        )
    return [by_id[f.fragment_id] for f in fragment_contents]


def extract_review_content_v2(
    *,
    repo_root,
    base_sha: str,
    head_sha: str,
    manifest: ManifestV2,
    payload_sha256_by_chunk_id: Mapping[str, str],
    target_profile: TargetProfileV2,
    dlp_policy: DlpPolicyDeclarationV2 | None = None,
) -> ReviewContentV2:
    """Extract real, redacted, DLP-checked content for every fragment
    ``manifest`` already planned, and bind the result back to that SAME
    manifest before returning it (fail-closed: ``bind_review_content_to_
    manifest_v2`` runs before this function returns, not left to the
    caller). Raises ``ExtractionBlockedError`` fail-closed -- never returns
    a partially-covered ``ReviewContentV2`` for a ``coverage_required``
    fragment.

    ``target_profile`` must be the SAME ``TargetProfileV2`` that produced
    ``manifest`` -- proven, not assumed: ``compute_profile_hash_v2(target_
    profile)`` is checked against ``manifest.identity.profile_hash`` before
    anything else runs, and a mismatch blocks the whole extraction fail-
    closed (``CONTENT_REASON_PROFILE_HASH_MISMATCH_V2``). A bare
    ``TargetBudgetsV2`` was accepted here in an earlier revision, but a
    caller could construct any ``TargetBudgetsV2`` value with looser limits
    than the profile that actually planned ``manifest`` -- ``RunIdentityV2.
    profile_hash`` already carries the real profile's hash for exactly this
    check, so accepting the full profile and re-deriving the hash closes
    that gap instead of trusting a caller-supplied budget object. Only once
    that check passes is ``target_profile.budgets.max_chars_per_chunk``
    read. Both per-fragment AND per-chunk (the sum of every ``INCLUDED``
    fragment in a chunk) are checked against this same value -- see
    ``_enforce_chunk_budget_v2``.

    ``payload_sha256_by_chunk_id`` must carry the REAL ``payload_sha256`` of
    each chunk's already-built ``ChunkPayloadV2`` (``payload_builder_v2.
    build_chunk_payload_v2``, built from this SAME manifest before this
    function is called) -- this module does not build payloads itself and
    never fabricates a placeholder hash. A missing entry for a chunk in
    ``manifest.chunks`` blocks the whole extraction fail-closed, mirroring
    ``bind_review_content_to_manifest_v2``'s own "content_chunk_set_
    mismatch" discipline: no chunk may reach ``ReviewContentV2`` with a
    hash that does not describe a real payload. Cross-checking that this
    hash actually matches the real payload object byte-for-byte is #200-C's
    job (see ``review_content_v2.bind_review_content_to_manifest_v2``'s own
    docstring for why that specific check is deferred there, not here).

    ``dlp_policy`` with a ``detector_name`` (host-owned external detector)
    is accepted, but this extractor does not execute that detector -- see
    ``_apply_dlp_v2``'s docstring. Every fragment such a policy applies to
    is therefore treated as UNPROVEN, not clean: it blocks fail-closed
    (``must_review``) or degrades to a typed ``BLOCKED_BY_TARGET_DLP``
    omission (optional), exactly like an actual inline-rule match. A
    target relying on a detector-only policy gets zero content reaching
    the Router until a real detector runner exists (out of scope for this
    slice).
    """

    if compute_profile_hash_v2(target_profile) != manifest.identity.profile_hash:
        raise ExtractionBlockedError(CONTENT_REASON_PROFILE_HASH_MISMATCH_V2, fragment_id=None)

    try:
        file_diffs = acquire_authoritative_diff_v2(repo_root, base_sha=base_sha, head_sha=head_sha)
        diff_text = acquire_diff_v2(repo_root, base_sha=base_sha, head_sha=head_sha)
        hunk_bodies = extract_hunk_bodies_v2(diff_text)
    except DiffAcquisitionError as exc:
        raise ExtractionBlockedError(
            CONTENT_REASON_DIFF_ACQUISITION_FAILED_V2, fragment_id=None
        ) from exc

    if not manifest.chunks:
        # A diff whose every file was excluded as non-must-review binary/
        # submodule/hunkless (validate_diff_completeness_v2's own
        # unrepresentable_paths, filtered out upstream by run_assembly_v2)
        # can legitimately produce an EMPTY manifest -- nothing to plan,
        # nothing to review. ReviewContentMaterialV2 requires at least one
        # chunk by construction (mirrors every other "at least one" v2
        # invariant), so this is surfaced as an explicit, typed refusal
        # here rather than left to leak out as a raw pydantic
        # ValidationError from deep inside model construction below.
        raise ExtractionBlockedError(CONTENT_REASON_NO_REVIEWABLE_CHUNKS_V2, fragment_id=None)

    max_chars_per_chunk = target_profile.budgets.max_chars_per_chunk
    file_diff_by_path = {file_diff.path: file_diff for file_diff in file_diffs}
    hunk_body_by_key = {(body.path, body.hunk_index): body for body in hunk_bodies}

    fragment_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}

    # Ownership is resolved ONCE, across every fragment in the manifest --
    # not per chunk -- because a hunk's windowed fragments can land in
    # DIFFERENT chunks after packing; ownership must be consistent
    # regardless of which chunk a given window ends up in. Only hunks with
    # more than one fragment need it at all (a lone fragment trivially
    # owns everything in its own range, exactly as before).
    fragments_by_hunk: dict[tuple[str, int], list[FragmentV2]] = {}
    for fragment in manifest.fragments:
        key = (fragment.path, fragment.hunk_indexes[0])
        fragments_by_hunk.setdefault(key, []).append(fragment)
    ownership_by_fragment_id: dict[str, frozenset[int]] = {}
    for hunk_key, hunk_fragments in fragments_by_hunk.items():
        if len(hunk_fragments) <= 1:
            continue
        hunk_body = hunk_body_by_key.get(hunk_key)
        if hunk_body is None:
            continue
        owner_by_index = _assign_hunk_line_ownership_v2(hunk_fragments, hunk_body)
        per_fragment_indices: dict[str, set[int]] = {f.fragment_id: set() for f in hunk_fragments}
        for index, owner_fragment_id in owner_by_index.items():
            per_fragment_indices[owner_fragment_id].add(index)
        for fragment_id, indices in per_fragment_indices.items():
            ownership_by_fragment_id[fragment_id] = frozenset(indices)

    chunks: list[ChunkContentV2] = []
    for manifest_chunk in manifest.chunks:
        if manifest_chunk.chunk_id not in payload_sha256_by_chunk_id:
            raise ExtractionBlockedError(
                "chunk_payload_sha256_unavailable", fragment_id=None
            )
        payload_sha256 = payload_sha256_by_chunk_id[manifest_chunk.chunk_id]
        fragment_contents = [
            _build_fragment_content_v2(
                fragment_by_id[fragment_id],
                file_diff=file_diff_by_path.get(fragment_by_id[fragment_id].path),
                hunk_body=hunk_body_by_key.get(
                    (fragment_by_id[fragment_id].path, fragment_by_id[fragment_id].hunk_indexes[0])
                ),
                dlp_policy=dlp_policy,
                max_chars_per_chunk=max_chars_per_chunk,
                owned_line_indices=ownership_by_fragment_id.get(fragment_id),
            )
            for fragment_id in manifest_chunk.fragment_ids
        ]
        fragment_contents = _enforce_chunk_budget_v2(
            fragment_contents, max_chars_per_chunk=max_chars_per_chunk
        )
        chunk_content_sha256 = compute_chunk_content_sha256_v2(
            ChunkContentV2.model_construct(
                chunk_id=manifest_chunk.chunk_id,
                payload_sha256=payload_sha256,
                fragments=fragment_contents,
                content_sha256="0" * 64,
            )
        )
        chunks.append(
            ChunkContentV2(
                chunk_id=manifest_chunk.chunk_id,
                payload_sha256=payload_sha256,
                fragments=fragment_contents,
                content_sha256=chunk_content_sha256,
            )
        )

    dlp_digest = compute_dlp_policy_digest_v2(dlp_policy) if dlp_policy is not None else None
    material = ReviewContentV2.model_construct(
            schema_id="agent-review.review-content.v2", schema_version=2,
            source="aiops-review-build-review-content", run_id=manifest.run_id,
            manifest_hash=manifest.identity.manifest_hash, dlp_policy_digest=dlp_digest,
        chunks=chunks, limitations=[], content_set_sha256="0" * 64,
    )
    content_set_sha256 = compute_review_content_sha256_v2(material)
    try:
        content = ReviewContentV2(
            schema_id="agent-review.review-content.v2", schema_version=2,
            source="aiops-review-build-review-content", run_id=manifest.run_id,
            manifest_hash=manifest.identity.manifest_hash, dlp_policy_digest=dlp_digest,
            chunks=chunks, limitations=[], content_set_sha256=content_set_sha256,
        )
    except ValidationError as exc:
        raise ExtractionBlockedError(
            CONTENT_REASON_CONTRACT_INVALID_V2, fragment_id=None
        ) from exc

    # `#200-D` predecessor (model B): `bind_review_content_to_manifest_v2`
    # raises `ReviewContentBindingError` -- a SIBLING family of this module's
    # own -- and `ReviewContentV2` construction above can fail its contract as
    # a pydantic error. Both used to escape this authority's declared surface,
    # so a caller had to know two foreign types to learn that extraction
    # refused. The binder's precise reason is preserved.
    try:
        bind_review_content_to_manifest_v2(content, manifest)
    except ReviewContentBindingError as exc:
        raise ExtractionBlockedError(exc.reason_code, fragment_id=None) from exc
    return content
