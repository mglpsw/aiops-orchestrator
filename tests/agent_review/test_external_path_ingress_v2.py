from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agent_review.external_path_ingress_v2 import (
    EXTERNAL_DIRECTORY_UNREADABLE_REASON_V2,
    EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2,
    EXTERNAL_PATH_MISSING_REASON_V2,
    EXTERNAL_PATH_RESOLUTION_FAILED_REASON_V2,
    EXTERNAL_PATH_UNREADABLE_REASON_V2,
    EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
    ExternalPathIngressError,
    validate_external_input_directory_v2,
    validate_external_input_file_v2,
    validate_external_output_path_v2,
)


def _reason(excinfo: pytest.ExceptionInfo[ExternalPathIngressError]) -> str:
    assert str(excinfo.value) == excinfo.value.reason_code
    assert "/" not in str(excinfo.value)
    assert "\\" not in str(excinfo.value)
    return excinfo.value.reason_code


def test_input_file_capability_reads_valid_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "profile.json"
    target.write_text('{"ok":true}', encoding="utf-8")
    capability = validate_external_input_file_v2(target, root=root)
    assert capability.read_text() == '{"ok":true}'
    assert capability.read_bytes() == b'{"ok":true}'


def test_read_bytes_bounded_returns_full_content_within_the_limit(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "artifact.txt"
    target.write_bytes(b"hello")
    capability = validate_external_input_file_v2(target, root=root)
    assert capability.read_bytes_bounded(10) == b"hello"
    assert capability.read_bytes_bounded(5) == b"hello"


def test_read_bytes_bounded_never_reads_past_max_bytes_plus_one(tmp_path: Path) -> None:
    """The mechanical contract of the bounded read: for a file far larger
    than `max_bytes`, at most `max_bytes + 1` bytes are ever pulled off the
    underlying file handle -- this is what makes the read race-free without
    a separate pre-read `stat()` (see #200-G4B post-merge Codex P1)."""

    root = tmp_path / "root"
    root.mkdir()
    target = root / "big.bin"
    target.write_bytes(b"x" * 1_000_000)
    capability = validate_external_input_file_v2(target, root=root)

    result = capability.read_bytes_bounded(10)
    assert len(result) == 11
    assert result == b"x" * 11


def test_missing_path_is_typed_and_path_free(tmp_path: Path) -> None:
    missing = tmp_path / "private-secret-name.json"
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_input_file_v2(missing, root=tmp_path)
    assert _reason(excinfo) == EXTERNAL_PATH_MISSING_REASON_V2


def test_wrong_type_is_typed(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_input_file_v2(directory, root=tmp_path)
    assert _reason(excinfo) == EXTERNAL_PATH_WRONG_TYPE_REASON_V2


def test_file_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_input_file_v2(outside, root=root)
    assert _reason(excinfo) == EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2


def test_symlink_loop_is_resolution_refusal_not_traceback(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    a = root / "a"
    b = root / "b"
    a.symlink_to(b.name)
    b.symlink_to(a.name)
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_input_file_v2(a, root=root)
    assert _reason(excinfo) == EXTERNAL_PATH_RESOLUTION_FAILED_REASON_V2


def test_overlong_path_oserror_is_resolution_refusal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    overlong = root / ("x" * 10000)
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_input_file_v2(overlong, root=root)
    assert _reason(excinfo) in {
        EXTERNAL_PATH_RESOLUTION_FAILED_REASON_V2,
        EXTERNAL_PATH_UNREADABLE_REASON_V2,
    }


def test_permission_error_from_actual_read_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "profile.json"
    target.write_text("payload", encoding="utf-8")
    capability = validate_external_input_file_v2(target, root=root)
    original_read_bytes = Path.read_bytes

    def deny_read_bytes(self: Path):
        if self == target.resolve():
            raise PermissionError("must-not-leak-path")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", deny_read_bytes)
    with pytest.raises(ExternalPathIngressError) as excinfo:
        capability.read_bytes()
    assert _reason(excinfo) == EXTERNAL_PATH_UNREADABLE_REASON_V2
    assert "must-not-leak" not in str(excinfo.value)


def test_post_seal_programmer_defect_is_not_laundered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "profile.json"
    target.write_text("payload", encoding="utf-8")
    capability = validate_external_input_file_v2(target, root=root)
    original_read_bytes = Path.read_bytes

    def defect(self: Path):
        if self == target.resolve():
            raise AssertionError("programmer defect")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", defect)
    with pytest.raises(AssertionError, match="programmer defect"):
        capability.read_bytes()


def test_capability_rechecks_containment_at_read_to_catch_replacement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    raw = root / "response.json"
    raw.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    capability = validate_external_input_file_v2(raw, root=root)
    raw.unlink()
    raw.symlink_to(outside)
    with pytest.raises(ExternalPathIngressError) as excinfo:
        capability.read_bytes()
    assert _reason(excinfo) == EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2


def test_input_directory_enumerates_as_file_capabilities(tmp_path: Path) -> None:
    root = tmp_path / "responses"
    root.mkdir()
    (root / "b.json").write_text("b", encoding="utf-8")
    (root / "a.json").write_text("a", encoding="utf-8")
    (root / "subdir").mkdir()
    capability = validate_external_input_directory_v2(root, root=tmp_path)
    entries = capability.iter_input_files()
    assert [entry.resolved_path.name for entry in entries] == ["a.json", "b.json"]
    assert [entry.read_text() for entry in entries] == ["a", "b"]


def test_directory_enumeration_external_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "responses"
    directory.mkdir()
    capability = validate_external_input_directory_v2(directory, root=tmp_path)
    original_iterdir = Path.iterdir

    def deny_iterdir(self: Path):
        if self == directory.resolve():
            raise PermissionError("private-directory")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", deny_iterdir)
    with pytest.raises(ExternalPathIngressError) as excinfo:
        capability.iter_input_files()
    assert _reason(excinfo) == EXTERNAL_DIRECTORY_UNREADABLE_REASON_V2


def test_output_path_may_not_exist_but_parent_must_be_valid(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    output = root / "result.json"
    capability = validate_external_output_path_v2(output, root=root)
    with capability.open_binary_exclusive() as handle:
        handle.write(b"result")
    assert output.read_bytes() == b"result"


def test_output_parent_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    output = tmp_path / "outside" / "result.json"
    with pytest.raises(ExternalPathIngressError) as excinfo:
        validate_external_output_path_v2(output, root=root)
    assert _reason(excinfo) == EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2


def test_no_broad_exception_handler_in_authority() -> None:
    source = Path("app/agent_review/external_path_ingress_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            forbidden.append(node.lineno)
            continue
        names: set[str] = set()
        targets = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
        if names & {"Exception", "BaseException"}:
            forbidden.append(node.lineno)
    assert forbidden == []
