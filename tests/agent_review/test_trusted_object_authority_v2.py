"""`#200-G1C` (issue #303) -- isolated immutable Git object-store authority.

This is the mandatory falsifier corpus named in #303: every historical
mechanism that refuted PR #302's four correction rounds (lazy-fetch/
partial-clone contamination) and PR #306's withdrawn S4 attempt (silent
false-negative ancestry determination), ported forward as regression tests
against the NEW `trusted_object_authority_v2.py` architecture.

Process discipline for this file (this project's established RED-first
practice, see `test_commit_derived_execution_identity_v2.py`'s own
docstring for the same pattern on the previous round): every test below was
run against `master` BEFORE `trusted_object_authority_v2.py` and the
rewiring of `commit_derived_execution_identity_v2.py` /
`git_commit_subject_v2.py` existed, to confirm each one is a real,
demonstrable gap and not a test that was always going to pass. That RED run
is recorded in the PR body, not repeated here as executable code -- the
corpus that remains is the permanent GREEN regression suite, exactly as the
prior round's file states its own two ported falsifiers stay in the
permanent corpus rather than being thrown away.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import os
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

import pytest

from app.agent_review.commit_derived_execution_identity_v2 import (
    IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2,
    IDENTITY_TREE_UNREADABLE_REASON_V2,
    IDENTITY_UNKNOWN_COMMIT_REASON_V2,
    ExecutedSourceIdentityError,
    authorize_commit_for_execution_v2,
)
from app.agent_review.git_commit_subject_v2 import (
    SubjectMaterialisationError,
    materialise_commit_subject_v2,
    resolve_commit_v2,
)
from app.agent_review.trusted_object_authority_v2 import (
    TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2,
    TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2,
    TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2,
    TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2,
    TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2,
    TrustedObjectAuthorityError,
    TrustedObjectAuthorityV2,
    open_trusted_object_authority_v2,
)

# -- fixtures ------------------------------------------------------------------


def _init_repo(repo: Path, *, branch: str = "main") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "-b", branch, "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return _rev_parse(repo, "HEAD")


def _rev_parse(repo: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _linear_history_fixture(tmp_path: Path, *, seed: str = "") -> tuple[Path, str, str, str]:
    """`c1 -> c2 -> c3` on `main`, a package with one tracked file.

    `seed` is folded into the committed content: two independently-called
    fixtures with identical content, author, and commit message would
    otherwise risk landing on the exact same commit sha if both happen
    within the same one-second timestamp granularity git uses -- content
    identity is what git actually hashes, not call-site identity.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text(f"V = 1\nSEED = {seed!r}\n")
    c1 = _commit_all(repo, f"c1 {seed}")
    (repo / "pkg" / "a.py").write_text(f"V = 2\nSEED = {seed!r}\n")
    c2 = _commit_all(repo, f"c2 {seed}")
    (repo / "pkg" / "a.py").write_text(f"V = 3\nSEED = {seed!r}\n")
    c3 = _commit_all(repo, f"c3 {seed}")
    return repo, c1, c2, c3


def _object_store_snapshot_v2(repo: Path) -> dict[str, str]:
    """Path (relative to `.git/objects`) -> content hash, for every file
    physically present. Used to prove a live repository's object store was
    or was not mutated by a call -- byte-for-byte, not just "count changed".
    """
    objects_dir = repo / ".git" / "objects"
    if not objects_dir.is_dir():
        return {}
    snapshot: dict[str, str] = {}
    for path in objects_dir.rglob("*"):
        if path.is_file():
            snapshot[str(path.relative_to(objects_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _make_promisor_partial_clone_v2(tmp_path: Path, *, remote_name: str = "origin") -> tuple[Path, Path, str, str]:
    """A genuine local partial clone: `source` has real server-side filter
    support enabled, `clone` is missing blob objects on disk and has a
    working promisor remote that WOULD lazily fetch them on demand -- the
    exact live mechanism PR #302's four correction rounds tried and failed
    to make safe by detection. Returns (clone_repo, source_repo, first_sha,
    second_sha).
    """
    source = tmp_path / "source"
    _init_repo(source, branch="main")
    (source / "pkg").mkdir()
    (source / "pkg" / "a.py").write_text("V = 1\n")
    first_sha = _commit_all(source, "first")
    (source / "pkg" / "a.py").write_text("V = 2\n")
    second_sha = _commit_all(source, "second")
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=source, check=True)
    subprocess.run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=source, check=True)

    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            "--filter=blob:none",
            "--origin",
            remote_name,
            f"file://{source}",
            str(clone),
        ],
        check=True,
        capture_output=True,
    )
    promisor_value = subprocess.run(
        ["git", "config", "--get", f"remote.{remote_name}.promisor"],
        cwd=clone,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert promisor_value == "true", "fixture did not produce a genuine promisor remote -- test is not RED-valid"
    return clone, source, first_sha, second_sha


# -- 1-3: lazy fetch / promisor remote -- structurally dissolved -----------------


def test_actual_lazy_fetch_never_mutates_the_live_repositorys_object_store(tmp_path: Path) -> None:
    """The primary falsifier: a genuine partial clone with a working
    promisor remote, materialised through the new architecture. Historical
    root cause (PR #302, all four rounds): the live repository's object
    store gets silently written to by the very git invocation that was
    supposed to be read-only. `#200-G1C`'s property is structural, not
    detected -- the live repository's `.git/objects` must be BYTE-IDENTICAL
    before and after, because no fetch-capable git command is ever run
    against it."""
    clone, _source, first_sha, second_sha = _make_promisor_partial_clone_v2(tmp_path)
    before = _object_store_snapshot_v2(clone)

    destination = tmp_path / "subject"
    destination.mkdir()
    result = materialise_commit_subject_v2(repo_root=clone, ref=second_sha, destination=destination)

    after = _object_store_snapshot_v2(clone)
    assert after == before, "the live (hostile) repository's object store was mutated by a read"
    assert result.commit_sha == second_sha
    assert (destination / "pkg" / "a.py").read_text() == "V = 2\n"

    # Second, independent top-level call (the retry-contamination witness):
    # a completely fresh call must observe the exact same untouched state,
    # not a state quietly improved by whatever the first call did.
    destination_two = tmp_path / "subject_two"
    destination_two.mkdir()
    materialise_commit_subject_v2(repo_root=clone, ref=second_sha, destination=destination_two)
    assert _object_store_snapshot_v2(clone) == before

    # `first_sha`'s blob was never part of the clone's own initial checkout
    # (only the tip, `second_sha`, was) and a `--filter=blob:none` clone
    # otherwise fetches blobs lazily, on demand, from the promisor remote --
    # the exact live mechanism this architecture removes. That blob is
    # therefore genuinely, structurally absent from the private authority:
    # this must REFUSE, never silently fetch it to make the read succeed.
    destination_three = tmp_path / "subject_three"
    destination_three.mkdir()
    with pytest.raises(SubjectMaterialisationError):
        materialise_commit_subject_v2(repo_root=clone, ref=first_sha, destination=destination_three)
    assert _object_store_snapshot_v2(clone) == before, (
        "even a REFUSED read must not mutate the live repository's object store"
    )


def test_non_origin_promisor_remote_never_mutates_the_live_repositorys_object_store(tmp_path: Path) -> None:
    """PR #302 round 1's own gap: a hardcoded `remote.origin.promisor=false`
    override only ever covered a remote literally named `origin`. Proven
    moot here, not patched: the CAS build never reads *any* remote config,
    of any name, because it never copies `config` at all."""
    clone, _source, _first_sha, second_sha = _make_promisor_partial_clone_v2(tmp_path, remote_name="upstream")
    before = _object_store_snapshot_v2(clone)

    destination = tmp_path / "subject"
    destination.mkdir()
    materialise_commit_subject_v2(repo_root=clone, ref=second_sha, destination=destination)

    assert _object_store_snapshot_v2(clone) == before


@pytest.mark.parametrize("promisor_spelling", ["true", "yes", "on", "1"])
def test_every_promisor_boolean_spelling_is_equally_inert(tmp_path: Path, promisor_spelling: str) -> None:
    """PR #302 round 2's gap: only literal `true`/`false` were normalised,
    so `yes`/`on`/`1` slipped through a naive check. Dissolved here rather
    than enumerated: the CAS build never reads `remote.*.promisor` in any
    spelling, because it never reads `config` at all -- every spelling
    behaves identically (inertly)."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    subprocess.run(["git", "remote", "add", "decoy", "file:///nonexistent"], cwd=repo, check=True)
    subprocess.run(["git", "config", "remote.decoy.promisor", promisor_spelling], cwd=repo, check=True)
    before = _object_store_snapshot_v2(repo)

    destination = tmp_path / "subject"
    destination.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=c3, destination=destination)

    assert _object_store_snapshot_v2(repo) == before


def test_partialclonefilter_without_promisor_is_equally_inert(tmp_path: Path) -> None:
    """PR #302 round 2's specific gap: `remote.origin.partialclonefilter`
    alone, with `promisor` entirely unset, was independently sufficient to
    enable a fetch under the old detection-based design. Moot here: neither
    key is ever read, because `config` is never copied into the CAS."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "file:///nonexistent"], cwd=repo, check=True)
    subprocess.run(["git", "config", "remote.origin.partialclonefilter", "blob:none"], cwd=repo, check=True)
    before = _object_store_snapshot_v2(repo)

    destination = tmp_path / "subject"
    destination.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=c3, destination=destination)

    assert _object_store_snapshot_v2(repo) == before


# -- 4: retry / session-restart contamination -------------------------------------


def test_a_rejected_acquisition_followed_by_a_fresh_retry_still_refuses(tmp_path: Path) -> None:
    """The temporal-trust-epoch-mismatch witness (PR #302's hardest-refuted
    finding): a first rejected call must not leave anything a second,
    completely independent, top-level call could treat as pre-existing
    trusted input. There is no session object here for the second call to
    inherit -- each call to `authorize_commit_for_execution_v2` builds and
    tears down its own authority from nothing, so this is tested directly:
    two independent, sequential calls against a repository with a genuinely
    undeterminable ancestry closure (a deleted parent object -- see the
    ancestry section below) both refuse, identically."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)
    _delete_loose_object_v2(repo, c2)

    for _ in range(2):
        with pytest.raises(ExecutedSourceIdentityError) as excinfo:
            authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
        assert excinfo.value.reason_code == IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2


def test_process_restart_does_not_convert_a_rejected_acquisition_into_a_trusted_baseline(
    tmp_path: Path,
) -> None:
    """A first acquisition that is refused for resource reasons (a budget
    rejection standing in for "any rejected acquisition") must not leave
    behind a half-built authority that a later, independent acquisition
    -- simulating a fresh process -- could inherit or be confused by. Each
    call builds an entirely new private directory and removes it on exit
    regardless of outcome; nothing persists between them to inherit."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)

    stray_before = _count_stray_cas_dirs_v2()
    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo, max_object_count=0):
            pass
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2
    assert _count_stray_cas_dirs_v2() == stray_before, "a rejected acquisition leaked its private directory"

    # A fresh, independent, correctly-budgeted acquisition is entirely
    # unaffected by the rejected one that preceded it.
    with open_trusted_object_authority_v2(repo) as authority:
        resolved = resolve_commit_v2(repo_root=authority.trusted_repo_root, ref=c3)
        assert resolved == c3
    assert _count_stray_cas_dirs_v2() == stray_before


def _count_stray_cas_dirs_v2() -> int:
    tmp_root = Path(tempfile.gettempdir())
    return sum(1 for p in tmp_root.glob("agent_review_g1c_cas_v2_*") if p.is_dir())


# -- 5: linked worktree ------------------------------------------------------------


def test_linked_worktree_resolves_the_shared_object_store_not_the_private_worktree_dir(
    tmp_path: Path,
) -> None:
    """PR #302 round 3's own point-fix, carried forward on its insight (not
    its surrounding refuted snapshot mechanism): a linked worktree's
    `.git` is a FILE pointing at a private per-worktree directory holding
    only `HEAD`/`index`/per-worktree refs -- `objects/` lives in the shared
    common directory. Resolving `--git-dir` instead of `--git-common-dir`
    here would silently see an object-less repository."""
    repo, c1, _c2, c3 = _linear_history_fixture(tmp_path)
    worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(worktree), c3],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert (worktree / ".git").is_file(), "fixture did not produce a linked worktree"

    destination = tmp_path / "subject"
    destination.mkdir()
    result = materialise_commit_subject_v2(repo_root=worktree, ref=c1, destination=destination)
    assert result.commit_sha == c1
    assert "V = 1" in (destination / "pkg" / "a.py").read_text()

    authorization = authorize_commit_for_execution_v2(repo_root=worktree, commit_sha=c1, trusted_ref=c3)
    assert authorization.authorized is True


# -- 6: forged / unbound capability -------------------------------------------------


def test_direct_construction_without_the_build_sentinel_is_refused(tmp_path: Path) -> None:
    """No caller outside this module holds the build sentinel
    `open_trusted_object_authority_v2` requires -- a direct construction
    attempt (the forgeable/unbound-proof-carrier shape from PR #302's
    round-3 refutation) is refused immediately, before any operation."""
    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        TrustedObjectAuthorityV2(cas_root=tmp_path, expected_marker=b"x" * 32, _sentinel=object())
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2


def test_a_capability_pointed_at_a_directory_with_no_matching_marker_is_refused(tmp_path: Path) -> None:
    """Even an instance that somehow obtains the real sentinel (an
    "insider" construction, simulating a bug or a hostile internal caller)
    is refused on first use unless the directory it names actually holds
    the exact marker this module wrote at build time -- an arbitrary
    directory (never built by `open_trusted_object_authority_v2`) has no
    such marker."""
    import app.agent_review.trusted_object_authority_v2 as authority_module

    arbitrary_dir = tmp_path / "not-a-real-authority"
    arbitrary_dir.mkdir()
    forged = TrustedObjectAuthorityV2(
        cas_root=arbitrary_dir,
        expected_marker=b"x" * 32,
        _sentinel=authority_module._BUILD_SENTINEL_V2,  # noqa: SLF001 -- simulating an insider forgery
    )
    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        _ = forged.trusted_repo_root
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2


# -- 7: session bound to a repository, not suppliable across repositories ---------


def test_no_operation_accepts_an_external_repository_path(tmp_path: Path) -> None:
    """Structural, not behavioural: there is no parameter on
    `TrustedObjectAuthorityV2` by which a caller could name *which*
    repository an operation targets -- every operation is against the
    exact directory the instance built for itself. This is what makes "a
    session opened for repo A supplied to a call targeting repo B" -- PR
    #302's other round-3 refutation -- inexpressible rather than merely
    guarded against."""
    for name, member in inspect.getmembers(TrustedObjectAuthorityV2):
        if name.startswith("_") or not callable(member):
            continue
        signature = inspect.signature(member)
        for parameter_name in signature.parameters:
            assert parameter_name not in ("repo_root", "cwd", "path", "repo", "repository"), (
                f"{name} accepts {parameter_name!r} -- a capability must not take an external repo path"
            )


def test_an_authority_built_for_one_repository_has_no_knowledge_of_a_different_one(tmp_path: Path) -> None:
    """Even setting the (structurally absent) parameter question aside:
    prove by construction that an authority built from repo A cannot answer
    anything about repo B's commits -- its private copy simply never
    contains B's objects."""
    repo_a, c1_a, _c2_a, c3_a = _linear_history_fixture(tmp_path / "a", seed="repo-a")
    repo_b, c1_b, _c2_b, c3_b = _linear_history_fixture(tmp_path / "b", seed="repo-b")
    assert c1_a != c1_b and c3_a != c3_b

    with open_trusted_object_authority_v2(repo_a) as authority_a:
        assert resolve_commit_v2(repo_root=authority_a.trusted_repo_root, ref=c1_a) == c1_a
        with pytest.raises(SubjectMaterialisationError):
            resolve_commit_v2(repo_root=authority_a.trusted_repo_root, ref=c3_b)


# -- 8-10: ancestry-determination integrity (the withdrawn S4 mechanisms) ---------


def _object_path_for_sha_v2(repo: Path, sha: str) -> Path:
    return repo / ".git" / "objects" / sha[:2] / sha[2:]


def _delete_loose_object_v2(repo: Path, sha: str) -> None:
    object_path = _object_path_for_sha_v2(repo, sha)
    assert object_path.is_file(), "fixture assumption violated: object is not a loose object"
    object_path.unlink()


def test_deleted_parent_object_yields_undetermined_not_a_false_negative(tmp_path: Path) -> None:
    """Withdrawn S4 mechanism #2 (object-store corruption): a reachable
    parent object physically removed from the store makes `git
    merge-base --is-ancestor` exit 1 -- indistinguishable, by exit code
    alone, from a genuine non-ancestor. `authorize_commit_for_execution_v2`
    must not report `authorized=False` here: the closure could not be
    completely enumerated, so the honest answer is
    `IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2`, not a silent False."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)
    _delete_loose_object_v2(repo, c2)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert excinfo.value.reason_code == IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2


def test_shallow_history_yields_undetermined_not_a_false_negative(tmp_path: Path) -> None:
    """Withdrawn S4 mechanism #1: a shallow clone's boundary commit reports
    no parent at all (via `.git/shallow`, never copied into the authority),
    so a real ancestor beyond the boundary is indistinguishable from a
    genuine non-ancestor by exit code alone. Reproduced with two
    independent shallow fetches into one local repository, each anchored
    at a different point in the same true linear history, so both `c1` and
    `c3` resolve locally while the connecting `c2` is absent -- exactly
    the shape that made `git merge-base --is-ancestor c1 c3` return exit 1
    silently under the withdrawn design."""
    source = tmp_path / "source"
    _init_repo(source, branch="main")
    (source / "pkg").mkdir()
    (source / "pkg" / "a.py").write_text("V = 1\n")
    c1 = _commit_all(source, "c1")
    (source / "pkg" / "a.py").write_text("V = 2\n")
    _c2 = _commit_all(source, "c2")
    (source / "pkg" / "a.py").write_text("V = 3\n")
    c3 = _commit_all(source, "c3")
    subprocess.run(["git", "config", "uploadpack.allowReachableSHA1InWant", "true"], cwd=source, check=True)

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", "--depth=1", f"file://{source}", str(clone)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", f"{c1}:refs/heads/c1-shallow-tip"],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    assert _rev_parse(clone, "refs/heads/c1-shallow-tip") == c1
    assert _rev_parse(clone, "HEAD") == c3

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=clone, commit_sha=c1, trusted_ref=c3)
    assert excinfo.value.reason_code == IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2


def test_grafts_file_is_never_consulted(tmp_path: Path) -> None:
    """`.git/info/grafts` is legacy history-rewriting -- never copied into
    the authority (only `objects/`, `refs/heads`, `refs/tags`,
    `packed-refs`, `HEAD` are). A hostile grafts entry that would sever
    `c3` from its real parent `c2` if consulted has no effect: the real,
    complete history is what the authority was built from."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)
    grafts_path = repo / ".git" / "info" / "grafts"
    grafts_path.parent.mkdir(parents=True, exist_ok=True)
    grafts_path.write_text(f"{c3}\n")  # rewrites c3 to have NO parents
    subprocess.run(["git", "config", "advice.graftFileDeprecated", "false"], cwd=repo, check=True)

    # Sanity: the live repository's OWN git, asked directly, is fooled --
    # this proves the fixture is a real, live hazard, not a no-op.
    hostile_view = subprocess.run(
        ["git", "merge-base", "--is-ancestor", c1, c3], cwd=repo, capture_output=True
    )
    assert hostile_view.returncode != 0, "fixture's grafts file did not actually fool a direct git invocation"

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True
    _ = c2


def test_git_replace_never_affects_ancestry_determination(tmp_path: Path) -> None:
    """Companion to the existing materialisation-side replace test: `git
    replace` must not affect the ancestry decision either. `refs/replace/*`
    is never copied into the authority, and every bounded git invocation
    additionally carries `--no-replace-objects` as defense in depth."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)
    subprocess.run(["git", "replace", c1, c3], cwd=repo, check=True)
    try:
        result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
        assert result.authorized is True
    finally:
        subprocess.run(["git", "replace", "-d", c1], cwd=repo, check=True)
    _ = c2


def test_hostile_config_in_the_live_repository_is_never_read(tmp_path: Path) -> None:
    """The CAS's own `config` is hand-authored from scratch, never copied
    from the live repository -- a hostile or malformed `config` in the
    live repository (here: garbage appended directly to the file, bypassing
    `git config`'s own validation) has no effect on anything read through
    the authority."""
    repo, c1, _c2, c3 = _linear_history_fixture(tmp_path)
    config_path = repo / ".git" / "config"
    with config_path.open("a") as handle:
        handle.write("\n[this is not valid git config syntax at all !!! ===\n")

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True


# -- 11: alternate object directory -- flattened, not chained ----------------------


def test_alternate_object_directory_is_flattened_into_the_authority(tmp_path: Path) -> None:
    """An `objects/info/alternates` entry (e.g. from `git clone --reference`)
    names a second object store a repository legitimately depends on.
    Historical falsifier item: "an alternate object directory". This is
    not refused -- it is recursively copied into the SAME private
    authority, so the result is self-contained and correct, never a
    dangling reference the authority itself would need to chase later."""
    base = tmp_path / "base"
    _init_repo(base, branch="main")
    (base / "pkg").mkdir()
    (base / "pkg" / "a.py").write_text("V = 1\n")
    base_sha = _commit_all(base, "base commit")

    fork = tmp_path / "fork"
    subprocess.run(
        # `--shared` implies `--local`; combining it with `--no-local` would
        # make git fall back to a real, self-contained copy with no
        # alternates link at all, defeating this fixture's purpose.
        ["git", "clone", "--quiet", "--shared", str(base), str(fork)],
        check=True,
        capture_output=True,
    )
    alternates_path = fork / ".git" / "objects" / "info" / "alternates"
    assert alternates_path.is_file(), "fixture did not produce an alternates file"

    # `fork`'s own objects directory does not have `base_sha`'s objects --
    # only the alternate does.
    own_object_path = _object_path_for_sha_v2(fork, base_sha)
    assert not own_object_path.is_file()

    destination = tmp_path / "subject"
    destination.mkdir()
    result = materialise_commit_subject_v2(repo_root=fork, ref=base_sha, destination=destination)
    assert result.commit_sha == base_sha
    assert (destination / "pkg" / "a.py").read_text() == "V = 1\n"


# -- 12: fake `git` earlier in PATH -------------------------------------------------


def test_fake_git_earlier_in_path_is_never_executed_by_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the existing materialisation-side test: the same
    guarantee for `authorize_commit_for_execution_v2`'s own git
    invocations (`--git-common-dir` resolution, the CAS's internal
    `rev-parse --git-dir` self-check, and `prove_ancestry`'s `rev-list`) --
    all go through the same hardened `bounded_git_v2.run_bounded_git_v2`,
    which resolves `git` against `os.defpath`, never the caller's `PATH`."""
    repo, c1, _c2, c3 = _linear_history_fixture(tmp_path)

    marker = tmp_path / "fake-git-ran"
    fake_git_dir = tmp_path / "fake-bin"
    fake_git_dir.mkdir()
    fake_git = fake_git_dir / "git"
    fake_git.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_git_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True
    assert not marker.exists()


# -- concurrent mutation of the live repository during acquisition ---------------


def test_object_deleted_from_the_live_repository_mid_copy_is_a_typed_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent writer to the LIVE (hostile, by threat model) repository
    can remove or replace an object between this module's directory
    enumeration and its subsequent read of that same path -- a TOCTOU
    window inherent to reading any live filesystem. The acquisition must
    fail as a typed `TrustedObjectAuthorityError`, never let a raw
    `OSError` escape this module's otherwise fully-typed error surface,
    and must not leave a partially-built private directory behind."""
    import app.agent_review.trusted_object_authority_v2 as authority_module

    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)

    # Correction round 1 moved the low-level read off `Path.read_bytes()`
    # onto a no-follow-symlink-safe, pre-charged `os.open`/`os.read` path
    # (`_read_regular_file_charged_v2`) -- simulate the concurrent deletion
    # at `os.open` instead, for whichever path is under a two-hex fanout
    # directory (a loose object).
    real_open = os.open

    def flaky_open(path, flags, *args, **kwargs):
        path_obj = Path(os.fsdecode(path))
        if path_obj.parent.name and len(path_obj.parent.name) == 2 and path_obj.parent.parent.name == "objects":
            raise FileNotFoundError(f"simulated concurrent deletion: {path_obj}")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky_open)

    stray_before = _count_stray_cas_dirs_v2()
    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised before yielding an authority")
    assert excinfo.value.reason_code == authority_module.TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2
    assert _count_stray_cas_dirs_v2() == stray_before
    _ = c3


# -- 13: hard budgets precede untrusted parsing -------------------------------------


def test_object_copy_budget_exceeded_is_refused_before_any_git_reading_primitive_runs(
    tmp_path: Path,
) -> None:
    """CAEM ADR 0011's "hard budgets precede untrusted parsing", applied to
    acquisition: a budget refusal happens during the raw filesystem copy,
    strictly before any of the copied bytes are handed to git for
    interpretation."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo, max_total_bytes=1):
            pytest.fail("should have raised before yielding an authority")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2
    _ = c3


# -- 14: happy path sanity (the property must not just always-refuse) --------------


def test_ordinary_authorization_and_materialisation_still_work_end_to_end(tmp_path: Path) -> None:
    """A refusal-only corpus cannot detect over-rejection -- this asserts
    the new architecture still produces the correct positive answer for a
    completely ordinary, non-hostile repository."""
    repo, c1, _c2, c3 = _linear_history_fixture(tmp_path)

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True
    assert result.commit_sha == c1
    assert result.trusted_ref_sha == c3

    destination = tmp_path / "subject"
    destination.mkdir()
    materialised = materialise_commit_subject_v2(repo_root=repo, ref=c3, destination=destination)
    assert materialised.commit_sha == c3
    assert "V = 3" in (destination / "pkg" / "a.py").read_text()


def test_unresolvable_commit_still_raises_unknown_commit_not_undetermined(tmp_path: Path) -> None:
    """An sha that simply does not exist anywhere is a different fact from
    an sha that exists but whose ancestry cannot be fully proven -- the two
    reason codes must not collapse into each other."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha="d" * 40, trusted_ref=c3)
    assert excinfo.value.reason_code == IDENTITY_UNKNOWN_COMMIT_REASON_V2


# -- correction round 1 (post-review, both lanes) -----------------------------
#
# Lane A (acquisition/trust-transition) reproduced three P0s with runnable
# repro scripts against the pre-correction head: (1) symlink-following in the
# objects/refs copy walk let a hostile checkout redirect a fanout directory or
# ref to arbitrary host bytes; (2) the budget check ran AFTER a full
# `read_bytes()`, reactive rather than preventive; (3) `_parse_alternates_v2`
# had zero containment check, letting an ordinary host directory (e.g. `/etc`)
# be flattened into the CAS as if it were a real object store. Lane B (graph/
# authorization truth) reproduced, TWICE, through the real production path
# (`authorize_commit_for_execution_v2` -> `prove_ancestry`), that a loose
# object's content was never re-verified against the sha its own storage path
# claims -- overwriting a loose object's bytes at its existing, predictable
# path flipped a genuine ancestor to `authorized=False` and an unrelated,
# never-integrated commit to `authorized=True`, both silently, no exception.
#
# All five are fixed under ONE coherent design change (content re-verification
# at CAS-copy time for loose objects + no-follow reads throughout + pre-stat
# budget checks + alternates structural containment), not four separate
# patches -- see `trusted_object_authority_v2.py`'s `_verify_loose_object_hash_v2`,
# `_read_regular_file_charged_v2`, `_safe_scandir_no_symlinks_v2`, and
# `_looks_like_git_objects_directory_v2`.


def test_loose_object_overwritten_at_its_own_path_no_longer_silently_authorizes_false(
    tmp_path: Path,
) -> None:
    """Lane B finding #1, independently reproduced against the real
    `authorize_commit_for_execution_v2` production path before this
    correction existed (`/tmp/loose_poc/repro.py`). `c1 -> c2 -> c3`; `c1`
    is root (no parent), so overwriting `c2`'s loose object with `c1`'s own
    bytes truncates the real ancestry at `c2`'s path without deleting or
    corrupting anything -- git decompresses it just fine, it simply is not
    `c2`'s real content any more. Before this correction: `authorized`
    silently flipped `True` -> `False`, no exception. After: refused."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True

    c1_obj = _object_path_for_sha_v2(repo, c1)
    c2_obj = _object_path_for_sha_v2(repo, c2)
    c2_obj.write_bytes(c1_obj.read_bytes())

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert excinfo.value.reason_code == IDENTITY_TREE_UNREADABLE_REASON_V2


def test_forged_parent_line_spliced_into_existing_object_path_no_longer_authorizes_true(
    tmp_path: Path,
) -> None:
    """Lane B finding #2, independently reproduced against the real
    `authorize_commit_for_execution_v2` production path before this
    correction existed (`/tmp/loose_poc/repro2.py`). An unrelated, never-
    merged `evil` commit is genuinely NOT an ancestor of `c3`. Overwriting
    `c2`'s loose object with forged content (same tree/author/committer,
    with an added `parent evil` line spliced in -- a different real sha
    than `c2`'s, stored at `c2`'s own path) spliced `evil` into `c3`'s
    apparent ancestry. Before this correction: `authorized` silently
    flipped `False` -> `True`, no exception, for a commit that was never
    actually integrated. After: refused."""
    repo, c1, c2, c3 = _linear_history_fixture(tmp_path)
    subprocess.run(["git", "checkout", "--quiet", "-b", "evil", c1], cwd=repo, check=True)
    (repo / "pkg" / "evil.py").write_text("PWNED=1\n")
    evil = _commit_all(repo, "evil-commit")
    subprocess.run(["git", "checkout", "--quiet", "main"], cwd=repo, check=True)

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=evil, trusted_ref=c3)
    assert result.authorized is False

    raw = subprocess.run(
        ["git", "cat-file", "commit", c2], cwd=repo, capture_output=True, check=True
    ).stdout
    lines = raw.split(b"\n")
    tree_line = next(line for line in lines if line.startswith(b"tree "))
    rest_index = lines.index(tree_line) + 1
    forged_content = tree_line + b"\n" + b"parent " + evil.encode() + b"\n" + b"\n".join(lines[rest_index:])
    header = f"commit {len(forged_content)}\0".encode()
    forged_true_sha = hashlib.sha1(header + forged_content, usedforsecurity=False).hexdigest()  # noqa: S324
    assert forged_true_sha != c2, "forged content must genuinely differ from c2 -- not a no-op fixture"

    c2_obj = _object_path_for_sha_v2(repo, c2)
    c2_obj.write_bytes(zlib.compress(header + forged_content))

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha=evil, trusted_ref=c3)
    assert excinfo.value.reason_code == IDENTITY_TREE_UNREADABLE_REASON_V2


def test_loose_object_hash_verification_is_reachable_at_the_authority_level(tmp_path: Path) -> None:
    """Same forged-loose-object shape as the two tests above, exercised
    directly against `open_trusted_object_authority_v2` (rather than
    through `authorize_commit_for_execution_v2`'s error-mapping) to assert
    the precise, specific reason code the CAS build itself raises."""
    repo, c1, c2, _c3 = _linear_history_fixture(tmp_path)
    c1_obj = _object_path_for_sha_v2(repo, c1)
    c2_obj = _object_path_for_sha_v2(repo, c2)
    c2_obj.write_bytes(c1_obj.read_bytes())

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised while copying the tampered loose object")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2


def test_symlinked_loose_object_fanout_directory_is_refused(tmp_path: Path) -> None:
    """Lane A finding #1: `.git/objects/<xx>` symlinked to an arbitrary
    host directory must never have its target's bytes copied into the CAS
    as if they were real loose objects."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    decoy = tmp_path / "decoy_fanout"
    decoy.mkdir()
    (decoy / "0123456789abcdef0123456789abcdef012345").write_bytes(b"not a real git object")

    objects_dir = repo / ".git" / "objects"
    fanout_dirs = [p for p in objects_dir.iterdir() if p.is_dir() and len(p.name) == 2]
    assert fanout_dirs, "fixture must have produced at least one real fanout directory"
    victim = fanout_dirs[0]
    shutil.rmtree(victim)
    victim.symlink_to(decoy, target_is_directory=True)

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised on the symlinked fanout directory")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2
    _ = c3


def test_symlinked_ref_is_refused(tmp_path: Path) -> None:
    """Lane A finding #1, refs side: `.git/refs/heads/<name>` symlinked to
    an arbitrary host path must never be read and copied verbatim."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    decoy = tmp_path / "decoy_ref_target"
    decoy.write_text(c3 + "\n")

    evil_ref = repo / ".git" / "refs" / "heads" / "evil"
    evil_ref.symlink_to(decoy)

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised on the symlinked ref")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2


def test_symlinked_packed_refs_is_refused(tmp_path: Path) -> None:
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    decoy = tmp_path / "decoy_packed_refs"
    decoy.write_text(f"{c3} refs/heads/main\n")
    (repo / ".git" / "packed-refs").symlink_to(decoy)

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised on the symlinked packed-refs file")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2


def test_symlinked_alternates_file_is_refused(tmp_path: Path) -> None:
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    decoy = tmp_path / "decoy_alternates"
    decoy.write_text("/nonexistent\n")
    info_dir = repo / ".git" / "objects" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "alternates").symlink_to(decoy)

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised on the symlinked alternates file")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2
    _ = c3


def test_budget_is_checked_from_stat_before_any_content_is_read(tmp_path: Path) -> None:
    """Lane A finding #2, exercised directly against the corrected
    low-level primitive: `os.read` must never be called at all once
    `fstat`-derived pre-charge has already exceeded budget -- proving the
    check is genuinely preventive (before any byte enters memory), not
    merely reactive (after a full read)."""
    import unittest.mock

    import app.agent_review.trusted_object_authority_v2 as authority_module

    big_file = tmp_path / "big"
    big_file.write_bytes(b"x" * 4096)

    tracker = authority_module._ObjectCopyBudgetTrackerV2(  # noqa: SLF001
        authority_module._ObjectCopyBudgetV2(  # noqa: SLF001
            max_total_bytes=10, max_object_count=1000, max_alternate_depth=8
        )
    )

    original_os_read = os.read
    read_was_called = False

    def spy_os_read(*args, **kwargs):
        nonlocal read_was_called
        read_was_called = True
        return original_os_read(*args, **kwargs)

    with unittest.mock.patch.object(os, "read", spy_os_read):
        with pytest.raises(TrustedObjectAuthorityError) as excinfo:
            authority_module._read_regular_file_charged_v2(big_file, tracker)  # noqa: SLF001

    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2
    assert read_was_called is False, "budget must be checked from fstat before any os.read call"


def test_alternate_pointing_at_an_ordinary_non_repository_directory_is_refused(
    tmp_path: Path,
) -> None:
    """Lane A finding #3: an `objects/info/alternates` entry naming an
    ordinary host directory that was never shaped like a git repository
    (no sibling `HEAD`) must be refused, not flattened into the CAS as if
    it were a real object store."""
    repo, _c1, _c2, c3 = _linear_history_fixture(tmp_path)
    ordinary_dir = tmp_path / "not_a_repository_at_all"
    ordinary_dir.mkdir()
    (ordinary_dir / "just_some_file.txt").write_text("nothing to see here\n")

    info_dir = repo / ".git" / "objects" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "alternates").write_text(str(ordinary_dir) + "\n")

    with pytest.raises(TrustedObjectAuthorityError) as excinfo:
        with open_trusted_object_authority_v2(repo):
            pytest.fail("should have raised on the non-repository-shaped alternate")
    assert excinfo.value.reason_code == TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2
    _ = c3


def test_legitimate_alternate_still_works_after_containment_check(tmp_path: Path) -> None:
    """Positive counterpart to the refusal test above -- the containment
    check must not break the real, already-covered `--shared` clone
    workflow (a genuine alternate always has a `HEAD` sibling)."""
    base = tmp_path / "base"
    _init_repo(base, branch="main")
    (base / "pkg").mkdir()
    (base / "pkg" / "a.py").write_text("V = 1\n")
    base_sha = _commit_all(base, "base commit")

    fork = tmp_path / "fork"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(base), str(fork)], check=True, capture_output=True
    )

    destination = tmp_path / "subject"
    destination.mkdir()
    result = materialise_commit_subject_v2(repo_root=fork, ref=base_sha, destination=destination)
    assert result.commit_sha == base_sha


def test_packed_refs_with_ordinary_content_still_round_trips(tmp_path: Path) -> None:
    """Regression guard for the anchored packed-refs parsing (Lane A P2):
    an ordinary, legitimate `packed-refs` file must still work exactly as
    before -- the fix narrows what an incidental-substring line could
    smuggle in, not what a real ref line means."""
    repo, c1, _c2, c3 = _linear_history_fixture(tmp_path)
    subprocess.run(["git", "pack-refs", "--all"], cwd=repo, check=True)
    assert (repo / ".git" / "packed-refs").is_file()

    result = authorize_commit_for_execution_v2(repo_root=repo, commit_sha=c1, trusted_ref=c3)
    assert result.authorized is True


def test_authority_dataclass_is_frozen(tmp_path: Path) -> None:
    """Lane A P2: `TrustedObjectAuthorityV2` is frozen like every sibling
    dataclass in this package's threat-scoped modules -- its own
    "unforgeable" docstring claim should not be contradicted by ordinary
    post-construction mutability."""
    repo, _c1, _c2, _c3 = _linear_history_fixture(tmp_path)
    with open_trusted_object_authority_v2(repo) as authority:
        with pytest.raises(dataclasses.FrozenInstanceError):
            authority._cas_root = Path("/tmp/evil")  # noqa: SLF001
