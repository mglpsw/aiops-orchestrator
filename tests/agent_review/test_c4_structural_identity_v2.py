"""#333 -- full Git tree structural equality (contract A, symlink rule A2).

Contract Q: under the quiescence precondition (nothing mutates the subject
during verification), `G == M` -- the directory graph at `subject_root`,
walked without following symlinks, is node-for-node equal to `commit_sha`'s
raw Git tree. Every fixture here is a STABLE subject. Countermodels
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


def _traversal_live_fd_delta(p: _Plumbing, tmp_path: Path, depth: int) -> int:
    """Peak live descriptors ABOVE the pre-call baseline, sampled only while
    the SUBJECT TRAVERSAL runs (both structural observations and every leaf
    comparison), after each ``os.open`` / ``os.dup`` / ``os.scandir`` /
    ``os.readlink``. Deliberately NOT the whole verifier call: the git-side
    pipes and spool dominate that peak and do not depend on the subject's
    depth (measured on #352: whole call +13, traversal +4, at every depth
    5..99)."""
    tree = p.tree(("100644", "blob", p.blob(CODE), b"leaf.py"))
    for _ in range(depth):
        tree = p.tree(("040000", "tree", tree, b"d"))
    c = p.commit(tree)
    root = _materialised(p, c, tmp_path / f"s{depth}")
    live = lambda: len(os.listdir("/proc/self/fd"))  # noqa: E731
    state = {"inside": 0, "peak": 0}

    def sampled(fn):
        def inner(*a, **k):
            out = fn(*a, **k)
            if state["inside"]:
                state["peak"] = max(state["peak"], live())
            return out
        return inner

    def traversal(fn):
        def inner(*a, **k):
            state["inside"] += 1
            try:
                return fn(*a, **k)
            finally:
                state["inside"] -= 1
        return inner

    from unittest.mock import patch
    baseline = live()
    with patch.object(ident.os, "open", sampled(os.open)), patch.object(ident.os, "dup", sampled(os.dup)), \
            patch.object(ident.os, "scandir", sampled(os.scandir)), patch.object(ident.os, "readlink", sampled(os.readlink)), \
            patch.object(ident, "_observe_subject_graph_v2", traversal(ident._observe_subject_graph_v2)), \
            patch.object(ident, "_compare_regular_leaf_v2", traversal(ident._compare_regular_leaf_v2)), \
            patch.object(ident, "_compare_symlink_leaf_v2", traversal(ident._compare_symlink_leaf_v2)):
        _verify(p, c, root)
    assert live() == baseline, "descriptors leaked past return"
    return state["peak"] - baseline


def test_traversal_live_descriptors_are_independent_of_tree_depth(plumbing: _Plumbing, tmp_path: Path) -> None:
    """Property: StructuralTraversalLiveFDs = O(1) with respect to tree depth.
    Discriminated by comparing the SAME measurement at depth 5 and depth 60
    (an O(depth) descriptor stack or a per-directory leak differs by ~55).
    The numeric ceiling is a regression guardrail on that same traversal-only
    measurement, not a bound on the whole verifier's descriptors."""
    shallow = _traversal_live_fd_delta(plumbing, tmp_path, 5)
    deep = _traversal_live_fd_delta(plumbing, tmp_path, 60)
    assert deep == shallow, f"traversal descriptors depend on depth (depth 5: {shallow}, depth 60: {deep})"
    assert deep < 12, f"traversal-only live-descriptor guardrail exceeded ({deep})"


# -- #321 mechanism discriminators (NOT part of contract Q's truth-maker) --------
#
# The static FIFO/symlink cases above are caught by the structural walk before
# any leaf is opened. These tests substitute the node between the structural
# comparison and the leaf read -- #321's historical falsifiers -- so the
# descriptor-level gates in the read path (`O_NOFOLLOW`, `O_NONBLOCK` + `fstat`
# `S_ISREG`, no-follow re-acquisition) are the only thing standing. They pin
# those gates (typed refusal, never a hang, never a followed symlink). They do
# NOT claim resistance to a concurrent writer: such a writer is outside Q, and
# other same-privilege mutations (R1/R3) are accepted by design of the contract.


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


# -- #352 F1 history (not a Q qualification test) --------------------------------
#
# A node added while leaves were being read, and a node added during a second
# ("final") walk (Codex R1), both require a concurrent same-privilege writer.
# That writer violates contract Q's quiescence precondition (the module's
# `host_arbitrary_code_attacker` boundary), so no test here asserts refusal of
# it, and none asserts that such an attack is safe. The witnesses are preserved
# as forensic evidence in PR #352's records; resistance belongs to #301.


# -- #352 M14: executable semantics are Git's own ---------------------------------


def _git_recorded_mode(tmp_path: Path, file_mode: int) -> str:
    """The mode Git itself records for a regular file with `file_mode` (oracle
    independent of the verifier: `core.fileMode=true`, `update-index --add`)."""
    repo = tmp_path / f"oracle_{file_mode:o}"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.fileMode", "true")
    (repo / "f").write_bytes(CODE)
    os.chmod(repo / "f", file_mode)
    _git(repo, "update-index", "--add", "f")
    return _git(repo, "ls-files", "-s", "f").split()[0]


@pytest.mark.parametrize("file_mode", [0o644, 0o645, 0o654, 0o744, 0o655, 0o711, 0o755])
def test_executable_semantics_agree_with_the_mode_git_itself_records(
    plumbing: _Plumbing, tmp_path: Path, file_mode: int
) -> None:
    """For a regular file carrying `file_mode`, the verifier accepts it against
    a 100644 blob iff Git would record it as 100644, and against a 100755 blob
    iff Git would record it as 100755 (group/other execute bits are not Git's
    executable bit)."""
    recorded = _git_recorded_mode(tmp_path, file_mode)
    assert recorded in ("100644", "100755")
    for committed_mode in ("100644", "100755"):
        c = plumbing.commit(plumbing.tree((committed_mode, "blob", plumbing.blob(CODE), b"f")))
        root = _subject(tmp_path / f"s_{file_mode:o}_{committed_mode}", {"f": CODE})
        os.chmod(root / "f", file_mode)
        if committed_mode == recorded:
            _verify(plumbing, c, root)
        else:
            assert _refusal(plumbing, c, root) == IDENTITY_MODE_MISMATCH_REASON_V2


# -- #352 M17/M18/M21: budget edges and admission parity with C3 ---------------


def _chain_of_empty_trees(p: _Plumbing, depth: int) -> str:
    tree = p.empty_tree
    for _ in range(depth):
        tree = p.tree(("040000", "tree", tree, b"d"))
    return p.commit(tree)


def test_deepest_tree_c3_admits_verifies_and_one_level_deeper_is_unrepresentable(
    plumbing: _Plumbing, tmp_path: Path
) -> None:
    """Real constants, no monkeypatch. C3 admits a directory at depth 100
    (child depth <= 100); the verifier's observed-depth budget must admit the
    exact subject C3 materialises for it. One level deeper, C3 refuses the
    COMMIT as unrepresentable and the verifier reports exactly that."""
    from app.agent_review.git_commit_subject_v2 import (
        SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
        SubjectMaterialisationError,
    )

    at_bound = _chain_of_empty_trees(plumbing, ident.MAX_OBSERVED_DEPTH_V2)
    root = _materialised(plumbing, at_bound, tmp_path / "at_bound")
    _verify(plumbing, at_bound, root)

    deeper_subject = root / "/".join(["d"] * ident.MAX_OBSERVED_DEPTH_V2) / "d"
    deeper_subject.mkdir()  # the subject, not the commit, goes one level deeper
    assert _refusal(plumbing, at_bound, root) == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2

    beyond = _chain_of_empty_trees(plumbing, ident.MAX_OBSERVED_DEPTH_V2 + 1)
    with pytest.raises(SubjectMaterialisationError) as c3:
        materialise_commit_subject_v2(repo_root=plumbing.repo, ref=beyond, destination=tmp_path / "beyond")
    assert c3.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert _refusal(plumbing, beyond, _subject(tmp_path / "empty_subject")) == ident.IDENTITY_TREE_UNREPRESENTABLE_REASON_V2


def test_tree_c3_refuses_as_unrepresentable_keeps_its_own_reason(plumbing: _Plumbing, tmp_path: Path) -> None:
    """A committed name longer than NAME_MAX: C3 cannot represent it, and the
    verifier must say so -- not collapse it into `identity_tree_unreadable`."""
    from app.agent_review.git_commit_subject_v2 import (
        SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
        SubjectMaterialisationError,
    )

    c = plumbing.commit(plumbing.tree(("100644", "blob", plumbing.blob(CODE), b"n" * 256)))
    with pytest.raises(SubjectMaterialisationError) as c3:
        materialise_commit_subject_v2(repo_root=plumbing.repo, ref=c, destination=tmp_path / "m")
    assert c3.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert _refusal(plumbing, c, _subject(tmp_path / "s", {"main.py": CODE})) == ident.IDENTITY_TREE_UNREPRESENTABLE_REASON_V2


@pytest.mark.parametrize("budget", [13, 12], ids=["exactly-at-bound", "one-below"])
def test_observed_node_budget_has_the_same_edge_as_c3_admission(
    plumbing: _Plumbing, tmp_path: Path, monkeypatch, budget: int
) -> None:
    """Admission parity at the edge: a 13-entry tree is admitted by C3's
    structural enumeration iff its entry budget is >= 13; the verifier's
    observed-node budget must draw the edge in the same place for the subject
    C3 materialises (so "exactly at the bound" is admitted by both)."""
    from app.agent_review.git_commit_subject_v2 import SubjectMaterialisationError, _build_canonical_trie_hierarchical

    c = plumbing.commit(plumbing.tree(
        ("100644", "blob", plumbing.blob(CODE), b"main.py"),
        *[("040000", "tree", plumbing.empty_tree, f"d{i:02d}".encode()) for i in range(12)],
    ))
    root = _materialised(plumbing, c, tmp_path / "s")
    root_tree = _git(plumbing.repo, "rev-parse", f"{c}^{{tree}}")
    try:
        _build_canonical_trie_hierarchical(repo_root=plumbing.repo, root_tree_oid=root_tree, max_expanded_entries=budget)
        c3_admits = True
    except SubjectMaterialisationError:
        c3_admits = False
    monkeypatch.setattr(ident, "MAX_OBSERVED_NODES_V2", budget)
    try:
        _verify(plumbing, c, root)
        verifier_admits = True
    except ExecutedSourceIdentityError as exc:
        assert exc.reason_code == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2
        verifier_admits = False
    assert verifier_admits == c3_admits == (budget >= 13)


# -- #352 F3: entries are streamed; one huge directory is not listed whole -----


def test_oversized_directory_is_refused_without_enumerating_all_of_it(
    plumbing: _Plumbing, tmp_path: Path, monkeypatch
) -> None:
    """The observed-node budget bounds how many names the walk pulls from the
    filesystem, not only how many it keeps: a directory holding far more
    entries than the budget is refused after at most budget + 1 names.
    Counted at the enumeration primitive (both `os.scandir` and `os.listdir`
    on a descriptor), only while the structural walk runs."""
    c = _commit_main_only(plumbing)
    root = _subject(tmp_path / "s", {"main.py": CODE}, dirs=("big",))
    for i in range(500):
        (root / "big" / f"f{i:03d}").write_bytes(b"")
    budget = 50
    monkeypatch.setattr(ident, "MAX_OBSERVED_NODES_V2", budget)
    drawn = {"names": 0, "inside": 0}
    real_scandir, real_listdir, real_observe = os.scandir, os.listdir, ident._observe_subject_graph_v2

    class _Counting:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __enter__(self):
            return self

        def __exit__(self, *exc_info) -> None:
            self._inner.close()

        def __iter__(self):
            for entry in self._inner:
                drawn["names"] += 1
                yield entry

        def close(self) -> None:
            self._inner.close()

    def scandir(target="."):
        it = real_scandir(target)
        return _Counting(it) if drawn["inside"] and isinstance(target, int) else it

    def listdir(target="."):
        names = real_listdir(target)
        if drawn["inside"] and isinstance(target, int):
            drawn["names"] += len(names)
        return names

    def observe(root_fd):
        drawn["inside"] += 1
        try:
            return real_observe(root_fd)
        finally:
            drawn["inside"] -= 1

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(ident, "_observe_subject_graph_v2", observe)
    assert _refusal(plumbing, c, root) == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2
    assert drawn["names"] <= budget + 1, f"walk pulled {drawn['names']} names for a budget of {budget}"


# -- #352 R2: the depth budget applies to every node kind, before kind dispatch --


def _commit_with_leaf_at_depth(p: _Plumbing, depth: int, entry: tuple[str, str, str, bytes]) -> str:
    tree = p.tree(entry)
    for _ in range(depth - 1):
        tree = p.tree(("040000", "tree", tree, b"d"))
    return p.commit(tree)


@pytest.mark.parametrize("kind", ["regular", "symlink"])
def test_leaf_at_the_depth_bound_is_admitted_as_c3_admits_it(plumbing: _Plumbing, tmp_path: Path, kind: str) -> None:
    """C3 admits a leaf at child depth 100; the verifier must too (depth 100
    directory is covered by the deepest-tree test above)."""
    entry = ("100644", "blob", plumbing.blob(CODE), b"leaf.py") if kind == "regular" else \
        ("120000", "blob", plumbing.blob(b"leaf.py"), b"link")
    c = _commit_with_leaf_at_depth(plumbing, ident.MAX_OBSERVED_DEPTH_V2, entry)
    _verify(plumbing, c, _materialised(plumbing, c, tmp_path / "s"))


@pytest.mark.parametrize("kind", ["directory", "regular", "symlink", "fifo"])
def test_any_node_beyond_the_depth_bound_is_a_budget_refusal(plumbing: _Plumbing, tmp_path: Path, kind: str) -> None:
    """A node of ANY kind one level below the deepest directory C3 can admit
    is refused with the typed budget reason -- not as an extra file/node that
    happened to be classified first. The FIFO is never opened (no hang)."""
    c = _chain_of_empty_trees(plumbing, ident.MAX_OBSERVED_DEPTH_V2)
    root = _materialised(plumbing, c, tmp_path / "s")
    node = root / "/".join(["d"] * ident.MAX_OBSERVED_DEPTH_V2) / "n"
    {"directory": lambda: node.mkdir(), "regular": lambda: node.write_bytes(b""),
     "symlink": lambda: os.symlink("x", node), "fifo": lambda: os.mkfifo(node)}[kind]()
    assert _refusal(plumbing, c, root) == ident.IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2


# -- #352 Q review (5327260045): bounded expected side and cleanup ----------------


def test_success_path_never_produces_the_flattened_leaf_listing(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    """`git ls-tree -r` flattens every repeated path prefix and is not bounded
    by C3's budgets (review finding A: ~3x the flattened path bytes of a tree
    C3 admits). On a tree C3 admits, the verifier must not run it at all; it
    is consulted only on C3's refusal path, to keep the legacy gitlink / `..`
    reason codes."""
    from app.agent_review import bounded_git_v2, git_commit_subject_v2
    seen: list[list[str]] = []
    real_run, real_open = bounded_git_v2.run_bounded_git_v2, bounded_git_v2.open_bounded_git_subprocess_v2

    def run(argv, *a, **k):
        seen.append(list(argv))
        return real_run(argv, *a, **k)

    def open_(argv, *a, **k):
        seen.append(list(argv))
        return real_open(argv, *a, **k)

    monkeypatch.setattr(git_commit_subject_v2, "run_bounded_git_v2", run)
    monkeypatch.setattr(git_commit_subject_v2, "open_bounded_git_subprocess_v2", open_)
    monkeypatch.setattr(ident, "open_bounded_git_subprocess_v2", open_)
    c = _commit_with_nested_empty(plumbing)
    _verify(plumbing, c, _materialised(plumbing, c, tmp_path / "s"))
    assert not [argv for argv in seen if argv[:1] == ["ls-tree"] and "-r" in argv], seen


def test_expected_blob_is_compared_without_holding_it_whole(plumbing: _Plumbing, tmp_path: Path, monkeypatch) -> None:
    """Review finding B: the expected side is streamed from the carrier spool;
    the leaf comparison's heap peak is independent of the blob size."""
    import tracemalloc
    big = os.urandom(1 << 20) * 8
    c = plumbing.commit(plumbing.tree(("100644", "blob", plumbing.blob(big), b"big.bin")))
    root = _materialised(plumbing, c, tmp_path / "s")
    del big
    real = ident._compare_regular_leaf_v2
    peaks: list[int] = []

    def measured(*a, **k):
        tracemalloc.start()
        try:
            return real(*a, **k)
        finally:
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()

    monkeypatch.setattr(ident, "_compare_regular_leaf_v2", measured)
    _verify(plumbing, c, root)
    assert peaks and max(peaks) < (1 << 20), f"leaf comparison held {max(peaks)} bytes for an 8 MiB blob"


def test_blob_carrier_is_closed_even_if_closing_the_root_descriptor_is_interrupted(
    plumbing: _Plumbing, tmp_path: Path, monkeypatch
) -> None:
    """Review finding C: neither cleanup step may skip the other."""
    c = _commit_main_only(plumbing)
    root = _materialised(plumbing, c, tmp_path / "s")
    carriers, root_fds = [], []
    real_read, real_acquire, real_close = ident.read_commit_blobs_v2, ident._acquire_subject_root_fd_v2, os.close

    def read(**k):
        carrier = real_read(**k)
        carriers.append(carrier)
        return carrier

    def acquire(path):
        fd = real_acquire(path)
        root_fds.append(fd)
        return fd

    def close(fd):
        real_close(fd)
        if root_fds and fd == root_fds[0]:
            raise KeyboardInterrupt("interrupted while closing the root descriptor")

    monkeypatch.setattr(ident, "read_commit_blobs_v2", read)
    monkeypatch.setattr(ident, "_acquire_subject_root_fd_v2", acquire)
    monkeypatch.setattr(ident.os, "close", close)
    with pytest.raises(KeyboardInterrupt):
        _verify(plumbing, c, root)
    monkeypatch.setattr(ident.os, "close", real_close)
    assert carriers and carriers[0]._closed
