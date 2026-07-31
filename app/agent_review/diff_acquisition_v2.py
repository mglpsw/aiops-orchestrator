"""Git unified-diff acquisition for AgentReview v2 (issue #84).

Parses the text of ``git diff --no-ext-diff --binary BASE...HEAD`` into
structured per-file, per-hunk records (``ParsedFileDiffV2``/``ParsedHunkV2``)
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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
        escape_map = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
        mapped = escape_map.get(next_char)
        if mapped is not None:
            decoded.extend(mapped.encode("utf-8"))
            index += 2
            continue
        decoded.extend(next_char.encode("utf-8"))
        index += 2
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
    if len(tokens) != 2:
        return None
    return tokens[0], tokens[1]


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
            body_text = "\n".join(current_hunk_body)
            self.hunks.append(
                ParsedHunkV2(
                    old_start=current_old_start,
                    old_lines=current_old_lines,
                    new_start=current_new_start,
                    new_lines=current_new_lines,
                    diff_sha256=hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest(),
                    diff_chars=len(body_text),
                )
            )
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
                    current_hunk_body.append(line)
                    index += 1
                    continue
                if line == r"\ No newline at end of file":
                    # Applies to whichever side the immediately preceding
                    # body line belonged to -- a context line (" " prefix)
                    # is unchanged content present on *both* sides, so a
                    # missing trailing newline there means both sides lack
                    # it, not just the new side.
                    last = current_hunk_body[-1] if current_hunk_body else ""
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
                self.rename_from = _strip_ab_prefix(_decode_git_path(line[len("rename from "):]))
                index += 1
                continue
            if line.startswith("rename to "):
                self.rename_to = _strip_ab_prefix(_decode_git_path(line[len("rename to "):]))
                index += 1
                continue
            if line.startswith("copy from "):
                self.copy_from = _strip_ab_prefix(_decode_git_path(line[len("copy from "):]))
                index += 1
                continue
            if line.startswith("copy to "):
                self.copy_to = _strip_ab_prefix(_decode_git_path(line[len("copy to "):]))
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
    """Parse the full text of ``git diff --no-ext-diff --binary
    BASE...HEAD`` into structured per-file, per-hunk records.

    Never raises on content it does not specifically recognize inside a
    file block (unrecognized extended-header lines are skipped, matching
    unified diff's small set of optional headers); a structurally
    unreadable overall document (no ``diff --git`` markers found at all in
    non-empty input) raises ``DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)``.
    """

    if diff_text.strip() == "":
        return ()

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


_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def acquire_diff_v2(repo_root: Path, *, base_sha: str, head_sha: str) -> str:
    """Run the canonical, fixed, allowlisted diff command:
    ``git diff --no-ext-diff --binary <base_sha>...<head_sha>`` in
    ``repo_root``, and return its raw text.

    Never invokes a shell interpreter (the command is a fixed argv list). ``base_sha``/``head_sha`` must each be a full
    lowercase 40-character commit SHA -- never a branch, tag, or
    caller-supplied ref string -- rejected with
    ``DiffAcquisitionError(INVALID_REF_REASON_V2)`` otherwise, so a
    malicious or malformed ref can never be interpreted as a shell
    argument or an unexpected git revision expression (e.g. one starting
    with ``-``).
    """

    import subprocess

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
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, SHA-validated refs
        ["git", "diff", "--no-ext-diff", "--binary", f"{base_sha}...{head_sha}"],
        cwd=repo_root,
        capture_output=True,
        text=False,
        check=False,
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
    line-range hunks -- binary content, submodule/gitlink changes, or a
    hunkless metadata-only change (pure rename/copy, mode change, or an
    empty file add/delete with no hunk headers at all). A hunkless entry
    can never produce a ``HunkInputV2``/fragment for the line-range
    planner, so treating it as covered would let it silently disappear
    from review; these need an explicit, separate policy decision, never
    silent coverage."""
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
    unrepresentable = tuple(
        sorted(
            file_diff.path
            for file_diff in file_diffs
            if file_diff.path in expected_paths
            and (file_diff.is_binary or file_diff.is_submodule or not file_diff.hunks)
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
