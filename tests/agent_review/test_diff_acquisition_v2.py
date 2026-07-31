from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_review.diff_acquisition_v2 import (
    DIFF_UNREADABLE_REASON_V2,
    INVALID_REF_REASON_V2,
    DiffAcquisitionError,
    acquire_diff_v2,
    parse_unified_diff,
    validate_diff_completeness_v2,
)


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


def test_parse_distinct_undecodable_octal_paths_do_not_collide() -> None:
    """Arbitrary bytes are valid in a git filename. Decoding an
    undecodable octal-escaped byte with errors="replace" would map every
    such byte to the same U+FFFD character, conflating two genuinely
    different paths (e.g. one byte 0o200, the other 0o201) under one
    string -- corrupting completeness checks and fragment-identity
    hashing that key off path. Must stay distinct."""

    diff_text_a = (
        'diff --git "a/bad\\200.py" "b/bad\\200.py"\n'
        "index abc..def 100644\n"
        '--- "a/bad\\200.py"\n'
        '+++ "b/bad\\200.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    diff_text_b = (
        'diff --git "a/bad\\201.py" "b/bad\\201.py"\n'
        "index abc..def 100644\n"
        '--- "a/bad\\201.py"\n'
        '+++ "b/bad\\201.py"\n'
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    path_a = parse_unified_diff(diff_text_a)[0].path
    path_b = parse_unified_diff(diff_text_b)[0].path
    assert path_a != path_b


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
