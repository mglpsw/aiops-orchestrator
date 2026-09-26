"""#333 -- full Git tree structural equality (contract A, symlink rule A2).

`G == M`: the directory graph at `subject_root`, walked without following
symlinks, is node-for-node equal to `commit_sha`'s raw Git tree. Countermodels
CM-333-01..05 were reproduced on master `9a5cf35b` (issue comment 5844528708)
before this file existed; the leaf-only verifier accepted every one of them.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review import commit_derived_execution_identity_v2 as ident
from app.agent_review.commit_derived_execution_identity_v2 import (
    IDENTITY_CONTENT_MISMATCH_REASON_V2,
    IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2,
    IDENTITY_MISSING_TRACKED_FILE_REASON_V2,
    IDENTITY_MODE_MISMATCH_REASON_V2,
    IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2,
    IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2,
    IDENTITY_SYMLINKED_DIRECTORY_REASON_V2,
    ExecutedSourceIdentityError,
    verify_executed_source_identity_v2,
)
from app.agent_review.git_commit_subject_v2 import (
    list_commit_tree_structure_v2,
    materialise_commit_subject_v2,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# -- Git plumbing fixtures (explicit trees cannot be built with `git add`) -----


def _git(repo: Path, *argv: str, data: bytes | None = None) -> str:
    return subprocess.run(
        ["git", *argv], cwd=repo, input=data, check=True, capture_output=True
    ).stdout.decode().strip()


class _Plumbing:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "t")
        self.empty_tree = _git(repo, "hash-object", "-t", "tree", "-w", "--stdin", data=b"")

    def blob(self, data: bytes) -> str:
        return _git(self.repo, "hash-object", "-w", "--stdin", data=data)

    def tree(self, *entries: tuple[str, str, str, bytes]) -> str:
        """entries: (mode, type, oid, raw_name)."""
        payload = b"".join(f"{m} {t} {o}\t".encode() + n + b"\0" for m, t, o, n in entries)
        return _git(self.repo, "mktree", "-z", data=payload)

    def commit(self, tree_oid: str) -> str:
        return _git(self.repo, "commit-tree", tree_oid, "-m", "c")


CODE = b"print(1)\n"


@pytest.fixture
def plumbing(tmp_path: Path) -> _Plumbing:
    return _Plumbing(tmp_path / "repo")


def _commit_with_explicit_empty(p: _Plumbing) -> str:
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py"),
                           ("040000", "tree", p.empty_tree, b"empty")))


def _commit_with_nested_empty(p: _Plumbing) -> str:
    t_b = p.tree(("040000", "tree", p.empty_tree, b"empty"))
    t_a = p.tree(("040000", "tree", t_b, b"b"))
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py"), ("040000", "tree", t_a, b"a")))


def _commit_main_only(p: _Plumbing) -> str:
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py")))


def _commit_ordinary(p: _Plumbing) -> str:
    pkg = p.tree(("100644", "blob", p.blob(CODE), b"__init__.py"), ("100755", "blob", p.blob(b"#!/bin/sh\n"), b"run.sh"))
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py"), ("040000", "tree", pkg, b"pkg")))


def _commit_symlink_to_dir(p: _Plumbing) -> str:
    pkg = p.tree(("100644", "blob", p.blob(CODE), b"__init__.py"))
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py"), ("040000", "tree", pkg, b"pkg"),
                           ("120000", "blob", p.blob(b"pkg"), b"link")))


def _commit_dangling_symlink(p: _Plumbing) -> str:
    return p.commit(p.tree(("100644", "blob", p.blob(CODE), b"main.py"), ("120000", "blob", p.blob(b"nowhere"), b"dangling")))


def _subject(root: Path, files: dict[str, bytes] = {}, dirs: tuple[str, ...] = (), links: dict[str, str] = {}) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel, target in links.items():
        os.symlink(target, root / rel)
    return root


def _verify(p: _Plumbing, commit: str, root: Path):
    return verify_executed_source_identity_v2(repo_root=p.repo, commit_sha=commit, subject_root=root, loaded_module_paths=())


def _refusal(p: _Plumbing, commit: str, root: Path) -> str:
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        _verify(p, commit, root)
    return excinfo.value.reason_code


def _materialised(p: _Plumbing, commit: str, dest: Path) -> Path:
    materialise_commit_subject_v2(repo_root=p.repo, ref=commit, destination=dest)
    return dest


# -- CM-333-01 / 02: explicit empty tree nodes must be present ----------------


def test_cm_333_01_explicit_empty_tree_omitted_is_refused(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_with_explicit_empty(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE})) == ident.IDENTITY_MISSING_TREE_NODE_REASON_V2


def test_cm_333_01_control_explicit_empty_tree_present_passes(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_with_explicit_empty(plumbing)
    _verify(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE}, dirs=("empty",)))


@pytest.mark.parametrize("dirs", [("a/b",), ("a",), ()], ids=["last-node-missing", "only-a", "all-absent"])
def test_cm_333_02_nested_empty_tree_degradations_are_refused(plumbing: _Plumbing, tmp_path: Path, dirs) -> None:
    c = _commit_with_nested_empty(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE}, dirs=dirs)) == ident.IDENTITY_MISSING_TREE_NODE_REASON_V2


def test_cm_333_02_control_nested_empty_tree_present_passes(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_with_nested_empty(plumbing)
    _verify(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE}, dirs=("a/b/empty",)))


# -- CM-333-03: extra nodes must not be invisible ------------------------------


@pytest.mark.parametrize("dirs", [("empty",), ("x/y/z",)], ids=["cm-03-extra-empty", "cm-03b-extra-nested"])
def test_cm_333_03_extra_directory_is_refused(plumbing: _Plumbing, tmp_path: Path, dirs) -> None:
    c = _commit_main_only(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE}, dirs=dirs)) == ident.IDENTITY_EXTRA_UNTRACKED_NODE_REASON_V2


def test_extra_regular_file_keeps_existing_reason(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE, "extra.py": b"x"})) == IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2


def test_missing_regular_file_keeps_existing_reason(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_ordinary(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE, "pkg/run.sh": b"#!/bin/sh\n"})) == IDENTITY_MISSING_TRACKED_FILE_REASON_V2


# -- A2: symlinks are node type + target bytes, never followed -----------------


def test_cm_333_04_committed_symlink_to_directory_is_a_positive_control(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_symlink_to_dir(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    assert os.readlink(root / "link") == "pkg" and (root / "link").is_dir()  # it really resolves to a directory
    identity = _verify(plumbing, c, root)
    assert identity.commit_sha == c


def test_cm_333_05_dangling_symlink_leaf_is_a_positive_control(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_dangling_symlink(plumbing)
    _verify(plumbing, c, _materialised(plumbing, c, tmp_path / "s"))


def test_symlink_target_is_compared_as_raw_bytes(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = plumbing.commit(plumbing.tree(("120000", "blob", plumbing.blob(b"t\xff\xfe"), b"lnk")))
    root = tmp_path / "s"
    root.mkdir()
    os.symlink(b"t\xff\xfe", os.fsencode(root / "lnk"))
    _verify(plumbing, c, root)
    bad = tmp_path / "bad"
    bad.mkdir()
    os.symlink(b"t\xff\xfd", os.fsencode(bad / "lnk"))
    assert _refusal(plumbing, c, bad) == IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2


def test_directory_substituted_by_symlink_is_refused_even_if_target_is_byte_identical(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_ordinary(plumbing)
    real = _materialised(plumbing, c, tmp_path / "real")
    root = _subject(tmp_path / "s", {"main.py": CODE})
    os.symlink(real / "pkg", root / "pkg")  # Git says tree; the subject offers a symlink to an identical directory
    assert _refusal(plumbing, c, root) == IDENTITY_SYMLINKED_DIRECTORY_REASON_V2


def test_regular_file_substituted_by_symlink_is_a_type_mismatch(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    root = tmp_path / "s"
    root.mkdir()
    (tmp_path / "elsewhere.py").write_bytes(CODE)
    os.symlink(tmp_path / "elsewhere.py", root / "main.py")
    assert _refusal(plumbing, c, root) == ident.IDENTITY_NODE_TYPE_MISMATCH_REASON_V2


def test_symlink_leaf_substituted_by_directory_is_a_type_mismatch(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_symlink_to_dir(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    os.unlink(root / "link")
    (root / "link").mkdir()
    assert _refusal(plumbing, c, root) == ident.IDENTITY_NODE_TYPE_MISMATCH_REASON_V2


# -- #321 class: special files where Git declares a regular file ---------------

_FIFO_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from app.agent_review.commit_derived_execution_identity_v2 import verify_executed_source_identity_v2, ExecutedSourceIdentityError
try:
    verify_executed_source_identity_v2(repo_root=Path(sys.argv[2]), commit_sha=sys.argv[3], subject_root=Path(sys.argv[4]), loaded_module_paths=())
    print("SUCCESS")
except ExecutedSourceIdentityError as exc:
    print("REFUSED:" + exc.reason_code)
"""


def test_fifo_in_place_of_regular_file_is_refused_without_hanging(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    root = tmp_path / "s"
    root.mkdir()
    os.mkfifo(root / "main.py")  # nobody will ever write to it: a blocking open/read would hang forever
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _FIFO_PROBE, str(REPO_ROOT), str(plumbing.repo), c, str(root)],
            capture_output=True, text=True, timeout=20, cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("verifier blocked on a FIFO where Git declares a regular file (#321 class)")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"REFUSED:{ident.IDENTITY_NODE_TYPE_MISMATCH_REASON_V2}"


def test_extra_special_file_is_an_extra_node(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE})
    os.mkfifo(root / "pipe")
    assert _refusal(plumbing, c, root) == ident.IDENTITY_EXTRA_UNTRACKED_NODE_REASON_V2


# -- root authority ------------------------------------------------------------


def test_subject_root_locator_that_is_a_symlink_is_refused(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    real = _materialised(plumbing, c, tmp_path / "real")
    _verify(plumbing, c, real)  # control: the real directory passes
    alias = tmp_path / "alias"
    os.symlink(real, alias)
    assert _refusal(plumbing, c, alias) == IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2


# -- mode semantics from st_mode, not process access ---------------------------


def test_executable_semantics_come_from_st_mode(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_ordinary(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    _verify(plumbing, c, root)
    os.chmod(root / "pkg" / "run.sh", 0o644)
    assert _refusal(plumbing, c, root) == IDENTITY_MODE_MISMATCH_REASON_V2
    os.chmod(root / "pkg" / "run.sh", 0o755)
    os.chmod(root / "main.py", 0o755)
    assert _refusal(plumbing, c, root) == IDENTITY_MODE_MISMATCH_REASON_V2


def test_content_mismatch_keeps_existing_reason(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": b"print(2)\n"})) == IDENTITY_CONTENT_MISMATCH_REASON_V2


# -- C3 parity: producer semantics == verifier semantics -----------------------


def _observed_graph(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        st = os.lstat(p)
        kind = "symlink" if stat.S_ISLNK(st.st_mode) else "tree" if stat.S_ISDIR(st.st_mode) else "blob"
        out[str(p.relative_to(root))] = kind
    return out


@pytest.mark.parametrize(
    "factory",
    [_commit_ordinary, _commit_with_explicit_empty, _commit_with_nested_empty, _commit_symlink_to_dir, _commit_dangling_symlink],
    ids=["ordinary+executable", "explicit-empty", "nested-empty", "symlink-to-dir", "dangling-symlink"],
)
def test_c3_materialisation_matches_verifier_and_structural_enumeration(plumbing: _Plumbing, tmp_path: Path, factory) -> None:
    c = factory(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    expected = {
        e.path: ("symlink" if e.mode == "120000" else e.object_type)
        for e in list_commit_tree_structure_v2(repo_root=plumbing.repo, commit_sha=c)
    }
    assert _observed_graph(root) == expected  # equality with the producer's own structural enumeration
    assert _verify(plumbing, c, root).commit_sha == c


# -- resource bounds -----------------------------------------------------------


def test_observed_structure_budget_is_typed_and_bounded(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE}, dirs=tuple(f"d{i}" for i in range(12)))
    monkeypatch.setattr(ident, "MAX_OBSERVED_NODES_V2", 10)
    assert _refusal(plumbing, c, root) == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2


def test_observed_depth_budget_is_typed_and_bounded(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE}, dirs=("/".join(["d"] * 8),))
    monkeypatch.setattr(ident, "MAX_OBSERVED_DEPTH_V2", 5)
    assert _refusal(plumbing, c, root) == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2


def test_live_descriptors_do_not_grow_with_depth(plumbing: _Plumbing, tmp_path: Path) -> None:
    depth = 60
    parts = ["d"] * depth
    p = plumbing
    tree = p.tree(("100644", "blob", p.blob(CODE), b"leaf.py"))
    for _ in range(depth):
        tree = p.tree(("040000", "tree", tree, b"d"))
    c = p.commit(tree)
    root = _materialised(p, c, tmp_path / "s")
    peak = {"n": 0}
    real_open = os.open

    def counting_open(*a, **k):
        fd = real_open(*a, **k)
        peak["n"] = max(peak["n"], len(os.listdir("/proc/self/fd")))
        return fd

    baseline = len(os.listdir("/proc/self/fd"))
    from unittest.mock import patch
    with patch.object(ident.os, "open", counting_open):
        _verify(p, c, root)
    assert peak["n"] - baseline < 12, f"live descriptors grew with depth ({peak['n'] - baseline})"


# -- race-shaped discriminators: the node changes AFTER the structural walk ----
#
# The static FIFO/symlink cases above are caught by the structural walk before
# any leaf is opened. These tests substitute the node between the structural
# comparison and the leaf read, so the descriptor-level gates in the read
# path (`O_NOFOLLOW`, `O_NONBLOCK` + `fstat` `S_ISREG`, no-follow
# re-acquisition) are the ONLY thing standing -- the `#321` shape.


def _swap_after_structure(monkeypatch, mutate) -> None:
    real = ident._compare_structure_v2

    def compare_then_mutate(expected, observed):
        real(expected, observed)
        mutate()

    monkeypatch.setattr(ident, "_compare_structure_v2", compare_then_mutate)


_RACE_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from unittest.mock import patch
from app.agent_review import commit_derived_execution_identity_v2 as ident
root = Path(sys.argv[4])
real = ident._compare_structure_v2
def compare_then_swap(expected, observed):
    real(expected, observed)
    os.unlink(root / "main.py")
    os.mkfifo(root / "main.py")
with patch.object(ident, "_compare_structure_v2", compare_then_swap):
    try:
        ident.verify_executed_source_identity_v2(repo_root=Path(sys.argv[2]), commit_sha=sys.argv[3], subject_root=root, loaded_module_paths=())
        print("SUCCESS")
    except ident.ExecutedSourceIdentityError as exc:
        print("REFUSED:" + exc.reason_code)
"""


def test_regular_file_swapped_for_fifo_after_the_walk_is_refused_on_the_descriptor(plumbing: _Plumbing, tmp_path: Path) -> None:
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE})
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _RACE_PROBE, str(REPO_ROOT), str(plumbing.repo), c, str(root)],
            capture_output=True, text=True, timeout=20, cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("leaf read blocked on a FIFO swapped in after the structural walk (#321 class)")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"REFUSED:{ident.IDENTITY_NODE_TYPE_MISMATCH_REASON_V2}"


def test_regular_file_swapped_for_symlink_after_the_walk_is_not_followed(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE})
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_bytes(CODE)  # byte-identical: only no-follow on the open can tell

    def swap() -> None:
        os.unlink(root / "main.py")
        os.symlink(elsewhere, root / "main.py")

    _swap_after_structure(monkeypatch, swap)
    assert _refusal(plumbing, c, root) in (ident.IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2, ident.IDENTITY_NODE_TYPE_MISMATCH_REASON_V2)


def test_directory_swapped_for_symlink_after_the_walk_is_not_followed_on_reacquisition(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    c = _commit_ordinary(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    shadow = tmp_path / "shadow"
    _materialised(plumbing, c, shadow)  # byte-identical directory elsewhere

    def swap() -> None:
        os.rename(root / "pkg", tmp_path / "moved_away")
        os.symlink(shadow / "pkg", root / "pkg")

    _swap_after_structure(monkeypatch, swap)
    assert _refusal(plumbing, c, root) == ident.IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2
