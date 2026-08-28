from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agent_review.diff_acquisition_v2 import (
    DIFF_INFO_ATTRIBUTES_ACTIVE_REASON_V2,
    DIFF_LOCAL_FILTER_CONFIG_ACTIVE_REASON_V2,
    DIFF_UNREADABLE_REASON_V2,
    INVALID_REF_REASON_V2,
    RAW_DIFF_CARDINALITY_MISMATCH_REASON_V2,
    RAW_DIFF_PATH_MISMATCH_REASON_V2,
    RAW_DIFF_STATUS_MISMATCH_REASON_V2,
    RAW_DIFF_TYPE_CHANGE_UNSUPPORTED_REASON_V2,
    DiffAcquisitionError,
    RawDiffRecordV2,
    acquire_authoritative_diff_v2,
    acquire_diff_v2,
    acquire_raw_diff_v2,
    correlate_raw_and_unified_v2,
    parse_raw_diff_z,
    parse_unified_diff,
    validate_diff_completeness_v2,
)


def _raw_record(mode_old: str, mode_new: str, sha_old: str, sha_new: str, status: str, *paths: str) -> str:
    """Build one NUL-terminated ``git diff --raw -z`` record for tests --
    metadata line, then one path (single-path statuses) or two paths
    (rename/copy), each individually NUL-terminated, matching the exact
    byte shape verified empirically against real git output."""

    header = f":{mode_old} {mode_new} {sha_old} {sha_new} {status}"
    return "\x00".join((header, *paths)) + "\x00"


def _raw_stream(*records: str) -> str:
    return "".join(records)


# -- empty / malformed input --------------------------------------------------


def test_parse_returns_empty_tuple_for_an_empty_diff() -> None:
    assert parse_unified_diff("") == ()


def test_parse_returns_empty_tuple_for_a_whitespace_only_diff() -> None:
    assert parse_unified_diff("   \n\n  ") == ()


def test_parse_rejects_text_with_no_diff_git_markers() -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff("this is not a diff\njust some prose")
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


# -- basic modification --------------------------------------------------------


def test_parse_a_simple_modification() -> None:
    diff_text = (
        "diff --git a/app/service.py b/app/service.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/service.py\n"
        "+++ b/app/service.py\n"
        "@@ -10,6 +10,7 @@\n"
        " def resolve_shift_slot(slot):\n"
        "+    # comment\n"
        "     return slot\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert len(diffs) == 1
    file_diff = diffs[0]
    assert file_diff.path == "app/service.py"
    assert file_diff.old_path == "app/service.py"
    assert file_diff.new_path == "app/service.py"
    assert file_diff.change_type == "modified"
    assert not file_diff.is_binary
    assert not file_diff.is_submodule
    assert len(file_diff.hunks) == 1
    hunk = file_diff.hunks[0]
    assert (hunk.old_start, hunk.old_lines) == (10, 6)
    assert (hunk.new_start, hunk.new_lines) == (10, 7)
    assert len(hunk.diff_sha256) == 64


def test_parse_multiple_hunks_in_one_file() -> None:
    diff_text = (
        "diff --git a/file.py b/file.py\n"
        "index abc..def 100644\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        " c\n"
        "@@ -10,3 +10,3 @@\n"
        " x\n"
        "-y\n"
        "+Y\n"
        " z\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert len(diffs) == 1
    assert len(diffs[0].hunks) == 2
    assert [(h.old_start, h.new_start) for h in diffs[0].hunks] == [(1, 1), (10, 10)]


def test_parse_multiple_files_in_one_diff() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/b.py b/b.py\n"
        "index 3..4 100644\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-p\n"
        "+q\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert [d.path for d in diffs] == ["a.py", "b.py"]


# -- addition / deletion --------------------------------------------------------


def test_parse_a_new_file_addition() -> None:
    diff_text = (
        "diff --git a/new_file.py b/new_file.py\n"
        "new file mode 100644\n"
        "index 0000000..abc1234\n"
        "--- /dev/null\n"
        "+++ b/new_file.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].change_type == "added"
    assert diffs[0].old_path is None
    assert diffs[0].new_path == "new_file.py"
    assert diffs[0].path == "new_file.py"


def test_parse_a_deleted_file() -> None:
    diff_text = (
        "diff --git a/old_file.py b/old_file.py\n"
        "deleted file mode 100644\n"
        "index abc1234..0000000\n"
        "--- a/old_file.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-line1\n"
        "-line2\n"
        "-line3\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].change_type == "deleted"
    assert diffs[0].old_path == "old_file.py"
    assert diffs[0].new_path is None
    assert diffs[0].path == "old_file.py"  # falls back to old_path for a pure deletion


# -- rename / copy ---------------------------------------------------------------


def test_parse_a_rename_with_content_change() -> None:
    diff_text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 85%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
        "index abc1234..def5678 100644\n"
        "--- a/old_name.py\n"
        "+++ b/new_name.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_changed\n"
        " line3\n"
    )
    diffs = parse_unified_diff(diff_text)
    file_diff = diffs[0]
    assert file_diff.change_type == "renamed"
    assert file_diff.old_path == "old_name.py"
    assert file_diff.new_path == "new_name.py"
    assert file_diff.similarity_index == 85
    assert len(file_diff.hunks) == 1


def test_parse_a_pure_rename_without_content_change() -> None:
    diff_text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 100%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    diffs = parse_unified_diff(diff_text)
    file_diff = diffs[0]
    assert file_diff.change_type == "renamed"
    assert file_diff.similarity_index == 100
    assert file_diff.hunks == ()


def test_parse_preserves_a_rename_path_under_a_top_level_directory_named_a() -> None:
    """Unlike diff --git/---/+++ marker paths, git never adds an a/ or
    b/ prefix to "rename from"/"rename to" header paths -- they are
    already repo-relative. A file genuinely living under a top-level
    directory literally named "a" (real repo path "a/foo.py") must not
    have that "a/" stripped as if it were the diff-marker prefix."""

    diff_text = (
        "diff --git a/a/foo.py b/a/bar.py\n"
        "similarity index 100%\n"
        "rename from a/foo.py\n"
        "rename to a/bar.py\n"
    )
    diffs = parse_unified_diff(diff_text)
    file_diff = diffs[0]
    assert file_diff.old_path == "a/foo.py"
    assert file_diff.new_path == "a/bar.py"


# -- binary files (both "Binary files ... differ" and real "GIT binary patch") --


def test_parse_a_binary_file_with_the_human_readable_marker() -> None:
    diff_text = (
        "diff --git a/image.png b/image.png\n"
        "index abc1234..def5678 100644\n"
        "Binary files a/image.png and b/image.png differ\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "image.png"
    assert diffs[0].is_binary
    assert diffs[0].hunks == ()


def test_parse_a_binary_addition_with_the_human_readable_marker() -> None:
    diff_text = (
        "diff --git a/image.png b/image.png\n"
        "new file mode 100644\n"
        "index 0000000..def5678\n"
        "Binary files /dev/null and b/image.png differ\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "image.png"
    assert diffs[0].change_type == "added"
    assert diffs[0].is_binary


def test_parse_a_real_git_binary_patch_new_file() -> None:
    """Captured verbatim from `git diff --no-ext-diff --binary` against a
    real repository: with --binary, git emits neither "--- "/"+++ "
    markers nor a "Binary files ... differ" line -- only "GIT binary
    patch". The diff --git header is the only remaining path source."""

    diff_text = (
        "diff --git a/real_binary.bin b/real_binary.bin\n"
        "new file mode 100644\n"
        "index 0000000000000000000000000000000000000000"
        "..25710bb8ec308d59efdd5b0f7042e230cd6f8f11\n"
        "GIT binary patch\n"
        "literal 9\n"
        "QcmZQzWMXDvW%&OO00UJ54FCWD\n"
        "\n"
        "literal 0\n"
        "HcmV?d00001\n"
        "\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "real_binary.bin"
    assert diffs[0].change_type == "added"
    assert diffs[0].is_binary
    assert diffs[0].hunks == ()
    # An addition has no old blob -- old_path must not be populated just
    # because the diff --git header (the only path source for a GIT
    # binary patch block) always carries both an a/ and a b/ token, even
    # when one side doesn't exist. A consumer using old_path for an
    # explicit binary/blob-retrieval policy must not try to fetch a blob
    # from a revision where it never existed.
    assert diffs[0].old_path is None
    assert diffs[0].new_path == "real_binary.bin"


def test_parse_a_real_git_binary_patch_modification() -> None:
    diff_text = (
        "diff --git a/real_binary.bin b/real_binary.bin\n"
        "index 25710bb8ec308d59efdd5b0f7042e230cd6f8f11"
        "..961676565fc85d921185418178d450aa52c730a8 100644\n"
        "GIT binary patch\n"
        "literal 9\n"
        "Qcmd<&U}s=sWnpFl009vIEdT%j\n"
        "\n"
        "literal 9\n"
        "QcmZQzWMXDvW%&OO00UJ54FCWD\n"
        "\n"
        "diff --git a/next_file.py b/next_file.py\n"
        "index 1..2 100644\n"
        "--- a/next_file.py\n"
        "+++ b/next_file.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert len(diffs) == 2
    assert diffs[0].path == "real_binary.bin"
    assert diffs[0].change_type == "modified"
    assert diffs[0].is_binary
    # Parsing must resume correctly at the next file after the binary
    # patch body is skipped.
    assert diffs[1].path == "next_file.py"
    assert len(diffs[1].hunks) == 1


def test_parse_a_real_git_binary_patch_deletion() -> None:
    diff_text = (
        "diff --git a/real_binary.bin b/real_binary.bin\n"
        "deleted file mode 100644\n"
        "index 961676565fc85d921185418178d450aa52c730a8"
        "..0000000000000000000000000000000000000000\n"
        "GIT binary patch\n"
        "literal 0\n"
        "HcmV?d00001\n"
        "\n"
        "literal 9\n"
        "Qcmd<&U}s=sWnpFl009vIEdT%j\n"
        "\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "real_binary.bin"
    assert diffs[0].change_type == "deleted"
    assert diffs[0].is_binary
    # A deletion has no new blob -- symmetric to the addition case above.
    assert diffs[0].old_path == "real_binary.bin"
    assert diffs[0].new_path is None


# -- submodules (gitlinks, mode 160000) ------------------------------------------


def test_parse_a_submodule_addition() -> None:
    """Captured verbatim from a real gitlink (mode 160000) addition."""

    diff_text = (
        "diff --git a/vendor/sub b/vendor/sub\n"
        "new file mode 160000\n"
        "index 0000000..871b9e2\n"
        "--- /dev/null\n"
        "+++ b/vendor/sub\n"
        "@@ -0,0 +1 @@\n"
        "+Subproject commit 871b9e267d2b5aca430d4cc941cd6fa8b9e37703\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "vendor/sub"
    assert diffs[0].is_submodule
    assert diffs[0].change_type == "added"


def test_parse_a_submodule_pointer_update() -> None:
    diff_text = (
        "diff --git a/vendor/sub b/vendor/sub\n"
        "index abc1234..def5678 160000\n"
        "--- a/vendor/sub\n"
        "+++ b/vendor/sub\n"
        "@@ -1 +1 @@\n"
        "-Subproject commit abc1234000000000000000000000000000000000\n"
        "+Subproject commit def5678000000000000000000000000000000000\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "vendor/sub"
    assert diffs[0].is_submodule
    assert diffs[0].change_type == "modified"


# -- no newline at end of file ---------------------------------------------------


def test_parse_no_newline_at_eof_on_both_sides() -> None:
    diff_text = (
        "diff --git a/file.py b/file.py\n"
        "index abc..def 100644\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "\\ No newline at end of file\n"
        "+line2_changed\n"
        "\\ No newline at end of file\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].old_no_newline_at_eof
    assert diffs[0].new_no_newline_at_eof


def test_parse_no_newline_at_eof_only_on_new_side() -> None:
    diff_text = (
        "diff --git a/file.py b/file.py\n"
        "index abc..def 100644\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-line1\n"
        "+line1_no_trailing_newline\n"
        "\\ No newline at end of file\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert not diffs[0].old_no_newline_at_eof
    assert diffs[0].new_no_newline_at_eof


def test_parse_no_newline_at_eof_after_an_unchanged_context_line_marks_both_sides() -> None:
    """When the unchanged trailing line lacks a newline in both revisions,
    git emits exactly one marker after a space-prefixed context line --
    that context line is unchanged content present on both sides, so both
    flags must be set, not just the new side."""

    diff_text = (
        "diff --git a/file.py b/file.py\n"
        "index abc..def 100644\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_changed\n"
        " line3_unchanged\n"
        "\\ No newline at end of file\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].old_no_newline_at_eof
    assert diffs[0].new_no_newline_at_eof


# -- quoted / non-ASCII paths -----------------------------------------------------


def test_parse_a_quoted_path_with_octal_escapes() -> None:
    diff_text = (
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        "index abc..def 100644\n"
        '--- "a/caf\\303\\251.py"\n'
        '+++ "b/caf\\303\\251.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "café.py"


def test_parse_decodes_the_full_git_c_style_escape_set_correctly() -> None:
    """git's quote_c_style() (quote.c) escapes exactly these letters:
    a, b, f, n, r, t, v, backslash, and double-quote. A path containing a
    real backspace byte is quoted as "\\b" -- treating an unrecognized
    escape char as ordinary text (dropping the backslash) would silently
    decode that to the same string as a distinct real file literally
    named "b", conflating two different paths."""

    diff_text = (
        'diff --git "a/x\\bfile.py" "b/x\\bfile.py"\n'
        "index abc..def 100644\n"
        '--- "a/x\\bfile.py"\n'
        '+++ "b/x\\bfile.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].path == "x\bfile.py"
    assert diffs[0].path != "xbfile.py"


def test_parse_fails_closed_on_an_unrecognized_backslash_escape() -> None:
    """A backslash followed by a character that is neither one of git's
    own escape letters, backslash, double-quote, nor a 3-digit octal is
    not a sequence git's own quoting ever produces -- malformed or
    unrepresentable input, not a value to guess at by dropping the
    backslash."""

    diff_text = (
        'diff --git "a/x\\zfile.py" "b/x\\zfile.py"\n'
        "index abc..def 100644\n"
        '--- "a/x\\zfile.py"\n'
        '+++ "b/x\\zfile.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_fails_closed_on_an_undecodable_octal_escaped_path() -> None:
    """Arbitrary bytes are valid in a git filename. Two decoding
    strategies were tried and rejected: errors="replace" collapses
    distinct undecodable byte sequences (e.g. octal \\200 vs \\201) onto
    the same U+FFFD, conflating two different paths under one string;
    errors="surrogateescape" preserves distinctness but produces a lone
    surrogate that this module's own canonical-JSON fragment/manifest
    hashing (strict ``.encode("utf-8")``, no error handler, by design)
    cannot encode -- trading a silent collision for a crash several calls
    downstream. A path this module cannot represent as a valid, hashable
    string must fail closed here instead."""

    diff_text = (
        'diff --git "a/bad\\200.py" "b/bad\\200.py"\n'
        "index abc..def 100644\n"
        '--- "a/bad\\200.py"\n'
        '+++ "b/bad\\200.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


# -- real fixture from this repository -------------------------------------------


def test_parse_the_existing_agentescala_fixture_diff() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "agentescala_e2e"
        / "artifacts"
        / "full.diff"
    )
    diffs = parse_unified_diff(fixture.read_text(encoding="utf-8"))
    assert len(diffs) == 1
    assert diffs[0].path == "backend/services/shift_service.py"
    assert diffs[0].change_type == "modified"
    assert len(diffs[0].hunks) == 1


# -- completeness validation ------------------------------------------------------


def test_completeness_reports_missing_paths() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({"a.py", "b.py"}))
    assert not result.complete
    assert result.missing_paths == ("b.py",)
    assert result.unrepresentable_paths == ()


def test_completeness_flags_binary_and_submodule_paths_as_unrepresentable() -> None:
    diff_text = (
        "diff --git a/image.png b/image.png\n"
        "index abc..def 100644\n"
        "Binary files a/image.png and b/image.png differ\n"
        "diff --git a/vendor/sub b/vendor/sub\n"
        "index abc..def 160000\n"
        "--- a/vendor/sub\n"
        "+++ b/vendor/sub\n"
        "@@ -1 +1 @@\n"
        "-Subproject commit " + "a" * 40 + "\n"
        "+Subproject commit " + "b" * 40 + "\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(
        diffs, expected_paths=frozenset({"image.png", "vendor/sub"})
    )
    assert not result.complete
    assert result.missing_paths == ()
    assert set(result.unrepresentable_paths) == {"image.png", "vendor/sub"}


def test_completeness_flags_a_hunkless_pure_rename_as_unrepresentable() -> None:
    """A pure rename with no content change has no hunks, is not binary,
    and is not a submodule -- it must not be reported as covered, since it
    can never produce a HunkInputV2/fragment for the line-range planner
    and would otherwise silently disappear from review."""

    diff_text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 100%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({"new_name.py"}))
    assert not result.complete
    assert result.unrepresentable_paths == ("new_name.py",)
    assert result.missing_paths == ()


def test_completeness_flags_a_glob_metacharacter_path_as_unrepresentable() -> None:
    """A path containing a glob metacharacter (e.g. the common Next.js
    route app/[id]/page.tsx) is a perfectly ordinary, valid git path, but
    manifest_v2.FragmentV2.path's frozen RelativePath contract
    (contracts_v2._validate_relative_path) rejects *?[] outright -- those
    are reserved for the pattern type, never a concrete path. Letting
    this reach the planner uncaught would raise an uncontrolled
    pydantic.ValidationError well past acquisition; must be classified
    here as unrepresentable instead."""

    diff_text = (
        "diff --git a/app/[id]/page.tsx b/app/[id]/page.tsx\n"
        "index abc..def 100644\n"
        "--- a/app/[id]/page.tsx\n"
        "+++ b/app/[id]/page.tsx\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({"app/[id]/page.tsx"}))
    assert not result.complete
    assert result.unrepresentable_paths == ("app/[id]/page.tsx",)


def test_completeness_flags_a_decoded_control_character_path_as_unrepresentable() -> None:
    """A quoted path decoding to a real control character (e.g. a
    backspace from git's "\\b" escape) is a perfectly valid, ordinary git
    path -- no glob metacharacter is involved -- but still fails
    contracts_v2.RelativePath's control-character rule
    (_validate_normalized_relative_posix). Validating only for glob
    metacharacters would miss this and let it reach the planner as an
    uncontrolled pydantic.ValidationError; must be classified here
    against the actual authoritative contract instead."""

    diff_text = (
        'diff --git "a/x\\bfile.py" "b/x\\bfile.py"\n'
        "index abc..def 100644\n"
        '--- "a/x\\bfile.py"\n'
        '+++ "b/x\\bfile.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    path = diffs[0].path
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({path}))
    assert not result.complete
    assert result.unrepresentable_paths == (path,)


def test_completeness_reports_complete_when_everything_is_representable() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({"a.py"}))
    assert result.complete
    assert result.missing_paths == ()
    assert result.unrepresentable_paths == ()


# -- truncated patch detection ----------------------------------------------------


def test_parse_flags_a_hunk_that_declares_more_lines_than_it_contains() -> None:
    """A hunk header claiming 5 old/new lines but whose body only supplies
    2 is a truncated patch (e.g. an API response cut off mid-hunk), not a
    legitimately smaller hunk -- must be flagged, never silently trusted
    at its declared range."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,5 +1,5 @@\n"
        " line1\n"
        "-line2\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].truncated


def test_parse_does_not_flag_a_complete_hunk_as_truncated() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2changed\n"
        " line3\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert not diffs[0].truncated


def test_parse_does_not_flag_a_shorter_but_fully_declared_hunk_as_truncated() -> None:
    """A hunk correctly declaring a small range (e.g. 1 old / 1 new line)
    must never be flagged just because it is short."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert not diffs[0].truncated


def test_parse_flags_truncation_masked_by_a_trailing_newline_split_artifact() -> None:
    """``diff_text.split("\\n")`` on text ending in a newline yields a
    synthetic trailing empty element. Left uncorrected, that element
    satisfies the hunk body's blank-context-line check (``body_line ==
    ""``) and inflates both the old- and new-side actual line counts --
    masking exactly this case: a hunk declaring 1 old / 1 new line whose
    body supplies only a removal and an addition with no context line."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    diffs = parse_unified_diff(diff_text)
    assert diffs[0].truncated


def test_completeness_flags_truncated_paths_distinctly_from_unrepresentable() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,5 +1,5 @@\n"
        " line1\n"
        "-line2\n"
    )
    diffs = parse_unified_diff(diff_text)
    result = validate_diff_completeness_v2(diffs, expected_paths=frozenset({"a.py"}))
    assert not result.complete
    assert result.truncated_paths == ("a.py",)
    assert result.unrepresentable_paths == ()
    assert result.missing_paths == ()


# -- diff_sha256 / diff_chars integrity -------------------------------------------


def test_hunk_diff_sha256_changes_when_hunk_body_changes() -> None:
    def make(body_line: str) -> str:
        return (
            "diff --git a/a.py b/a.py\n"
            "index 1..2 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            f"{body_line}\n"
        )

    diffs_a = parse_unified_diff(make("-x"))
    diffs_b = parse_unified_diff(make("-z"))
    assert diffs_a[0].hunks[0].diff_sha256 != diffs_b[0].hunks[0].diff_sha256


def test_hunk_diff_sha256_differs_by_which_side_lacks_a_trailing_newline() -> None:
    """Two otherwise-identical hunks differing only in whether the old or
    new revision lacks the final newline emit the same -x/+y body text
    (the marker line itself is never added to the hashed body) -- the
    EOF marker's side must still be folded into the hash so they don't
    collide on diff_sha256/fragment_id despite genuinely different
    content."""

    diff_text_old_side_no_newline = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "\\ No newline at end of file\n"
        "+y\n"
    )
    diff_text_new_side_no_newline = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
        "\\ No newline at end of file\n"
    )
    diffs_old = parse_unified_diff(diff_text_old_side_no_newline)
    diffs_new = parse_unified_diff(diff_text_new_side_no_newline)
    assert diffs_old[0].hunks[0].diff_sha256 != diffs_new[0].hunks[0].diff_sha256


def test_parse_fails_closed_on_a_no_newline_marker_with_no_preceding_body_line() -> None:
    """A "\\ No newline at end of file" marker with no preceding -/+/space
    body line at all cannot belong to any side -- it is not a real EOF
    annotation but a symptom of malformed/reconstructed input (e.g. right
    after a bare hunk header). Guessing "both sides" for it would let a
    hunk with no actual changed-line content still produce a nonempty
    hunks tuple, which validate_diff_completeness_v2 would then wrongly
    treat as representable."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -0,0 +0,0 @@\n"
        "\\ No newline at end of file\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_fails_closed_on_a_hunk_with_zero_changed_lines() -> None:
    """A real unified-diff hunk always represents at least one actual
    change; a malformed "@@ -0,0 +0,0 @@" with no +/- content at all (no
    marker involved -- the orphaned-EOF-marker case is a distinct bug)
    must not be materialized as a real, representable hunk."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -0,0 +0,0 @@\n"
        "diff --git a/b.py b/b.py\n"
        "index 1..2 100644\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_fails_closed_on_a_hunk_following_an_eof_marker_in_the_same_file() -> None:
    """A "\\ No newline at end of file" marker can only ever apply to a
    file's true final hunk -- content cannot exist past end-of-file. A
    file that already recorded one and is then followed by another hunk
    is contradictory reconstructed/malformed input, not a legitimate
    multi-hunk file."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
        "\\ No newline at end of file\n"
        "@@ -5,1 +5,1 @@\n"
        "-w\n"
        "+z\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


# -- acquire_diff_v2: fixed argv, SHA-validated, no shell -------------------------


def test_acquire_diff_rejects_a_short_sha(tmp_path: Path) -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(tmp_path, base_sha="abc1234", head_sha="d" * 40)
    assert excinfo.value.reason_code == INVALID_REF_REASON_V2


def test_acquire_diff_rejects_a_branch_name_as_a_ref(tmp_path: Path) -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(tmp_path, base_sha="master", head_sha="d" * 40)
    assert excinfo.value.reason_code == INVALID_REF_REASON_V2


def test_acquire_diff_rejects_a_ref_shaped_like_a_command_flag(tmp_path: Path) -> None:
    """A ref beginning with '-' could be misinterpreted as a git flag if it
    ever reached a shell or a loosely-validated argv; must be rejected by
    the same strict 40-hex check as any other malformed ref."""

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(tmp_path, base_sha="-" + "a" * 39, head_sha="d" * 40)
    assert excinfo.value.reason_code == INVALID_REF_REASON_V2


@pytest.mark.requires_network
def test_acquire_diff_runs_the_real_fixed_git_command(tmp_path: Path) -> None:
    """Not actually network-dependent -- marked requires_network only to
    keep the default offline gate free of subprocess/git invocations,
    consistent with how this repo already gates similar real-process
    tests."""

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.py").write_text("line1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "a.py").write_text("line1\nline2\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "update"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    diff_text = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    diffs = parse_unified_diff(diff_text)
    assert len(diffs) == 1
    assert diffs[0].path == "a.py"
    assert len(diffs[0].hunks) == 1


@pytest.mark.requires_network
def test_acquire_diff_never_runs_a_configured_textconv_filter(tmp_path: Path) -> None:
    """--no-ext-diff alone does not disable a diff driver's textconv
    filter -- a separate git mechanism. Without --no-textconv, a
    repository whose .gitattributes assigns a path to a driver with a
    locally configured textconv command could get acquire_diff_v2 to
    execute that command while merely acquiring a patch, violating this
    module's "never executes untrusted code" boundary."""

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    marker = tmp_path / "textconv-ran.marker"
    subprocess.run(
        ["git", "config", "diff.marker.textconv", f"sh -c 'touch {marker}; cat \"$1\"' --"],
        cwd=repo,
        check=True,
    )
    (repo / ".gitattributes").write_text("a.py diff=marker\n", encoding="utf-8")
    (repo / "a.py").write_text("line1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py", ".gitattributes"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "a.py").write_text("line1\nline2\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "update"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert not marker.exists(), "acquire_diff_v2 must never execute a configured textconv filter"


@pytest.mark.requires_network
def test_acquire_diff_fails_closed_on_non_utf8_but_non_binary_content(tmp_path: Path) -> None:
    """A text file with a byte sequence that is not valid UTF-8 but
    contains no NUL byte is treated by git as an ordinary textual patch
    (not binary). ``subprocess.run(..., text=True)`` would decode under
    the process locale and raise a raw ``UnicodeDecodeError`` straight out
    of ``acquire_diff_v2``, bypassing this module's stable
    ``DiffAcquisitionError`` reason-code contract entirely -- must not do
    that. Lossily replacing the undecodable bytes was tried and rejected
    too: it would let distinct byte sequences collapse onto the same
    diff_sha256, breaking the hash's content-identity guarantee. The
    correct behavior is to fail closed with a stable reason code."""

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.py").write_bytes(b"line1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "a.py").write_bytes(b"line1\n\xff\xfe not valid utf-8\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "update"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_fails_closed_on_content_after_an_eof_marker_within_the_same_hunk() -> None:
    """Content cannot exist past end-of-file on the side a marker
    applies to, even within the same hunk -- distinct from the
    already-fixed later-hunk case. A removal after an old-side marker,
    an addition after a new-side marker, or a context line (which
    requires both sides to still have real content) after either marker
    are all physically impossible."""

    old_side_then_more_old = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,1 @@\n"
        "-x\n"
        "\\ No newline at end of file\n"
        "-w\n"
        "+y\n"
    )
    new_side_then_more_new = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        "-x\n"
        "+y\n"
        "\\ No newline at end of file\n"
        "+z\n"
    )
    for diff_text in (old_side_then_more_old, new_side_then_more_new):
        with pytest.raises(DiffAcquisitionError) as excinfo:
            parse_unified_diff(diff_text)
        assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_fails_closed_on_non_utf8_encodable_text_passed_directly() -> None:
    """parse_unified_diff is a public, pure-text boundary callable by any
    adapter, not only via acquire_diff_v2 (whose own subprocess output is
    already strictly decoded). A caller-supplied str containing a lone
    surrogate must be rejected here too, before any hunk-body hashing
    (which uses errors="replace" and would otherwise collapse distinct
    surrogates onto the same encoded byte)."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\ud800\n"
        "+y\n"
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_unified_diff(diff_text)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


# =============================================================================
# Issue #103 — raw -z as the deterministic authority for path identity and
# change type, correlated against the unified diff's own blocks.
# =============================================================================


# -- parse_raw_diff_z: pure parsing of the NUL-separated raw stream ----------


def test_parse_raw_diff_z_returns_empty_tuple_for_empty_stream() -> None:
    assert parse_raw_diff_z("") == ()


def test_parse_raw_diff_z_parses_a_single_path_modified_record() -> None:
    stream = _raw_stream(_raw_record("100644", "100644", "abc1234", "def5678", "M", "app/service.py"))
    records = parse_raw_diff_z(stream)
    assert records == (
        RawDiffRecordV2(status="M", similarity_index=None, old_path="app/service.py", new_path="app/service.py"),
    )


def test_parse_raw_diff_z_parses_an_added_record_with_only_new_path() -> None:
    stream = _raw_stream(_raw_record("000000", "100644", "0000000", "abc1234", "A", "new_file.py"))
    records = parse_raw_diff_z(stream)
    assert records == (
        RawDiffRecordV2(status="A", similarity_index=None, old_path=None, new_path="new_file.py"),
    )


def test_parse_raw_diff_z_parses_a_deleted_record_with_only_old_path() -> None:
    stream = _raw_stream(_raw_record("100644", "000000", "abc1234", "0000000", "D", "gone.py"))
    records = parse_raw_diff_z(stream)
    assert records == (
        RawDiffRecordV2(status="D", similarity_index=None, old_path="gone.py", new_path=None),
    )


def test_parse_raw_diff_z_parses_a_rename_record_with_two_paths() -> None:
    """Proves the parser treats a rename as ONE logical record carrying
    both old_path and new_path -- the exact case #103 requires: 'rename
    com espaço nos dois lados' parsed as a single two-path record, not
    two records or a token-count mismatch."""

    stream = _raw_stream(
        _raw_record("100644", "100644", "aaa1111", "bbb2222", "R090", "old name file.txt", "new name file.txt")
    )
    records = parse_raw_diff_z(stream)
    assert len(records) == 1
    assert records[0].status == "R"
    assert records[0].similarity_index == 90
    assert records[0].old_path == "old name file.txt"
    assert records[0].new_path == "new name file.txt"


def test_parse_raw_diff_z_parses_a_copy_record_as_one_record_with_two_paths() -> None:
    """'copy (C<score>) tratado como registro de dois paths, não como
    dois registros' -- a single raw metadata line for status C must
    yield exactly one RawDiffRecordV2, never two."""

    stream = _raw_stream(
        _raw_record("100644", "100644", "ccc3333", "ccc3333", "C066", "source.txt", "copied target file.txt")
    )
    records = parse_raw_diff_z(stream)
    assert len(records) == 1
    assert records[0].status == "C"
    assert records[0].old_path == "source.txt"
    assert records[0].new_path == "copied target file.txt"


def test_parse_raw_diff_z_parses_multiple_records_in_stable_order() -> None:
    stream = _raw_stream(
        _raw_record("100644", "100644", "1", "2", "M", "a.py"),
        _raw_record("000000", "100644", "0", "3", "A", "b.py"),
        _raw_record("100644", "000000", "4", "0", "D", "c.py"),
    )
    records = parse_raw_diff_z(stream)
    assert [r.status for r in records] == ["M", "A", "D"]
    assert [r.new_path or r.old_path for r in records] == ["a.py", "b.py", "c.py"]


def test_parse_raw_diff_z_fails_closed_on_a_malformed_header_line() -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z("not-a-valid-raw-header\x00a.py\x00")
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_fails_closed_on_a_stream_truncated_before_its_path() -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z(":100644 100644 abc1234 def5678 M\x00")
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_fails_closed_on_a_rename_missing_its_second_path() -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z(":100644 100644 abc1234 def5678 R090\x00only_one_path.py\x00")
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_fails_closed_when_a_rename_missing_its_second_path_is_followed_by_a_real_record() -> None:
    """Issue #113: the case above (a rename/copy record missing its second
    path token) was only caught when it hit end-of-stream -- the
    `index + 1 >= len(tokens)` guard. When the missing path token is
    instead followed by a REAL next record, the prior implementation
    silently consumed that record's own header as if it were the missing
    path: for the exact stream below, it accepted ONE record with
    old_path=':100644 100644 3 4 M' (the next record's header, misread as
    a path) and new_path='b.py', instead of raising
    DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2) as the function's own
    docstring promises. Confirmed directly against the prior
    implementation before this fix (accepted without error, wrong record
    count and wrong paths)."""

    stream = ":100644 100644 1 2 R100\x00:100644 100644 3 4 M\x00b.py\x00"
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z(stream)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_fails_closed_when_a_copy_missing_its_second_path_is_followed_by_a_real_record() -> None:
    """Same defect, status C instead of R -- both are two-path statuses
    sharing the vulnerable branch."""

    stream = ":100644 100644 1 2 C066\x00:000000 100644 0 3 A\x00new_file.py\x00"
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z(stream)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_fails_closed_when_a_single_path_record_missing_its_path_is_followed_by_a_chain_of_real_records() -> None:
    """Found by independent review of this same PR: the identical defect
    class exists in the single-path branch (A/D/M/T), not just the
    two-path (R/C) branch this issue's title names. A record missing its
    OWN path token, followed by exactly one more record, happens to fail
    closed already (the leftover token exhausts the stream or fails the
    next header match) -- but a chain of TWO OR MORE further records lets
    the parser re-synchronize on a later header, silently returning a
    wrong record AND silently dropping an intervening real one.

    Confirmed directly against the unpatched branch: this exact stream
    returned 2 records (not an error) -- an 'M' record with both paths
    corrupted to the literal text of the 'D' record's header, and the 'D'
    record itself vanishing entirely, leaving only 'M' and 'A'."""

    stream = ":100644 100644 1 2 M\x00:100644 100644 3 4 D\x00:000000 100644 0 5 A\x00last.py\x00"
    with pytest.raises(DiffAcquisitionError) as excinfo:
        parse_raw_diff_z(stream)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


def test_parse_raw_diff_z_decodes_an_accented_utf8_path_exactly() -> None:
    """Equivalence fixture per issue #103's boundary: the GitHub API
    filename is a VERIFICATION value for this exact scenario (AgentEscala
    #752's field report: the API returns the decoded name, the old
    quoted-header path was 'b/docs/revis\\303\\243o.md'). With -z, git
    never quotes or escapes the path at all -- the raw stream carries the
    literal UTF-8 bytes directly, so no octal-escape decoding is needed
    here (unlike `_decode_git_path`, which this module deliberately does
    not touch)."""

    github_api_filename_fixture = "docs/revisão.md"
    stream = _raw_stream(
        _raw_record("100644", "100644", "1111111", "2222222", "M", github_api_filename_fixture)
    )
    records = parse_raw_diff_z(stream)
    assert records[0].new_path == github_api_filename_fixture
    assert records[0].old_path == github_api_filename_fixture


# -- correlate_raw_and_unified_v2: cross-checking raw against unified -------


def test_correlate_accepts_a_matching_single_file_modification() -> None:
    diff_text = (
        "diff --git a/app/service.py b/app/service.py\n"
        "index abc1234..def5678 100644\n"
        "--- a/app/service.py\n"
        "+++ b/app/service.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "100644", "abc1234", "def5678", "M", "app/service.py"))
    )
    # must not raise
    correlate_raw_and_unified_v2(raw_records, file_diffs)


def test_correlate_fails_closed_on_cardinality_mismatch() -> None:
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(
            _raw_record("100644", "100644", "1", "2", "M", "a.py"),
            _raw_record("000000", "100644", "0", "3", "A", "b.py"),
        )
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_CARDINALITY_MISMATCH_REASON_V2


def test_correlate_fails_closed_on_status_mismatch() -> None:
    """Raw says 'added' (status A); unified reports the block as an
    ordinary modification -- a genuine disagreement about change type."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("000000", "100644", "0", "2", "A", "a.py"))
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_STATUS_MISMATCH_REASON_V2


def test_correlate_fails_closed_on_a_rename_whose_paths_do_not_match() -> None:
    diff_text = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 90%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(
            _raw_record("100644", "100644", "1", "2", "R090", "old_name.py", "different_name.py")
        )
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_PATH_MISMATCH_REASON_V2


def test_correlate_fails_closed_on_order_divergence_between_two_records() -> None:
    """Two distinct, individually valid files whose raw records are
    supplied in the opposite order from the unified blocks: position i
    must correspond to block i, so a swap manifests as a mismatch at
    both positions rather than being silently accepted as a set."""

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "index 1..2 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/b.py b/b.py\n"
        "index 3..4 100644\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(
            _raw_record("100644", "100644", "3", "4", "M", "b.py"),
            _raw_record("100644", "100644", "1", "2", "M", "a.py"),
        )
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_PATH_MISMATCH_REASON_V2


def test_correlate_accepts_a_pure_mode_change_as_type_changed() -> None:
    """A pure chmod (no content change, no hunks) is raw status 'M' --
    NOT 'T' -- verified empirically against real git. The existing
    unified parser labels this hunkless, mode-only shape 'type_changed';
    correlation must accept raw 'M' against either 'modified' (content
    changed) or 'type_changed' (pure attribute change), since a single
    raw letter legitimately covers both."""

    diff_text = "diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755\n"
    file_diffs = parse_unified_diff(diff_text)
    assert file_diffs[0].change_type == "type_changed"
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "100755", "ce01362", "ce01362", "M", "script.sh"))
    )
    # must not raise
    correlate_raw_and_unified_v2(raw_records, file_diffs)


def test_correlate_fails_closed_on_a_raw_type_change_record() -> None:
    """A genuine object-type change (raw status 'T', e.g. regular file ->
    symlink) is represented by git's UNIFIED diff as a delete+add PAIR
    (two blocks), not one -- verified empirically against real git. This
    module does not attempt to correlate a 1-record/2-block shape; it
    fails closed with a dedicated reason code rather than misreporting it
    as a generic cardinality mismatch or silently accepting a wrong
    pairing."""

    diff_text = (
        "diff --git a/target.txt b/target.txt\n"
        "deleted file mode 100644\n"
        "index ce01362..0000000\n"
        "--- a/target.txt\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-hello\n"
        "diff --git a/target.txt b/target.txt\n"
        "new file mode 120000\n"
        "index 0000000..0231def\n"
        "--- /dev/null\n"
        "+++ b/target.txt\n"
        "@@ -0,0 +1,1 @@\n"
        "+script.sh\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "120000", "ce01362", "0231def", "T", "target.txt"))
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_TYPE_CHANGE_UNSUPPORTED_REASON_V2


def test_correlate_accepts_a_binary_space_path_now_that_it_is_resolved() -> None:
    """Case (a) from issue #103, corrected by a later Codex review (#99):
    a 'GIT binary patch' block for a file whose name contains a space has
    no '--- '/'+++ ' markers and no 'Binary files ... differ' line, so the
    'diff --git' header itself is the only source of its path. git quotes
    a path only for non-ASCII/special characters, never merely for a
    plain space, so the header's own whitespace-token count for a spaced
    path is ambiguous on its face -- but by the time this fallback runs,
    any rename/copy has already been consumed from its own dedicated
    header lines, so old_path and new_path are ALWAYS identical here.
    _split_diff_git_header_paths now exploits that invariant (the header
    is always exactly "a/<X> b/<X>" for a single X) to resolve the path
    correctly instead of returning None. Previously this test asserted
    the unresolved path ("") and relied on correlate_raw_and_unified_v2's
    raw-stream cross-check to fail closed as a defense-in-depth net; the
    root cause is now fixed, so the path resolves correctly and
    correlation succeeds instead of raising."""

    diff_text = (
        "diff --git a/my file.bin b/my file.bin\n"
        "new file mode 100644\n"
        "index 0000000..a611fb8\n"
        "GIT binary patch\n"
        "literal 16\n"
        "XcmZQzWJ=1+ODw8P&d)1J%_{)_CNl+u\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    assert file_diffs[0].path == "my file.bin"
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("000000", "100644", "0000000", "a611fb8", "A", "my file.bin"))
    )
    correlate_raw_and_unified_v2(raw_records, file_diffs)  # succeeds, raises nothing


def test_correlate_catches_the_and_in_name_corruption_bug() -> None:
    """Case (b) from issue #103: the 'Binary files ... differ' marker
    line splits on the FIRST ' and ' it finds, so a filename that itself
    contains the literal text ' and ' corrupts both old_path and
    new_path. This textual marker form is not reachable through the real
    acquire_diff_v2 pipeline (which always passes --binary, so real git
    emits 'GIT binary patch' instead) -- but parse_unified_diff is a
    public, pure-text boundary any adapter may call directly (its own
    module docstring), so the corruption must still be caught here."""

    diff_text = (
        "diff --git a/x and y.bin b/x and y.bin\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/x and y.bin and b/x and y.bin differ\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    assert file_diffs[0].old_path == "x"  # the corruption this issue closes, reproduced
    assert file_diffs[0].new_path == "y.bin and b/x and y.bin"
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "100644", "1111111", "2222222", "M", "x and y.bin"))
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_PATH_MISMATCH_REASON_V2


def test_correlate_fails_closed_on_a_confident_but_wrong_unified_header() -> None:
    """'header inequívoco discordando do stream': the unified side is not
    ambiguous here -- it confidently reports a coherent, well-formed path
    -- but it simply disagrees with the raw stream's ground truth (e.g. a
    reconstructed/replayed diff pointing at the wrong file). Confidence
    is not correctness; correlation must still fail closed."""

    diff_text = (
        "diff --git a/reported_name.py b/reported_name.py\n"
        "index abc1234..def5678 100644\n"
        "--- a/reported_name.py\n"
        "+++ b/reported_name.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "100644", "abc1234", "def5678", "M", "actual_name.py"))
    )
    with pytest.raises(DiffAcquisitionError) as excinfo:
        correlate_raw_and_unified_v2(raw_records, file_diffs)
    assert excinfo.value.reason_code == RAW_DIFF_PATH_MISMATCH_REASON_V2


def test_correlate_accepts_a_safe_rename_path_under_a_top_level_directory_named_a() -> None:
    """Safe counterexample that must keep passing: a real path under a
    top-level directory literally named 'a' is not an artifact of the
    diff-marker a/ b/ prefix, and must correlate cleanly."""

    diff_text = (
        "diff --git a/a/foo.py b/a/bar.py\n"
        "similarity index 100%\n"
        "rename from a/foo.py\n"
        "rename to a/bar.py\n"
    )
    file_diffs = parse_unified_diff(diff_text)
    raw_records = parse_raw_diff_z(
        _raw_stream(_raw_record("100644", "100644", "1", "1", "R100", "a/foo.py", "a/bar.py"))
    )
    # must not raise
    correlate_raw_and_unified_v2(raw_records, file_diffs)


# -- acquire_raw_diff_v2 / acquire_authoritative_diff_v2 (real git) ----------


def test_acquire_raw_diff_rejects_a_short_sha(tmp_path: Path) -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_raw_diff_v2(tmp_path, base_sha="abc1234", head_sha="d" * 40)
    assert excinfo.value.reason_code == INVALID_REF_REASON_V2


def test_acquire_raw_diff_rejects_a_branch_name_as_a_ref(tmp_path: Path) -> None:
    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_raw_diff_v2(tmp_path, base_sha="master", head_sha="d" * 40)
    assert excinfo.value.reason_code == INVALID_REF_REASON_V2


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.requires_network
def test_acquire_raw_diff_runs_the_real_fixed_git_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("line1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "a.py").write_text("line1\nline2\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    raw_text = acquire_raw_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    records = parse_raw_diff_z(raw_text)
    assert len(records) == 1
    assert records[0].status == "M"
    assert records[0].new_path == "a.py"


@pytest.mark.requires_network
def test_acquire_authoritative_diff_returns_file_diffs_for_a_simple_modification(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("line1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "a.py").write_text("line1\nline2\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert len(file_diffs) == 1
    assert file_diffs[0].path == "a.py"
    assert file_diffs[0].change_type == "modified"


@pytest.mark.requires_network
def test_acquire_authoritative_diff_resolves_a_real_binary_file_with_a_space(tmp_path: Path) -> None:
    """End-to-end reproduction of case (a), corrected by a later Codex
    review (#99): a genuinely new binary file whose name contains a
    space, added through a real git repository and acquired through the
    exact --binary-flagged command acquire_diff_v2 already uses. This
    produces a 'GIT binary patch' block whose header alone is the only
    path source; _split_diff_git_header_paths now resolves it correctly
    (previously it could not be split into two whitespace tokens, and the
    caller relied on the raw-stream correlation to fail closed instead)."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "my file.bin").write_bytes(b"\x00\x01\x02binarycontent")
    head_sha = _commit_all(repo, "add binary with space")

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert len(file_diffs) == 1
    assert file_diffs[0].path == "my file.bin"
    assert file_diffs[0].change_type == "added"
    assert file_diffs[0].is_binary


@pytest.mark.requires_network
def test_acquire_authoritative_diff_ignores_ambient_diff_noprefix(tmp_path: Path) -> None:
    """Codex review of PR #158 itself: diff.noprefix=true (a real,
    documented git config) makes git emit "diff --git my file.bin my
    file.bin" instead of the canonical "a/<path> b/<path>" header --
    _split_diff_git_header_paths' fallback only recognizes headers
    starting with "a/", so a prefixless spaced binary path would still
    resolve to nothing even after the #99 fix. --src-prefix=a/
    --dst-prefix=b/ are now explicit, fixed arguments so the header
    format never depends on this ambient config."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "my file.bin").write_bytes(b"\x00\x01\x02binarycontent")
    head_sha = _commit_all(repo, "add binary with space")

    subprocess.run(["git", "config", "diff.noprefix", "true"], cwd=repo, check=True)

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert len(file_diffs) == 1
    assert file_diffs[0].path == "my file.bin"
    assert file_diffs[0].change_type == "added"
    assert file_diffs[0].is_binary


@pytest.mark.requires_network
def test_acquire_authoritative_diff_handles_a_real_rename_and_copy_with_spaces(tmp_path: Path) -> None:
    """A real rename AND a real copy (source also modified, within
    --find-copies' default detection scope), both with spaces on both
    sides of the path, in the same commit -- proving the shared,
    explicit --find-renames/--find-copies flags detect and correlate
    identically between the raw and unified acquisitions."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "old name file.txt").write_text(
        "line one\nline two\nline three\nline four\nline five\n", encoding="utf-8"
    )
    base_sha = _commit_all(repo, "init")

    subprocess.run(
        ["git", "mv", "old name file.txt", "new name file.txt"], cwd=repo, check=True
    )
    (repo / "new name file.txt").write_text(
        "line one\nline two\nline three CHANGED\nline four\nline five\n", encoding="utf-8"
    )
    (repo / "copied target file.txt").write_text(
        "line one\nline two\nline three CHANGED\nline four\nline five\n", encoding="utf-8"
    )
    head_sha = _commit_all(repo, "rename with spaces + copy")

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    change_types = {fd.change_type for fd in file_diffs}
    assert change_types == {"renamed", "copied"}
    paths = {(fd.old_path, fd.new_path) for fd in file_diffs}
    assert ("old name file.txt", "new name file.txt") in paths
    assert ("old name file.txt", "copied target file.txt") in paths


@pytest.mark.requires_network
def test_acquire_authoritative_diff_preserves_a_real_rename_under_directory_named_a(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a").mkdir()
    (repo / "a" / "foo.py").write_text("content\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    subprocess.run(["git", "mv", "a/foo.py", "a/bar.py"], cwd=repo, check=True)
    head_sha = _commit_all(repo, "rename under a/")

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert len(file_diffs) == 1
    assert file_diffs[0].old_path == "a/foo.py"
    assert file_diffs[0].new_path == "a/bar.py"


@pytest.mark.requires_network
def test_acquire_authoritative_diff_detects_renames_despite_a_low_ambient_rename_limit(
    tmp_path: Path,
) -> None:
    """Codex review of PR #112: --find-renames/--find-copies alone do not
    bound how many candidate paths git will attempt to pair up (git diff -h
    documents -l<n> as "limit rename attempts up to <n> paths"). A large
    enough number of similar-but-not-identical renames (a PURE, byte-
    identical rename is matched via a cheap exact-content lookup that
    never consults renameLimit at all -- confirmed empirically; this is
    why each file gets one appended line after the rename) makes real git
    skip inexact rename detection entirely once diff.renameLimit is set
    low, printing "exhaustive rename detection was skipped due to too many
    files" and falling back to delete+add pairs -- confirmed empirically
    against this exact scenario. -l1000, now an explicit, fixed argument
    shared by both acquisitions rather than inherited from ambient config,
    keeps rename detection working regardless of a hostile-low ambient
    diff.renameLimit (simulating an ambient runner setting or a large diff
    exceeding git's built-in default)."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    file_count = 60
    for index in range(file_count):
        lines = "\n".join(f"line {n} of file {index} with some content padding here" for n in range(50))
        (repo / f"file{index}.txt").write_text(lines + "\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")

    for index in range(file_count):
        subprocess.run(["git", "mv", f"file{index}.txt", f"file{index}_renamed.txt"], cwd=repo, check=True)
        with (repo / f"file{index}_renamed.txt").open("a", encoding="utf-8") as handle:
            handle.write("one extra line appended after the rename\n")
    head_sha = _commit_all(repo, "rename and slightly modify every file")

    subprocess.run(["git", "config", "diff.renameLimit", "1"], cwd=repo, check=True)

    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    change_types = {fd.change_type for fd in file_diffs}
    assert change_types == {"renamed"}
    assert len(file_diffs) == file_count
    paths = {(fd.old_path, fd.new_path) for fd in file_diffs}
    assert paths == {(f"file{index}.txt", f"file{index}_renamed.txt") for index in range(file_count)}


@pytest.mark.requires_network
def test_acquire_authoritative_diff_fails_closed_on_a_real_type_change(tmp_path: Path) -> None:
    """End-to-end reproduction of the object-type-change asymmetry
    discovered while building this issue: a real regular-file-to-symlink
    conversion produces one raw 'T' record but two unified blocks. This
    module fails closed with a dedicated reason code rather than
    misreporting it as a cardinality mismatch."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "script.sh").write_text("hello\n", encoding="utf-8")
    (repo / "target.txt").write_text("hello\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")

    (repo / "target.txt").unlink()
    (repo / "target.txt").symlink_to("script.sh")
    head_sha = _commit_all(repo, "convert to symlink")

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert excinfo.value.reason_code == RAW_DIFF_TYPE_CHANGE_UNSUPPORTED_REASON_V2


@pytest.mark.requires_network
def test_acquire_raw_diff_fails_closed_on_non_utf8_path_bytes(tmp_path: Path) -> None:
    """A filename containing a byte sequence that is not valid UTF-8:
    with -z, git never quotes or escapes it, so the raw stream itself
    contains the invalid bytes directly. Decoding the whole subprocess
    output as UTF-8 must fail closed with the same disciplined reason
    code the rest of this module already uses for undecodable output."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")

    # Path does not support a bytes path component; construct and open the
    # non-UTF-8 filename via raw bytes at the os level instead.
    bad_name_bytes = os.fsencode(str(repo)) + b"/f\xff\xfe.txt"
    fd = os.open(bad_name_bytes, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        os.write(fd, b"content\n")
    finally:
        os.close(fd)
    head_sha = _commit_all(repo, "add non-utf8 filename")

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_raw_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert excinfo.value.reason_code == DIFF_UNREADABLE_REASON_V2


# -- `#200-D` correction: attribute-source binding and info/attributes -------


def test_untracked_worktree_gitattributes_does_not_change_the_canonical_diff(tmp_path: Path) -> None:
    """M4: an UNTRACKED `.gitattributes` planted in the target's own
    working tree must have zero effect on `acquire_diff_v2`'s output for
    the identical `base_sha...head_sha` range."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    diff_before = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    diff_during_attack = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    assert diff_during_attack == diff_before
    assert "GIT binary patch" not in diff_during_attack


def test_modified_worktree_gitattributes_does_not_change_the_canonical_diff(tmp_path: Path) -> None:
    """M4 variant: a MODIFIED (not merely added) working-tree
    `.gitattributes` must be equally inert."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("*.md text\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    diff_before = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")  # modified, uncommitted
    diff_during_attack = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    assert diff_during_attack == diff_before


def test_gitattributes_committed_at_head_sha_is_applied_deterministically(tmp_path: Path) -> None:
    """The flip side of M4: attributes actually declared AT `head_sha`
    must still be honored -- the fix binds attribute resolution to the
    declared subject, it does not disable attributes altogether."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update with committed attributes")

    diff_text = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert "GIT binary patch" in diff_text


def test_raw_diff_acquisition_is_also_attribute_source_bound(tmp_path: Path) -> None:
    """The grant requires unified and raw/correlation acquisitions to use
    compatible attribute semantics -- `acquire_raw_diff_v2` gets the
    identical untracked-`.gitattributes` immunity."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    raw_before = acquire_raw_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    raw_during_attack = acquire_raw_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    assert raw_during_attack == raw_before


def test_different_checkout_head_does_not_change_the_canonical_diff(tmp_path: Path) -> None:
    """The target's checked-out HEAD (as opposed to the declared
    `head_sha` argument) must be irrelevant to attribute resolution -- the
    disposable worktree is always checked out at the DECLARED subject,
    never at whatever the target happens to be sitting on."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    diff_at_real_head = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    subprocess.run(["git", "checkout", "--quiet", "--detach", base_sha], cwd=repo, check=True)
    diff_with_different_checkout_head = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    assert diff_with_different_checkout_head == diff_at_real_head


def test_active_info_attributes_refuses_diff_acquisition(tmp_path: Path) -> None:
    """M5: `$GIT_DIR/info/attributes` is not closed by the disposable
    worktree (it is shared common-dir state) and must fail closed rather
    than silently influence the diff."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("reviewed.txt -diff\n", encoding="utf-8")

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert excinfo.value.reason_code == DIFF_INFO_ATTRIBUTES_ACTIVE_REASON_V2


def test_comment_only_info_attributes_does_not_block(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "reviewed.txt").write_text("l1\nl2\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("l1\nl2 changed\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("# nothing active\n", encoding="utf-8")

    diff_text = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    assert "l2 changed" in diff_text


def test_replacement_blob_substitution_is_closed_on_the_reference_material_path(tmp_path: Path) -> None:
    """M1, exercised through this module's own public reference-material
    reader (the function `reference_source_v2` depends on)."""

    from app.agent_review.diff_acquisition_v2 import read_head_tree_entry_v2

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "artifact.txt").write_text("legit content\n", encoding="utf-8")
    head_sha = _commit_all(repo, "add artifact")

    artifact_blob = subprocess.run(
        ["git", "rev-parse", f"{head_sha}:artifact.txt"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    malicious_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input="MALICIOUS SUBSTITUTED\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "replace", artifact_blob, malicious_blob], cwd=repo, check=True)

    entry = read_head_tree_entry_v2(repo, head_sha=head_sha, relative_path="artifact.txt")
    assert entry.content == b"legit content\n"


def test_acquisition_does_not_execute_a_target_post_checkout_hook(tmp_path: Path):
    """`#200-D` correction, found by an independent review of the FIX itself.

    The disposable worktree that binds attribute resolution to the declared
    subject is created with `git worktree add` -- which runs the TARGET
    repository's `post-checkout` hook. That made the attribute fix a
    target-controlled code-execution path, contradicting the "never
    executes untrusted code" boundary this module documents for itself
    when it explains `--no-textconv`. Same threat model as the untracked
    `.gitattributes` this worktree exists to defeat: anyone who can plant
    that file can plant a hook.
    """

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    marker = tmp_path / "hook-ran"
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)

    assert "world" in diff, "acquisition must still return the real diff"
    assert not marker.exists(), "target post-checkout hook executed during acquisition"


def test_acquisition_does_not_execute_a_redirected_target_hook(tmp_path: Path):
    """The same closure has to survive a repository-local `core.hooksPath`
    redirect, which reaches an arbitrary directory and is not covered by
    neutralizing environment variables."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    marker = tmp_path / "redirected-hook-ran"
    evil_hooks = tmp_path / "evil-hooks"
    evil_hooks.mkdir()
    hook = evil_hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(evil_hooks)], cwd=repo, check=True
    )

    diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)

    assert "world" in diff, "acquisition must still return the real diff"
    assert not marker.exists(), "redirected target hook executed during acquisition"


def test_acquisition_refuses_a_target_with_an_executable_filter_driver(tmp_path: Path):
    """`#200-D` correction, second round. `git worktree add` checks files
    out, so a repository-local `filter.<driver>.smudge` executes -- verified
    directly against this host's Git. The driver name is chosen by whoever
    wrote the config, so unlike `core.hooksPath`/`core.fsmonitor` there is
    no `-c` override to enumerate, and `--no-checkout` is not an
    alternative (it leaves the worktree empty, so attribute resolution --
    the worktree's entire purpose -- stops working). Refused instead, the
    same fail-closed shape as `$GIT_DIR/info/attributes`.
    """

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("f.txt filter=evil\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    marker = tmp_path / "smudge-ran"
    subprocess.run(
        ["git", "config", "filter.evil.smudge", f"sh -c 'touch {marker}; cat'"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "config", "filter.evil.required", "false"], cwd=repo, check=True)

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_diff_v2(repo, base_sha=base, head_sha=head)

    assert excinfo.value.reason_code == DIFF_LOCAL_FILTER_CONFIG_ACTIVE_REASON_V2
    assert not marker.exists(), "the filter executed before the refusal"


def test_acquisition_still_works_for_a_repository_with_no_filter_config(tmp_path: Path):
    """The refusal above must be conditional, not a blanket failure."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    assert "world" in acquire_diff_v2(repo, base_sha=base, head_sha=head)


def test_acquisition_ignores_a_repository_local_attributes_file_redirect(tmp_path: Path):
    """`#200-D` correction, second round. A repository-local
    `core.attributesFile` points attribute resolution at an arbitrary path,
    and the disposable worktree does NOT close it -- reproduced directly, it
    flipped an ordinary text diff to a binary one, which is exactly the
    corruption the worktree was introduced to prevent."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    redirect = tmp_path / "outside.attributes"
    redirect.write_text("f.txt -diff\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "core.attributesFile", str(redirect)], cwd=repo, check=True
    )

    diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)

    assert "world" in diff, "out-of-tree attributes redirect changed the diff"
    # `--binary` renders a `-diff` path as "GIT binary patch", not the
    # plain-`git diff` "Binary files ... differ" wording.
    assert "GIT binary patch" not in diff


def test_acquisition_still_honours_attributes_committed_at_the_subject(tmp_path: Path):
    """The closure above must not suppress the subject's OWN committed
    attributes -- those are part of the declared subject."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("f.txt -diff\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)

    assert "GIT binary patch" in diff, "committed .gitattributes at the subject was ignored"
    assert "+world" not in diff, "the path should not have diffed as text"
