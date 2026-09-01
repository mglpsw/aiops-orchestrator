"""Git unified-diff acquisition for AgentReview v2 (issue #84).

Parses the text of ``git diff --no-ext-diff --no-textconv --binary
BASE...HEAD`` into structured per-file, per-hunk records
(``ParsedFileDiffV2``/``ParsedHunkV2``)
consumable by ``planner_v2.HunkInputV2``. This module never executes
untrusted code: it is a pure text parser (``parse_unified_diff``), plus a
thin, fixed-argv subprocess wrapper (``acquire_diff_v2``) that only ever
runs the exact allowlisted ``git diff`` command with SHA-validated refs --
never a shell interpreter, never a caller-controlled command string.

It never retains or forwards raw diff content downstream: each hunk's body
is hashed into ``diff_sha256`` and discarded, matching the same "no raw
diff/payload in public artifacts" boundary the rest of AgentReview v2
already enforces (``contracts_v2``'s response/payload hashing follows the
identical pattern).

Renames, deletions, additions, binaries, submodules (gitlinks, mode
160000), a missing trailing newline, and a truncated hunk (a header
declaring more old/new lines than its body actually supplies) are all
recognized structurally. Completeness validation
(``validate_diff_completeness_v2``) reports, per expected path: missing
entirely, present but not representable as ordinary line-range hunks
(binary or submodule content, needing an explicit separate policy), or
present but truncated (needing reconstruction from blobs) -- so a caller
can route each case through the right remediation instead of silently
treating it as covered or silently dropping it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from app.agent_review.contracts_v2 import RelativePath
from app.agent_review.external_path_ingress_v2 import (
    ExternalPathIngressError,
    validate_external_input_directory_v2,
)

_RELATIVE_PATH_ADAPTER: TypeAdapter[str] = TypeAdapter(RelativePath)

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@"
)
_SIMILARITY_RE = re.compile(r"^similarity index (\d+)%$")
_SUBMODULE_MODE = "160000"

ChangeTypeV2 = Literal["added", "modified", "deleted", "renamed", "copied", "type_changed"]


class DiffAcquisitionError(ValueError):
    """Raised for a diff that cannot be safely parsed or acquired. Carries
    a stable ``reason_code`` only -- never raw diff content or a local
    path, consistent with the rest of AgentReview v2."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


DIFF_UNREADABLE_REASON_V2 = "diff_unreadable"
DIFF_TRUNCATED_REASON_V2 = "diff_truncated"
INVALID_REF_REASON_V2 = "invalid_git_ref"
# `#200-D` predecessor: acquisition shells out, so the OS can fail the call
# before git ever runs. A consumer cannot tell these apart -- both arrive as
# `FileNotFoundError` -- but this authority can, and therefore must.
GIT_UNAVAILABLE_REASON_V2 = "git_unavailable"
REPO_ROOT_UNUSABLE_REASON_V2 = "repo_root_unusable"
DIFF_ACQUISITION_IO_FAILED_REASON_V2 = "diff_acquisition_io_failed"
RAW_DIFF_CARDINALITY_MISMATCH_REASON_V2 = "raw_diff_cardinality_mismatch"
RAW_DIFF_STATUS_MISMATCH_REASON_V2 = "raw_diff_status_mismatch"
RAW_DIFF_PATH_MISMATCH_REASON_V2 = "raw_diff_path_mismatch"
RAW_DIFF_TYPE_CHANGE_UNSUPPORTED_REASON_V2 = "raw_diff_type_change_unsupported"


@dataclass(frozen=True)
class ParsedHunkV2:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    diff_sha256: str
    diff_chars: int


@dataclass(frozen=True)
class ParsedFileDiffV2:
    old_path: str | None
    new_path: str | None
    change_type: ChangeTypeV2
    is_binary: bool
    is_submodule: bool
    similarity_index: int | None
    old_no_newline_at_eof: bool
    new_no_newline_at_eof: bool
    hunks: tuple[ParsedHunkV2, ...]
    truncated: bool

    @property
    def path(self) -> str:
        """The canonical path this file diff is filed under: the new path
        for anything that still exists afterward, or the old path for a
        pure deletion."""

        return self.new_path or self.old_path or ""


def _decode_git_path(raw: str) -> str:
    """Decode a git diff path token, including the quoted-with-octal-escape
    form git uses for paths containing non-ASCII or special characters."""

    text = raw.strip()
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    inner = text[1:-1]
    decoded = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            decoded.extend(char.encode("utf-8"))
            index += 1
            continue
        if index + 1 >= len(inner):
            decoded.append(ord("\\"))
            break
        next_char = inner[index + 1]
        octal = inner[index + 1 : index + 4]
        if len(octal) == 3 and re.fullmatch(r"[0-7]{3}", octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        # The complete set git's own quote_c_style() escapes (quote.c:
        # `escaped = "abfnrtv\\\""`), not just the subset that happens to
        # be common in test fixtures. A path containing a literal
        # backspace, for example, is quoted as "\b" -- treating an
        # unrecognized escape char as ordinary text (dropping the
        # backslash, as a prior revision did) silently collides a path
        # like "x\b.py" (real backspace byte) with a distinct real file
        # literally named "xb.py".
        escape_map = {
            "a": "\a", "b": "\b", "f": "\f", "n": "\n",
            "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"',
        }
        mapped = escape_map.get(next_char)
        if mapped is not None:
            decoded.extend(mapped.encode("utf-8"))
            index += 2
            continue
        # Any other backslash escape is not one git's own quoting ever
        # produces (only these letters, "\\", '"', or a 3-digit octal
        # are valid) -- malformed or unrepresentable input, not a value
        # to guess at.
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
    # Strict decode, fail closed: arbitrary bytes are valid in a git
    # filename, so "replace" is wrong (it collapses distinct undecodable
    # byte sequences, e.g. octal \200 vs \201, onto the same U+FFFD
    # character, conflating two different paths under one string).
    # "surrogateescape" was tried next and is also wrong here: it does
    # preserve distinctness, but the resulting lone-surrogate string
    # cannot survive this module's own canonical-JSON hashing downstream
    # (manifest_v2._canonical_json_bytes_v2/compute_fragment_id_v2 use a
    # strict ``.encode("utf-8")`` on every fragment's path, by the same
    # "no raw/lossy content in a hash preimage" discipline every other
    # v2 hash in this codebase follows) -- it would trade a silent
    # collision for a crash several calls downstream. A path this module
    # cannot represent as a valid, hashable string is unreadable, not a
    # value to paper over.
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2) from exc


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _split_diff_git_header_paths(raw: str) -> tuple[str, str] | None:
    """Split the text after ``diff --git `` into exactly two path tokens,
    respecting a quoted path (git quotes a path containing non-ASCII or
    other special characters). This is the *only* source of paths for a
    ``GIT binary patch`` file block: with ``--binary`` (the canonical
    acquisition command), git emits neither ``--- ``/``+++ `` marker lines
    nor a ``Binary files ... differ`` line for such a block -- only this
    header. Returns ``None`` if the text does not split into exactly two
    tokens (an ambiguous case this parser does not attempt to guess)."""

    tokens: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index].isspace():
            index += 1
        if index >= length:
            break
        if raw[index] == '"':
            token_chars = ['"']
            index += 1
            while index < length:
                char = raw[index]
                token_chars.append(char)
                index += 1
                if char == "\\" and index < length:
                    token_chars.append(raw[index])
                    index += 1
                    continue
                if char == '"':
                    break
            tokens.append("".join(token_chars))
            continue
        start = index
        while index < length and not raw[index].isspace():
            index += 1
        tokens.append(raw[start:index])
    if len(tokens) == 2:
        return tokens[0], tokens[1]

    # A Codex review found a real gap here: git quotes a path only for
    # non-ASCII/special characters, NEVER merely for a plain space, so an
    # unquoted path such as "foo and bar.bin" still splits into more than
    # two whitespace tokens above and this returned None for a real
    # (non-adversarial) binary filename. Exploit this function's own
    # documented invariant to disambiguate: this fallback fires only when
    # finish() found no rename/copy header, so old_path and new_path are
    # ALWAYS identical here (a rename/copy would have already set them from
    # its own "rename from"/"rename to" header lines). raw is therefore
    # always exactly "a/<X> b/<X>" for some single X -- find the one
    # occurrence of " b/" whose two sides are equal.
    if raw.startswith("a/"):
        remainder = raw[len("a/"):]
        search_start = 0
        while True:
            split_at = remainder.find(" b/", search_start)
            if split_at == -1:
                break
            candidate = remainder[:split_at]
            after = remainder[split_at + len(" b/"):]
            if candidate and candidate == after:
                return f"a/{candidate}", f"b/{candidate}"
            search_start = split_at + 1

    return None


def _parse_marker_path(line: str, *, prefix: str) -> str | None:
    """Parse a ``--- <path>``/``+++ <path>`` marker line. Returns ``None``
    for ``/dev/null``."""

    raw = line[len(prefix):].strip()
    if raw == "/dev/null":
        return None
    # A trailing tab introduces a timestamp git sometimes appends; not part
    # of the path.
    raw = raw.split("\t", 1)[0]
    return _strip_ab_prefix(_decode_git_path(raw))


class _FileBlockBuilder:
    def __init__(self, header_line: str) -> None:
        self.header_line = header_line
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.rename_from: str | None = None
        self.rename_to: str | None = None
        self.copy_from: str | None = None
        self.copy_to: str | None = None
        self.is_new_file = False
        self.is_deleted_file = False
        self.is_binary = False
        self.similarity_index: int | None = None
        self.old_mode: str | None = None
        self.new_mode: str | None = None
        self.mode: str | None = None
        self.hunks: list[ParsedHunkV2] = []
        #: Parallel to ``self.hunks`` by index: the exact body text each
        #: hunk's ``diff_sha256`` was computed over (#200-B). Never copied
        #: into ``ParsedFileDiffV2``.
        self.hunk_bodies: list[str] = []
        self.old_no_newline_at_eof = False
        self.new_no_newline_at_eof = False
        self.truncated = False

    def consume_header(self, lines: list[str], start_index: int) -> int:
        """Consume the extended-header lines following ``diff --git`` (up
        to but not including the first ``@@`` hunk header or the next
        ``diff --git``). Returns the index of the first unconsumed line."""

        index = start_index
        current_old_start = current_new_start = current_old_lines = current_new_lines = None
        current_hunk_body: list[str] = []
        in_hunk = False

        def flush_hunk() -> None:
            nonlocal current_hunk_body, in_hunk
            if not in_hunk:
                return
            # A hunk header declares exactly how many old-side and new-side
            # lines follow (context lines count on both sides). If the
            # body actually collected fewer matching lines than declared
            # -- because the text ended, or the next file/hunk header
            # arrived early -- the patch was truncated, not merely short.
            # Silently trusting a truncated hunk's declared range would
            # let a coverage claim outlive the content that was supposed
            # to back it. More matching lines than declared is equally a
            # mismatch (a reconstructed/corrupted patch, e.g. a stale
            # header left over a body that grew): the declared range would
            # then silently exclude real content past it, so any
            # departure from the declared count -- not just a shortfall --
            # is flagged.
            actual_old_lines = sum(1 for body_line in current_hunk_body if body_line[:1] in (" ", "-"))
            actual_new_lines = sum(1 for body_line in current_hunk_body if body_line[:1] in (" ", "+"))
            if actual_old_lines != current_old_lines or actual_new_lines != current_new_lines:
                self.truncated = True
            # A real unified-diff hunk always represents at least one
            # actual change -- a hunk with zero +/- lines (e.g. a
            # malformed "@@ -0,0 +0,0 @@" with only context or nothing at
            # all) is not something git diff ever produces. Materializing
            # it anyway would give the file a nonempty hunks tuple with no
            # real changed content, which validate_diff_completeness_v2
            # would then wrongly treat as representable.
            if not any(body_line[:1] in ("+", "-") for body_line in current_hunk_body):
                raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
            body_text = "\n".join(current_hunk_body)
            # A "\ No newline at end of file" marker can only ever apply
            # to a file's final hunk (only the true last line of a file
            # can lack a trailing newline), and self.old/new_no_newline_at_eof
            # are still False at every earlier hunk's flush -- so their
            # value here always reflects this specific hunk's own EOF
            # state, never an earlier hunk's. Two otherwise-identical
            # patches differing only in which side lacks the final
            # newline emit the same body text; without folding this
            # state into the hash preimage they would collide on
            # diff_sha256/fragment_id despite genuinely different content.
            # compute_hunk_diff_sha256_v2 (defined later in this module) is
            # the ONE preimage definition; called here by name, not
            # inlined, so this builder and extract_hunk_bodies_v2's
            # re-verification can never drift apart into two hashes for
            # the same body.
            diff_sha256 = compute_hunk_diff_sha256_v2(
                body_text,
                old_no_newline_at_eof=self.old_no_newline_at_eof,
                new_no_newline_at_eof=self.new_no_newline_at_eof,
            )
            self.hunks.append(
                ParsedHunkV2(
                    old_start=current_old_start,
                    old_lines=current_old_lines,
                    new_start=current_new_start,
                    new_lines=current_new_lines,
                    diff_sha256=diff_sha256,
                    diff_chars=len(body_text),
                )
            )
            # #200-B needs the body text this hunk's diff_sha256 was
            # computed over. It is retained HERE -- on the builder, beside
            # the hash that was just derived from it -- and deliberately
            # NOT on ParsedHunkV2: the parsed contract stays raw-content-
            # free exactly as this module's header promises, and only
            # extract_hunk_bodies_v2 (which reuses this very builder, not a
            # second parser) can reach these strings. Re-deriving the body
            # in a separate pass would be a second engine whose preimage
            # could silently drift from this one.
            self.hunk_bodies.append(body_text)
            current_hunk_body = []
            in_hunk = False

        while index < len(lines):
            line = lines[index]
            if line.startswith("diff --git "):
                break
            if in_hunk:
                if line.startswith(("+", "-", " ")):
                    # A real unified-diff content line always carries one
                    # of these three prefixes -- even a blank context line
                    # is emitted as a single space character, never an
                    # empty string. An unprefixed blank here is either the
                    # (already-stripped) trailing split artifact or
                    # malformed/truncated input; either way it must not be
                    # counted as hunk content.
                    #
                    # Content cannot exist past end-of-file: once a side
                    # has recorded a no-newline-at-EOF marker (checked
                    # against self.old/new_no_newline_at_eof, set the
                    # moment a marker line is seen, before this point),
                    # any further line contributing to that side --
                    # including a context line, which requires *both*
                    # sides to still have real content -- is contradictory
                    # reconstructed/malformed input.
                    if line.startswith("-") and self.old_no_newline_at_eof:
                        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
                    if line.startswith("+") and self.new_no_newline_at_eof:
                        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
                    if line.startswith(" ") and (self.old_no_newline_at_eof or self.new_no_newline_at_eof):
                        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
                    current_hunk_body.append(line)
                    index += 1
                    continue
                if line == r"\ No newline at end of file":
                    # Applies to whichever side the immediately preceding
                    # body line belonged to -- a context line (" " prefix)
                    # is unchanged content present on *both* sides, so a
                    # missing trailing newline there means both sides lack
                    # it, not just the new side. A marker with no
                    # preceding body line at all (current_hunk_body empty)
                    # is not a real EOF annotation -- it cannot belong to
                    # any side -- but a symptom of malformed/reconstructed
                    # input (e.g. a marker immediately after a bare hunk
                    # header); guessing "both sides" for it would let a
                    # hunk with no actual changed-line content still
                    # produce a nonempty hunks tuple, which
                    # validate_diff_completeness_v2 would then treat as a
                    # representable path.
                    if not current_hunk_body:
                        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
                    last = current_hunk_body[-1]
                    if last.startswith("-"):
                        self.old_no_newline_at_eof = True
                    elif last.startswith("+"):
                        self.new_no_newline_at_eof = True
                    else:
                        self.old_no_newline_at_eof = True
                        self.new_no_newline_at_eof = True
                    index += 1
                    continue
                match = _HUNK_HEADER_RE.match(line)
                if match:
                    flush_hunk()
                    # fall through to header handling below
                else:
                    flush_hunk()
                    index += 1
                    continue

            match = _HUNK_HEADER_RE.match(line)
            if match:
                if self.old_no_newline_at_eof or self.new_no_newline_at_eof:
                    # A "\ No newline at end of file" marker can only
                    # apply to a file's true final hunk -- content cannot
                    # exist past end-of-file. A file that already recorded
                    # one and is now starting another hunk is contradictory
                    # reconstructed/malformed input, not a legitimate
                    # multi-hunk file; accepting it would let
                    # validate_diff_completeness_v2 approve coverage for
                    # a physically impossible file shape.
                    raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
                current_old_start = int(match.group("old_start"))
                current_old_lines = int(match.group("old_lines") or "1")
                current_new_start = int(match.group("new_start"))
                current_new_lines = int(match.group("new_lines") or "1")
                in_hunk = True
                current_hunk_body = []
                index += 1
                continue

            if line.startswith("--- "):
                self.old_path = _parse_marker_path(line, prefix="--- ")
                index += 1
                continue
            if line.startswith("+++ "):
                self.new_path = _parse_marker_path(line, prefix="+++ ")
                index += 1
                continue
            if line.startswith("rename from "):
                # Unlike diff --git/---/+++ marker paths, git never adds
                # an a/ or b/ prefix to rename/copy header paths -- they
                # are already repo-relative. Stripping here would corrupt
                # a real path under a top-level directory literally named
                # "a" or "b" (e.g. "rename to a/bar" -> wrongly "bar").
                self.rename_from = _decode_git_path(line[len("rename from "):])
                index += 1
                continue
            if line.startswith("rename to "):
                self.rename_to = _decode_git_path(line[len("rename to "):])
                index += 1
                continue
            if line.startswith("copy from "):
                self.copy_from = _decode_git_path(line[len("copy from "):])
                index += 1
                continue
            if line.startswith("copy to "):
                self.copy_to = _decode_git_path(line[len("copy to "):])
                index += 1
                continue
            if line.startswith("new file mode "):
                self.is_new_file = True
                self.new_mode = line[len("new file mode "):].strip()
                index += 1
                continue
            if line.startswith("deleted file mode "):
                self.is_deleted_file = True
                self.old_mode = line[len("deleted file mode "):].strip()
                index += 1
                continue
            if line.startswith("old mode "):
                self.old_mode = line[len("old mode "):].strip()
                index += 1
                continue
            if line.startswith("new mode "):
                self.new_mode = line[len("new mode "):].strip()
                index += 1
                continue
            similarity_match = _SIMILARITY_RE.match(line)
            if similarity_match:
                self.similarity_index = int(similarity_match.group(1))
                index += 1
                continue
            if line.startswith("index "):
                parts = line[len("index "):].split(" ", 1)
                if len(parts) == 2:
                    self.mode = parts[1].strip()
                index += 1
                continue
            if line.startswith("Binary files ") and line.endswith(" differ"):
                self.is_binary = True
                # Git omits "--- "/"+++ " marker lines for a binary diff,
                # so this line is the only place the paths appear.
                middle = line[len("Binary files "):-len(" differ")]
                sep = " and "
                if sep in middle:
                    left, right = middle.split(sep, 1)
                    if self.old_path is None and left.strip() != "/dev/null":
                        self.old_path = _strip_ab_prefix(_decode_git_path(left.strip()))
                    if self.new_path is None and right.strip() != "/dev/null":
                        self.new_path = _strip_ab_prefix(_decode_git_path(right.strip()))
                index += 1
                continue
            if line == "GIT binary patch":
                self.is_binary = True
                # Skip the binary patch body (base85-encoded lines) up to
                # the next file header or a blank separator followed by one.
                index += 1
                while index < len(lines) and not lines[index].startswith("diff --git "):
                    index += 1
                break
            # Any other extended-header line (e.g. a second "index" variant,
            # or a line this parser does not specifically recognize) is
            # skipped rather than treated as a parse failure -- unified
            # diff has a small number of optional header lines, and
            # skipping an unrecognized one is safer than aborting the
            # whole file's parse.
            index += 1

        flush_hunk()
        return index

    def finish(self) -> ParsedFileDiffV2:
        old_path = self.rename_from or self.copy_from or self.old_path
        new_path = self.rename_to or self.copy_to or self.new_path

        if old_path is None and new_path is None:
            # No "--- "/"+++ " markers and no "Binary files ... differ"
            # line matched -- the case for a GIT binary patch block under
            # `git diff --binary`, which emits neither. The "diff --git"
            # header line itself is the only remaining source.
            header_paths = _split_diff_git_header_paths(
                self.header_line[len("diff --git "):]
            )
            if header_paths is not None:
                old_path = _strip_ab_prefix(_decode_git_path(header_paths[0]))
                new_path = _strip_ab_prefix(_decode_git_path(header_paths[1]))
                # The diff --git header always carries both a/ and b/
                # tokens, even for an addition (no old blob) or a deletion
                # (no new blob) -- unlike the --- /+++ markers (which git
                # would otherwise print as /dev/null), this fallback has
                # no "absent side" marker of its own. Clear the side that
                # is_new_file/is_deleted_file (set from the file's own
                # "new file mode"/"deleted file mode" header line) says
                # cannot exist, so a consumer relying on old_path/new_path
                # for an explicit binary/blob-retrieval policy does not
                # try to fetch a blob from a revision where it never
                # existed.
                if self.is_new_file:
                    old_path = None
                elif self.is_deleted_file:
                    new_path = None

        if self.rename_from and self.rename_to:
            change_type: ChangeTypeV2 = "renamed"
        elif self.copy_from and self.copy_to:
            change_type = "copied"
        elif self.is_new_file or (old_path is None and new_path is not None):
            change_type = "added"
        elif self.is_deleted_file or (new_path is None and old_path is not None):
            change_type = "deleted"
        elif self.old_mode is not None and self.new_mode is not None and not self.hunks and not self.is_binary:
            change_type = "type_changed"
        else:
            change_type = "modified"

        is_submodule = self.mode == _SUBMODULE_MODE or self.old_mode == _SUBMODULE_MODE or self.new_mode == _SUBMODULE_MODE

        return ParsedFileDiffV2(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            is_binary=self.is_binary,
            is_submodule=is_submodule,
            similarity_index=self.similarity_index,
            old_no_newline_at_eof=self.old_no_newline_at_eof,
            new_no_newline_at_eof=self.new_no_newline_at_eof,
            hunks=tuple(self.hunks),
            truncated=self.truncated,
        )


def parse_unified_diff(diff_text: str) -> tuple[ParsedFileDiffV2, ...]:
    """Parse the full text of ``git diff --no-ext-diff --no-textconv
    --binary BASE...HEAD`` into structured per-file, per-hunk records.

    Never raises on content it does not specifically recognize inside a
    file block (unrecognized extended-header lines are skipped, matching
    unified diff's small set of optional headers); a structurally
    unreadable overall document (no ``diff --git`` markers found at all in
    non-empty input) raises ``DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)``.
    """

    if diff_text.strip() == "":
        return ()

    # This is a public boundary: parse_unified_diff is a pure text
    # function callable directly by any adapter (e.g. a blob/API
    # reconstruction path), not only via acquire_diff_v2 (whose own
    # subprocess output is already strictly decoded). A caller-supplied
    # str can still contain a lone surrogate (e.g. built elsewhere via
    # errors="surrogateescape"); every hunk-body hash downstream encodes
    # with errors="replace", which maps distinct lone surrogates (e.g.
    # \ud800 vs \ud801) onto the same "?" byte, corrupting diff_sha256/
    # fragment-identity uniqueness. Reject non-UTF-8-encodable text here,
    # at the top, before any of that hashing happens.
    try:
        diff_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2) from exc

    lines = diff_text.split("\n")
    if lines and lines[-1] == "" and diff_text.endswith("\n"):
        # ``str.split("\n")`` on text ending in a newline yields a
        # synthetic trailing empty element that is not a real line. Left
        # in place, it satisfies the hunk body's blank-context-line check
        # (``body_line == ""``) and inflates both the old- and new-side
        # actual line counts, masking a genuinely truncated hunk whose
        # last real line was cut off right at that trailing newline.
        lines = lines[:-1]
    if not any(line.startswith("diff --git ") for line in lines):
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)

    file_diffs: list[ParsedFileDiffV2] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("diff --git "):
            index += 1
            continue
        builder = _FileBlockBuilder(line)
        index = builder.consume_header(lines, index + 1)
        file_diffs.append(builder.finish())

    return tuple(file_diffs)


HUNK_BODY_DIGEST_MISMATCH_REASON_V2 = "hunk_body_digest_mismatch"


def compute_hunk_diff_sha256_v2(
    body_text: str, *, old_no_newline_at_eof: bool, new_no_newline_at_eof: bool
) -> str:
    """The ONE definition of a hunk's ``diff_sha256`` preimage, extracted so
    ``_FileBlockBuilder`` and any consumer that wants to re-derive the hash
    from a body it holds share the same bytes by construction rather than by
    two implementations agreeing today and drifting tomorrow."""

    hasher = hashlib.sha256(body_text.encode("utf-8", errors="replace"))
    hasher.update(f"\x00old_no_newline_at_eof={old_no_newline_at_eof}".encode())
    hasher.update(f"\x00new_no_newline_at_eof={new_no_newline_at_eof}".encode())
    return hasher.hexdigest()


@dataclass(frozen=True)
class HunkBodyV2:
    """One hunk's real body text, keyed by the identity #200-B binds against.

    This is the ONLY structure in this module that carries raw diff content.
    It is never embedded in ``ParsedFileDiffV2``/``ParsedHunkV2`` and never
    reaches an artifact: ``review_content_extraction_v2`` redacts before the
    text is allowed into any contract object. ``old_start``/``new_start`` are
    carried alongside the body (not just the hash/char-count ``ParsedHunkV2``
    keeps) because #200-B's per-fragment slicing needs the absolute line
    numbers each body line represents, not merely proof of which bytes the
    hunk's own ``diff_sha256`` covers."""

    path: str
    hunk_index: int
    diff_sha256: str
    body_text: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    old_no_newline_at_eof: bool
    new_no_newline_at_eof: bool


def extract_hunk_bodies_v2(diff_text: str) -> tuple[HunkBodyV2, ...]:
    """Re-parse ``diff_text`` with the SAME ``_FileBlockBuilder`` that
    ``parse_unified_diff`` uses, returning each hunk's retained body text.

    Not a second parser: the hunks, their ranges, their ``diff_sha256`` and
    the bodies below all come from one pass of one implementation. Every
    returned body is additionally re-hashed through
    ``compute_hunk_diff_sha256_v2`` and checked against the hash the builder
    derived; a mismatch is impossible through this path today and raises
    ``DiffAcquisitionError(HUNK_BODY_DIGEST_MISMATCH_REASON_V2)`` rather than
    handing a caller content whose identity is not provably the parsed one.
    """

    if diff_text.strip() == "":
        return ()

    lines = diff_text.split("\n")
    if lines and lines[-1] == "" and diff_text.endswith("\n"):
        lines = lines[:-1]
    if not any(line.startswith("diff --git ") for line in lines):
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)

    bodies: list[HunkBodyV2] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("diff --git "):
            index += 1
            continue
        builder = _FileBlockBuilder(line)
        index = builder.consume_header(lines, index + 1)
        file_diff = builder.finish()
        if len(builder.hunk_bodies) != len(file_diff.hunks):
            raise DiffAcquisitionError(HUNK_BODY_DIGEST_MISMATCH_REASON_V2)
        for hunk_index, (hunk, body_text) in enumerate(zip(file_diff.hunks, builder.hunk_bodies)):
            recomputed = compute_hunk_diff_sha256_v2(
                body_text,
                old_no_newline_at_eof=file_diff.old_no_newline_at_eof,
                new_no_newline_at_eof=file_diff.new_no_newline_at_eof,
            )
            if recomputed != hunk.diff_sha256:
                raise DiffAcquisitionError(HUNK_BODY_DIGEST_MISMATCH_REASON_V2)
            bodies.append(
                HunkBodyV2(
                    path=file_diff.path,
                    hunk_index=hunk_index,
                    diff_sha256=hunk.diff_sha256,
                    body_text=body_text,
                    old_start=hunk.old_start,
                    old_lines=hunk.old_lines,
                    new_start=hunk.new_start,
                    new_lines=hunk.new_lines,
                    old_no_newline_at_eof=file_diff.old_no_newline_at_eof,
                    new_no_newline_at_eof=file_diff.new_no_newline_at_eof,
                )
            )

    return tuple(bodies)


_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Explicit, fixed rename/copy detection thresholds shared verbatim by
#: ``acquire_diff_v2`` and ``acquire_raw_diff_v2`` (issue #103). Nothing is
#: inherited from ``diff.renames``/``diff.copies`` config: if the two
#: acquisitions used different (or ambient, config-dependent) thresholds,
#: one could detect a rename/copy the other misses, and
#: ``correlate_raw_and_unified_v2`` would then reject a perfectly legitimate
#: diff as a status/path mismatch. ``--find-copies`` (not
#: ``--find-copies-harder``) only searches modified files in the same diff
#: for a copy source -- deliberately bounded, not an unbounded whole-tree
#: scan, matching this module's "no unbounded internal work" discipline.
#:
#: ``-l1000`` (git's own long-standing default rename-limit value, made
#: explicit here) closes a real gap a Codex review found: ``--find-renames``/
#: ``--find-copies`` alone do not bound the number of rename/copy candidate
#: paths git will attempt to pair up (``git diff -h`` documents ``-l<n>`` as
#: "limit rename attempts up to <n> paths") -- an ambient
#: ``diff.renameLimit`` config value, or a large diff exceeding git's
#: built-in default, could make the two acquisitions degrade differently
#: (one giving up on rename detection before the other), producing
#: different file counts, change types, and old/new path linkage that
#: ``correlate_raw_and_unified_v2`` cannot detect as a mismatch, since both
#: sides "agree" on their own degraded shape. Fixing this value here means
#: authoritative identity never depends on ambient runner configuration.
_RENAME_COPY_DETECTION_ARGS_V2: tuple[str, ...] = ("--find-renames=50%", "--find-copies=50%", "-l1000")



def _run_git_v2(argv: list[str], *, repo_root: Path) -> subprocess.CompletedProcess:
    """Run git for acquisition, converting OS-level failures to this
    authority's own family.

    `#200-D` predecessor (model B): a caller may know THAT acquisition failed;
    it must not have to interpret `FileNotFoundError` to find out why. The
    checkout is probed first precisely so "no such checkout" and "no git on
    PATH" -- which are the SAME `FileNotFoundError` from ``subprocess`` -- stay
    distinguishable here, where the distinction is knowable.

    Only ``OSError`` is converted. A defect in this module still raises: a bug
    must never be laundered into an acquisition refusal.
    """

    # G4B: `repo_root` is the true ingress boundary for this call -- it is
    # the caller-selected checkout root, not an engine-derived path. Any
    # failure shape the centralized authority can observe (missing, wrong
    # type, symlink loop, overlong path, permission denied) collapses to
    # the SAME pre-existing `REPO_ROOT_UNUSABLE_REASON_V2`: this call site
    # never distinguished those cases before, and inventing new public
    # reason codes here is not this predecessor's job.
    try:
        validated_repo_root = validate_external_input_directory_v2(repo_root).resolved_path
    except ExternalPathIngressError as exc:
        raise DiffAcquisitionError(REPO_ROOT_UNUSABLE_REASON_V2) from exc
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell, SHA-validated refs
            argv, cwd=validated_repo_root, capture_output=True, text=False, check=False
        )
    except FileNotFoundError as exc:
        # Ask which file was missing rather than infer it. The earlier version
        # reasoned "the checkout existed a moment ago, so it must be the
        # executable" -- but the probe and the exec are not atomic, so a
        # checkout that vanished in between was reported as `git_unavailable`:
        # exactly the confusion this authority exists to eliminate.
        if shutil.which(argv[0]) is None:
            raise DiffAcquisitionError(GIT_UNAVAILABLE_REASON_V2) from exc
        raise DiffAcquisitionError(REPO_ROOT_UNUSABLE_REASON_V2) from exc
    except OSError as exc:
        # permissions, ENOTDIR after a race, fork/exec exhaustion, ...
        raise DiffAcquisitionError(DIFF_ACQUISITION_IO_FAILED_REASON_V2) from exc


def acquire_diff_v2(repo_root: Path, *, base_sha: str, head_sha: str) -> str:
    """Run the canonical, fixed, allowlisted diff command:
    ``git diff --no-ext-diff --no-textconv --binary --find-renames=50%
    --find-copies=50% <base_sha>...<head_sha>`` in ``repo_root``, and
    return its raw text.

    ``--no-textconv`` matters as much as ``--no-ext-diff``: the two are
    separate git mechanisms. ``--no-ext-diff`` only disables an external
    *diff driver* (``diff.<driver>.command``); it does **not** disable a
    driver's *textconv* filter (``diff.<driver>.textconv``), which git
    still runs to turn a path into a text representation before diffing
    it, whenever a tracked ``.gitattributes`` in the checkout assigns that
    driver to the path and the local git config defines its textconv
    command. Without ``--no-textconv`` this module could execute
    arbitrary repository-configured tooling while merely acquiring a
    patch -- a direct violation of the "never executes untrusted code"
    boundary this module documents for itself.

    Never invokes a shell interpreter (the command is a fixed argv list). ``base_sha``/``head_sha`` must each be a full
    lowercase 40-character commit SHA -- never a branch, tag, or
    caller-supplied ref string -- rejected with
    ``DiffAcquisitionError(INVALID_REF_REASON_V2)`` otherwise, so a
    malicious or malformed ref can never be interpreted as a shell
    argument or an unexpected git revision expression (e.g. one starting
    with ``-``).
    """

    if not _GIT_SHA_RE.match(base_sha) or not _GIT_SHA_RE.match(head_sha):
        raise DiffAcquisitionError(INVALID_REF_REASON_V2)

    # capture_output as bytes (not text=True): text mode decodes under the
    # process locale encoding and raises a raw UnicodeDecodeError on any
    # byte sequence it can't decode, bypassing this module's stable
    # DiffAcquisitionError reason-code contract entirely. We decode
    # ourselves, strictly -- errors="replace" was tried and rejected: it
    # maps distinct undecodable byte sequences (e.g. 0x80 vs 0x81) to the
    # same replacement character before the hunk body is hashed, so
    # genuinely different content could collide on the same diff_sha256/
    # fragment_id. Undecodable output fails closed instead.
    result = _run_git_v2(
        [
            "git", "diff", "--no-ext-diff", "--no-textconv", "--binary",
            # A Codex review found that an ambient diff.noprefix=true
            # config (a real, documented git setting) makes git emit
            # "diff --git my file.bin my file.bin" instead of the
            # canonical "a/<path> b/<path>" form -- _split_diff_git_
            # header_paths' fallback only recognizes headers starting
            # with "a/", so a prefixless binary path with a space would
            # still resolve to nothing. Forcing the standard prefixes
            # explicitly, like -l1000 above forces the rename limit,
            # means acquisition output never depends on ambient runner
            # configuration.
            "--src-prefix=a/", "--dst-prefix=b/",
            *_RENAME_COPY_DETECTION_ARGS_V2, f"{base_sha}...{head_sha}",
        ],
        repo_root=repo_root,
    )
    if result.returncode != 0:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2) from exc


@dataclass(frozen=True)
class DiffCompletenessResultV2:
    complete: bool
    missing_paths: tuple[str, ...]
    unrepresentable_paths: tuple[str, ...]
    """Paths present in the diff but not representable as ordinary
    line-range hunks -- binary content, submodule/gitlink changes, a
    hunkless metadata-only change (pure rename/copy, mode change, or an
    empty file add/delete with no hunk headers at all), or a path that
    fails ``contracts_v2.RelativePath`` -- the same frozen contract
    ``manifest_v2.FragmentV2.path`` uses (validated here against that
    actual authoritative type, not a re-derived subset of its rules). A
    hunkless entry can never produce a ``HunkInputV2``/fragment for the
    line-range planner; a path violating ``RelativePath`` (e.g. a glob
    metacharacter like the common Next.js route ``app/[id]/page.tsx``, or
    a decoded control character from an escape sequence) is a perfectly
    valid, ordinary git path but would be rejected by ``FragmentV2``
    itself. Letting any of these reach the planner uncaught would raise
    an uncontrolled ``pydantic.ValidationError`` well past acquisition
    instead of a controlled, explicit policy decision; these need that
    explicit, separate policy decision, never silent coverage or an
    uncontrolled crash."""
    truncated_paths: tuple[str, ...] = ()
    """Paths whose patch content itself is incomplete: a hunk header
    declared more old/new lines than the body actually contains. Distinct
    from ``unrepresentable_paths`` -- the remediation here is
    reconstructing the diff from blobs (per issue #84: "Consumidores que
    só possuam API devem reconstruir o diff por arquivo/blobs quando
    patches estiverem ausentes ou truncados"), not an explicit binary/
    submodule policy."""


def validate_diff_completeness_v2(
    file_diffs: tuple[ParsedFileDiffV2, ...],
    *,
    expected_paths: frozenset[str],
) -> DiffCompletenessResultV2:
    """Check that every expected path is present in the parsed diff, and
    flag any present file that cannot be covered by ordinary line-range
    hunks (binary content, submodule/gitlink changes). Per issue #84:
    "Sem prova de completude: coverage_failure/blocked_pipeline" -- this
    function only reports the facts; the caller decides the resulting
    readiness/coverage state."""

    present_paths = {file_diff.path for file_diff in file_diffs if file_diff.path}
    missing = frozenset(expected_paths) - present_paths

    def _violates_relative_path_contract(path: str) -> bool:
        # Validate against the actual authoritative contract
        # (contracts_v2.RelativePath, the same type FragmentV2.path uses)
        # rather than re-deriving a subset of its rules here -- a glob
        # metacharacter is only one of several ways a perfectly valid git
        # path (e.g. containing a decoded control character from an
        # escape sequence) can still fail RelativePath's stricter
        # contract and would otherwise reach the planner as an
        # uncontrolled pydantic.ValidationError.
        try:
            _RELATIVE_PATH_ADAPTER.validate_python(path)
        except ValidationError:
            return True
        return False

    unrepresentable = tuple(
        sorted(
            file_diff.path
            for file_diff in file_diffs
            if file_diff.path in expected_paths
            and (
                file_diff.is_binary
                or file_diff.is_submodule
                or not file_diff.hunks
                or _violates_relative_path_contract(file_diff.path)
            )
        )
    )
    truncated = tuple(
        sorted(
            file_diff.path
            for file_diff in file_diffs
            if file_diff.path in expected_paths and file_diff.truncated
        )
    )
    return DiffCompletenessResultV2(
        complete=not missing and not unrepresentable and not truncated,
        missing_paths=tuple(sorted(missing)),
        unrepresentable_paths=unrepresentable,
        truncated_paths=truncated,
    )


# =============================================================================
# Issue #103 -- deterministic path identity via a raw NUL-separated stream.
#
# The unified diff parsed above must still guess at a path whenever a
# "GIT binary patch" block's header cannot be split into exactly two
# whitespace tokens (a name containing a plain space), or whenever a
# "Binary files ... differ" marker line contains the literal separator
# " and " as part of a real filename -- both silent, ambiguous cases (see
# ``_split_diff_git_header_paths`` and the "Binary files " branch of
# ``_FileBlockBuilder.consume_header`` above, neither of which is modified
# by this section: the fix is a new, independent authority, not a smarter
# guess).
#
# ``git diff --raw -z`` never quotes or escapes a path -- unlike the
# unified diff's header/marker lines, which ``_decode_git_path`` must
# reconstruct from quoted, octal-escaped text. Parsed into logical records
# and correlated position-by-position against the unified diff's own
# blocks (produced by the SAME range with the SAME explicit rename/copy
# thresholds), it becomes a ground truth that can prove -- or disprove --
# every path and change type the unified parser derived. Divergence of
# any kind fails closed; nothing is guessed.
# =============================================================================

_RAW_RECORD_HEADER_RE = re.compile(
    r"^:(?P<old_mode>[0-7]{6}) (?P<new_mode>[0-7]{6}) "
    r"(?P<old_sha>[0-9a-f]+) (?P<new_sha>[0-9a-f]+) "
    r"(?P<status>[ACDMRTUX])(?P<score>\d*)$"
)

# Statuses git's raw format ever emits carrying exactly one path. "U"
# (unmerged) and "X" ("unknown" -- per git's own documentation, a status
# that should never actually appear in output) are deliberately excluded:
# neither can occur in an ordinary <base>...<head> range diff, and forcing
# a decision for them here (rather than a fabricated single/two-path
# guess) keeps the parser itself a faithful, non-opinionated record of
# whatever git actually emitted.
_RAW_SINGLE_PATH_STATUSES = frozenset({"A", "D", "M", "T"})
_RAW_TWO_PATH_STATUSES = frozenset({"R", "C"})

# Change types the *unified* parser (see ``ParsedFileDiffV2.change_type``
# above) may legitimately report for a given raw status letter. A raw "M"
# maps to *both* "modified" (ordinary content change) and "type_changed"
# (a pure, hunkless mode/attribute change, e.g. chmod) -- verified
# empirically: git's raw diff reports a plain chmod as "M" with identical
# old/new blob SHAs, and the existing unified parser's own "type_changed"
# label is reachable *only* for that hunkless, mode-only shape (its
# ``finish()`` elif chain checks ``is_new_file``/``is_deleted_file``
# first, so a genuine raw "T" object-type change -- see below -- never
# reaches that branch at all). Raw "T" is deliberately absent from this
# map: it is handled, and rejected, before this map is ever consulted.
_RAW_STATUS_TO_CHANGE_TYPES_V2: dict[str, frozenset[ChangeTypeV2]] = {
    "A": frozenset({"added"}),
    "D": frozenset({"deleted"}),
    "M": frozenset({"modified", "type_changed"}),
    "R": frozenset({"renamed"}),
    "C": frozenset({"copied"}),
}


@dataclass(frozen=True)
class RawDiffRecordV2:
    """One logical record from ``git diff --raw -z``: a single-path
    change (``old_path``/``new_path`` set from the *same* literal path,
    per that status's side -- ``None`` for the side that does not exist),
    or a rename/copy carrying its own distinct ``old_path``/``new_path``.
    Paths here are never quoted or escaped by git, so -- unlike
    ``ParsedFileDiffV2`` -- there is no ambiguity left for this module to
    resolve; it is the ground truth ``correlate_raw_and_unified_v2``
    checks the unified diff's own paths against."""

    status: str
    similarity_index: int | None
    old_path: str | None
    new_path: str | None


def parse_raw_diff_z(raw_text: str) -> tuple[RawDiffRecordV2, ...]:
    """Parse the full text of ``git diff --raw -z`` into logical records.

    Parsing proceeds by RECORD, never by raw NUL-token count: a rename or
    copy record consumes two path tokens for one logical change, so
    comparing token counts between this stream and the unified diff's
    block count would be a category error (issue #103 is explicit about
    this). ``correlate_raw_and_unified_v2`` below compares *record* count
    to *block* count instead.

    Any structurally unreadable input -- an unparseable metadata line, or
    a record whose declared status is missing its expected path token(s)
    -- raises ``DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)``, the
    same reason code this module already uses for undecodable output;
    this is not a new class of failure, just a new source of it.
    """

    if raw_text == "":
        return ()

    tokens = raw_text.split("\x00")
    # Every record's final path is itself NUL-terminated, including the
    # very last record in the stream -- splitting on "\x00" therefore
    # yields one synthetic trailing empty token that is not a real path,
    # exactly mirroring parse_unified_diff's own trailing-newline handling.
    if tokens and tokens[-1] == "":
        tokens = tokens[:-1]

    records: list[RawDiffRecordV2] = []
    index = 0
    while index < len(tokens):
        header = tokens[index]
        match = _RAW_RECORD_HEADER_RE.match(header)
        if match is None:
            raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
        status = match.group("status")
        score_text = match.group("score")
        similarity_index = int(score_text) if score_text else None
        index += 1

        if status in _RAW_TWO_PATH_STATUSES:
            if index + 1 >= len(tokens):
                raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
            old_path_token = tokens[index]
            new_path_token = tokens[index + 1]
            # A rename/copy record missing one or both of its two path
            # tokens (e.g. truncated input, or a record genuinely emitted
            # without them) must not silently consume the NEXT record's
            # header as if it were a path -- issue #113. Neither consumed
            # token may itself look like a record header; if it does, this
            # record is missing a real path and the stream is unreadable,
            # not merely "the next record happens to start here".
            if _RAW_RECORD_HEADER_RE.match(old_path_token) or _RAW_RECORD_HEADER_RE.match(new_path_token):
                raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
            index += 2
            records.append(
                RawDiffRecordV2(
                    status=status,
                    similarity_index=similarity_index,
                    old_path=old_path_token,
                    new_path=new_path_token,
                )
            )
        elif status in _RAW_SINGLE_PATH_STATUSES:
            if index >= len(tokens):
                raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
            path_token = tokens[index]
            # Same defect class as the two-path branch above (issue #113):
            # a record missing its own single path token must not silently
            # consume the NEXT record's header as if it were that path. A
            # single missing token only exhausts the stream when exactly
            # one more record follows; a chain of 2+ further records
            # instead re-synchronizes on a later header, producing a
            # wrong-but-plausible-looking record AND silently dropping the
            # intervening one(s) -- found by independent review of this
            # same PR, reproduced directly against the unpatched branch.
            if _RAW_RECORD_HEADER_RE.match(path_token):
                raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
            index += 1
            if status == "A":
                old_path, new_path = None, path_token
            elif status == "D":
                old_path, new_path = path_token, None
            else:  # "M" or "T": path unchanged, present on both sides
                old_path, new_path = path_token, path_token
            records.append(
                RawDiffRecordV2(
                    status=status,
                    similarity_index=similarity_index,
                    old_path=old_path,
                    new_path=new_path,
                )
            )
        else:
            # Unreachable given the header regex's status character
            # class, kept as an explicit fail-closed branch rather than
            # an assertion: a future, wider status charset must not fall
            # through silently.
            raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)

    return tuple(records)


def correlate_raw_and_unified_v2(
    raw_records: tuple[RawDiffRecordV2, ...],
    file_diffs: tuple[ParsedFileDiffV2, ...],
) -> None:
    """Prove the unified diff's own parsed paths and change types agree
    with the raw stream's ground truth. Raises ``DiffAcquisitionError``
    fail-closed on any divergence; returns ``None`` on success (the
    unified ``file_diffs`` were already correct -- this only certifies
    that fact, it does not construct a new value).

    A raw "T" record (a genuine object-type change, e.g. regular file to
    symlink or submodule) is rejected unconditionally, before the
    cardinality check: verified empirically, git's UNIFIED diff renders
    this as a delete+add PAIR (two blocks) for one raw record, a
    1-to-2 shape this module does not attempt to correlate. Checking
    cardinality first would misreport this known, deliberate boundary as
    a generic mismatch instead of its own stable reason code.

    Position ``i`` in ``raw_records`` must correspond to block ``i`` in
    ``file_diffs`` -- there is no reordering or matching by set
    membership. A swapped order therefore surfaces as a status or path
    mismatch at the affected positions, which is the intended, sufficient
    detection: no separate "order" reason code is needed on top of that.
    """

    for record in raw_records:
        if record.status == "T":
            raise DiffAcquisitionError(RAW_DIFF_TYPE_CHANGE_UNSUPPORTED_REASON_V2)

    if len(raw_records) != len(file_diffs):
        raise DiffAcquisitionError(RAW_DIFF_CARDINALITY_MISMATCH_REASON_V2)

    for record, file_diff in zip(raw_records, file_diffs):
        allowed_change_types = _RAW_STATUS_TO_CHANGE_TYPES_V2.get(record.status)
        if allowed_change_types is None or file_diff.change_type not in allowed_change_types:
            raise DiffAcquisitionError(RAW_DIFF_STATUS_MISMATCH_REASON_V2)
        if record.old_path != file_diff.old_path or record.new_path != file_diff.new_path:
            raise DiffAcquisitionError(RAW_DIFF_PATH_MISMATCH_REASON_V2)


def acquire_raw_diff_v2(repo_root: Path, *, base_sha: str, head_sha: str) -> str:
    """Run the canonical, fixed, allowlisted raw-diff command:
    ``git diff --no-ext-diff --raw -z --find-renames=50% --find-copies=50%
    <base_sha>...<head_sha>`` in ``repo_root``, and return its raw text.

    Uses the exact same ``_RENAME_COPY_DETECTION_ARGS_V2`` as
    ``acquire_diff_v2`` -- never independently configured -- so a caller
    correlating this stream against the unified diff (via
    ``correlate_raw_and_unified_v2``) is comparing two views of the same
    underlying diff computation, not two that could legitimately disagree
    because of mismatched thresholds.

    Never invokes a shell interpreter (the command is a fixed argv list).
    ``base_sha``/``head_sha`` must each be a full lowercase 40-character
    commit SHA, exactly like ``acquire_diff_v2`` -- rejected with
    ``DiffAcquisitionError(INVALID_REF_REASON_V2)`` otherwise.

    The entire subprocess output is decoded as UTF-8 up front, strictly:
    with ``-z``, git never quotes or escapes a path, so any raw path byte
    is either valid UTF-8 or the acquisition fails closed with
    ``DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)`` -- the same
    discipline, and the same reason code, ``acquire_diff_v2`` already
    applies to its own output.
    """

    if not _GIT_SHA_RE.match(base_sha) or not _GIT_SHA_RE.match(head_sha):
        raise DiffAcquisitionError(INVALID_REF_REASON_V2)

    result = _run_git_v2(
        [
            "git", "diff", "--no-ext-diff", "--raw", "-z",
            *_RENAME_COPY_DETECTION_ARGS_V2, f"{base_sha}...{head_sha}",
        ],
        repo_root=repo_root,
    )
    if result.returncode != 0:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2) from exc


def acquire_authoritative_diff_v2(
    repo_root: Path, *, base_sha: str, head_sha: str
) -> tuple[ParsedFileDiffV2, ...]:
    """Acquire both the unified diff and the raw ``-z`` stream for the
    same ``base_sha...head_sha`` range, with identical rename/copy
    detection thresholds, and return the unified ``ParsedFileDiffV2``
    tuple only after ``correlate_raw_and_unified_v2`` has proven its
    paths and change types against the raw stream's ground truth.

    This is the entry point a caller wanting authoritative path identity
    should use in place of calling ``acquire_diff_v2`` + ``parse_unified_diff``
    directly -- both remain available and unchanged for callers (e.g.
    existing tests) that do not need the cross-check.

    #200-G3B: this now delegates to
    ``authoritative_diff_identity_v2.acquire_authoritative_diff_with_identity_v2``,
    which performs the exact same acquisition/parse/correlate sequence and
    additionally proves the byte-identity digest of the acquired diff. The
    import is local to avoid a module-level import cycle (that module
    imports this one for the lower-level acquisition/parse/correlate
    primitives). Signature and return type are unchanged for existing
    callers -- the identity value is simply discarded here.
    """

    from app.agent_review.authoritative_diff_identity_v2 import (
        acquire_authoritative_diff_with_identity_v2,
    )

    file_diffs, _diff_text, _identity = acquire_authoritative_diff_with_identity_v2(
        repo_root, base_sha=base_sha, head_sha=head_sha
    )
    return file_diffs
