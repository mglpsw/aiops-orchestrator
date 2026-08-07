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
from typing import Mapping

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
from app.agent_review.redaction import redact_text
from app.agent_review.review_content_v2 import (
    ChunkContentV2,
    DlpPolicyDeclarationV2,
    FragmentContentV2,
    ReviewContentPolicyV2,
    ReviewContentV2,
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

_GENERATED_PATH_MARKERS_V2: tuple[str, ...] = (
    "/generated/",
    ".generated.",
    "-lock.",
    ".lock.json",
)
_MINIFIED_PATH_MARKERS_V2: tuple[str, ...] = (".min.js", ".min.css")


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


def _apply_dlp_v2(text: str, *, dlp_policy: DlpPolicyDeclarationV2 | None) -> bool:
    """Declarative-rule DLP evaluation: ``pattern`` is data the host engine
    interprets as a regular expression, matched against the fragment's own
    (already generically-redacted) text -- never executed as code, never a
    path into the target repository (``DlpPolicyDeclarationV2`` is
    structurally incapable of naming one; see #200-A). Returns ``True`` if
    any rule blocks. A ``detector_name``-only policy (host-owned digest-
    pinned detector, no inline rules) is out of scope for THIS engine call:
    it is a separate, out-of-process detector #200-B does not implement,
    and its absence here is not silently treated as "no DLP configured" --
    a caller supplying a detector-only policy gets zero rule coverage from
    this function and must be aware of that, documented in the public
    entry point below."""

    if dlp_policy is None:
        return False
    for rule in dlp_policy.rules:
        try:
            if re.search(rule.pattern, text):
                return True
        except re.error:
            # An unparseable declarative pattern is a policy authoring
            # error, not absence of risk -- fail closed rather than skip
            # the rule silently.
            return True
    return False


def _build_fragment_content_v2(
    fragment: FragmentV2,
    *,
    file_diff: ParsedFileDiffV2 | None,
    hunk_body: HunkBodyV2 | None,
    dlp_policy: DlpPolicyDeclarationV2 | None,
    max_chars_per_chunk: int,
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
    sliced = slice_hunk_body_by_range_v2(
        hunk_body, old_range=fragment.old_range, new_range=fragment.new_range
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

    redaction_state = RedactionState()
    redacted = redact_text(sliced, redaction_state)
    redaction_applied = redaction_state.secret_like_values_found > 0

    if _apply_dlp_v2(redacted, dlp_policy=dlp_policy):
        if fragment.coverage_required:
            raise ExtractionBlockedError(
                "transport_blocked_by_dlp", fragment_id=fragment.fragment_id
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


def extract_review_content_v2(
    *,
    repo_root,
    base_sha: str,
    head_sha: str,
    manifest: ManifestV2,
    payload_sha256_by_chunk_id: Mapping[str, str],
    dlp_policy: DlpPolicyDeclarationV2 | None = None,
    max_chars_per_chunk: int = 20_000,
) -> ReviewContentV2:
    """Extract real, redacted, DLP-checked content for every fragment
    ``manifest`` already planned, and bind the result back to that SAME
    manifest before returning it (fail-closed: ``bind_review_content_to_
    manifest_v2`` runs before this function returns, not left to the
    caller). Raises ``ExtractionBlockedError`` fail-closed -- never returns
    a partially-covered ``ReviewContentV2`` for a ``coverage_required``
    fragment.

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

    ``dlp_policy`` with a ``detector_name`` (host-owned external detector,
    no inline rules) is accepted but contributes NO rule coverage here --
    see ``_apply_dlp_v2``'s docstring. A target relying on that mode alone
    is not yet covered by this extractor.
    """

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

    file_diff_by_path = {file_diff.path: file_diff for file_diff in file_diffs}
    hunk_body_by_key = {(body.path, body.hunk_index): body for body in hunk_bodies}

    fragment_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
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
            )
            for fragment_id in manifest_chunk.fragment_ids
        ]
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
    content = ReviewContentV2(
        schema_id="agent-review.review-content.v2", schema_version=2,
        source="aiops-review-build-review-content", run_id=manifest.run_id,
        manifest_hash=manifest.identity.manifest_hash, dlp_policy_digest=dlp_digest,
        chunks=chunks, limitations=[], content_set_sha256=content_set_sha256,
    )

    bind_review_content_to_manifest_v2(content, manifest)
    return content
