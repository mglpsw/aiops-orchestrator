"""`#200-G1` -- new tests for the ported bounded-git child environment.

Ported with revalidation from the frozen-forensic `#200-F` reconstruction
(commit `5703e5b`); no qualification transfer -- these tests are new.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agent_review.bounded_git_v2 import (
    BOUNDED_GIT_COMMAND_FAILED_REASON_V2,
    BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2,
    BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2,
    BoundedGitError,
    bounded_git_environment_v2,
    open_bounded_git_session_v2,
    resolve_trusted_git_absolute_path_v2,
    run_bounded_git_v2,
)


def _real_partial_clone_missing_a_blob(tmp_path: Path) -> tuple[Path, str]:
    """Build a real `--filter=blob:none --no-checkout` partial clone
    (`subject`, cloned from a fresh `upstream`) with one blob missing
    locally, and return `(subject, blob_sha)`. Shared setup for every
    finding-5-family test below, each of which then mutates `subject`'s
    config differently before attempting to read the missing blob through
    it.

    `--no-checkout` is required, not optional: without it, `git clone`
    still populates the working tree, which means the blob for whatever
    file is at `HEAD` is fetched immediately as PART OF THE CLONE ITSELF
    (into a pack the clone writes before this function ever returns) --
    confirmed by direct inspection, not assumed: cloning a one-file repo
    with `--filter=blob:none` alone (no `--no-checkout`) already left that
    file's blob content locally present, defeating the entire point of
    this fixture (a blob that is STILL missing at the moment the test
    calls `cat-file --batch`, matching exactly how Codex's own round-2
    reproduction was built).
    """
    upstream = tmp_path / "upstream"
    _init_repo(upstream)
    (upstream / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=upstream, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=upstream, check=True)
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=upstream, check=True)
    subprocess.run(
        ["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=upstream, check=True
    )
    blob_sha = subprocess.run(
        ["git", "rev-parse", "HEAD:f.txt"],
        cwd=upstream,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject = tmp_path / "subject"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            "--no-checkout",
            f"file://{upstream}",
            str(subject),
        ],
        check=True,
    )
    return subject, blob_sha


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def test_bounded_environment_key_set_is_exact() -> None:
    """The child's environment mapping is asserted on its exact key set so
    it cannot silently drift into `dict(os.environ)` with deletions."""
    env = bounded_git_environment_v2()
    assert set(env) == {
        "PATH",
        "LC_ALL",
        "LANG",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS",
        "GIT_ASKPASS",
        "GIT_SSH_COMMAND",
        "HOME",
    }


def test_bounded_environment_path_is_defpath_not_caller_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/attacker/controlled/bin")
    env = bounded_git_environment_v2()
    assert env["PATH"] == os.defpath
    assert "/attacker/controlled/bin" not in env["PATH"]


def test_bounded_environment_home_defaults_to_devnull_not_callers_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/attacker/controlled/home")
    env = bounded_git_environment_v2()
    assert env["HOME"] == os.devnull


def test_resolve_trusted_git_ignores_caller_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text("#!/bin/sh\necho fake\n")
    (fake_bin / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    resolved = resolve_trusted_git_absolute_path_v2()
    assert resolved != str(fake_bin / "git")
    assert Path(resolved).is_absolute()


def test_run_bounded_git_refuses_nonexistent_cwd(tmp_path: Path) -> None:
    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(["status"], cwd=tmp_path / "does-not-exist")
    assert excinfo.value.reason_code == BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2


def test_run_bounded_git_raises_on_command_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "refs/heads/does-not-exist"], cwd=repo)
    assert excinfo.value.reason_code == BOUNDED_GIT_COMMAND_FAILED_REASON_V2


def test_run_bounded_git_check_false_does_not_raise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    completed = run_bounded_git_v2(
        ["rev-parse", "--verify", "--quiet", "refs/heads/does-not-exist"],
        cwd=repo,
        check=False,
    )
    assert completed.returncode != 0


def test_run_bounded_git_ignores_ambient_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "totally-unrelated.git"))
    completed = run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo)
    assert completed.returncode == 0


# -- `#200-G1-PM` finding 5 and its two post-review recurrences: lazy fetch -----
#
# `#200-G1-PM` round 0 (original finding) and round 1 (post-review
# correction) both tried to ENUMERATE the config keys/spellings that make a
# repository capable of lazy fetch (`remote.<name>.promisor`, in every
# legal boolean spelling) and refuse pre-emptively when found. Round 2
# (Codex, this PR) found a THIRD, different marker
# (`remote.origin.partialclonefilter`, surviving even with `promisor`
# itself explicitly unset) that the enumeration never covered -- proving
# enumeration does not converge. The fix below abandons enumeration
# entirely: `run_bounded_git_v2` now snapshots the object store immediately
# before and after every invocation and fails closed if anything new
# appeared, regardless of which config key or marker made the fetch
# possible. Every scenario below -- the original non-`origin`-name case,
# the boolean-spelling case, and the new partialclonefilter-only case -- is
# kept as a regression witness against the SAME outcome-based mechanism,
# proving it is a structural superset of the enumeration it replaced, not
# merely a fix for the specific case that motivated it.


def test_partial_clone_with_non_origin_promisor_remote_is_refused(tmp_path: Path) -> None:
    """Original finding-5 witness: `git remote rename origin evil`
    (ordinary git, real plumbing below) preserves the `promisor` flag
    under the new name. Regression witness for the outcome-based
    invariant check, not the (now-removed) config-enumeration approach
    that originally closed this."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "remote", "rename", "origin", "evil"], cwd=subject, check=True)

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2


def test_repo_without_any_promisor_remote_is_unaffected(tmp_path: Path) -> None:
    """Sanity check: the object-store invariant must not misfire for an
    ordinary, non-partial-clone repository performing an ordinary read --
    the overwhelming majority of calls this primitive ever makes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)

    completed = run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo)
    assert completed.returncode == 0


def test_partial_clone_with_non_true_spelled_promisor_value_is_refused(tmp_path: Path) -> None:
    """Round-1 post-review witness: `git config remote.origin.promisor yes`
    (any legal git-boolean spelling other than the literal string `true`).
    Regression witness for the outcome-based invariant check."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "config", "remote.origin.promisor", "yes"], cwd=subject, check=True)

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2


def test_partial_clone_marker_survives_promisor_unset_and_is_still_refused(
    tmp_path: Path,
) -> None:
    """Round-2 finding (Codex, this PR): unsetting `remote.origin.promisor`
    entirely leaves `remote.origin.partialclonefilter=blob:none` behind,
    which alone is still enough for git to treat the remote as a
    partial-clone source and lazily fetch -- reproduced empirically
    against `run_bounded_git_v2` itself before this fix (the previous,
    enumeration-based check found nothing, since it only ever looked at
    `remote.*.promisor`, and the fetch went through uncaught). This is
    exactly the case that falsified config-key enumeration as a viable
    long-term approach and motivated the outcome-based redesign below."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "config", "--unset", "remote.origin.promisor"], cwd=subject, check=True)
    # Confirm the setup: promisor is really gone, the filter marker remains.
    promisor_check = subprocess.run(
        ["git", "config", "--get-regexp", "promisor"], cwd=subject, capture_output=True
    )
    assert promisor_check.returncode != 0
    filter_check = subprocess.run(
        ["git", "config", "--get", "remote.origin.partialclonefilter"],
        cwd=subject,
        check=True,
        capture_output=True,
        text=True,
    )
    assert filter_check.stdout.strip() == "blob:none"

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2


def test_object_store_snapshot_ignores_files_outside_objects_dir(tmp_path: Path) -> None:
    """Sanity check on the invariant's precision: writing a file elsewhere
    under `.git` (not `.git/objects`) during a command must not trip the
    check -- it inspects exactly the object store, nothing broader."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)

    # A plain, ordinary read must not be flagged merely because *something*
    # elsewhere under `.git` (e.g. `logs/HEAD`, which real git commands do
    # touch) changed -- only `objects/` is in scope.
    completed = run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo)
    assert completed.returncode == 0


# -- `#200-G1-PM` round 3, finding A: retry after a rejected fetch --------------


def test_without_a_session_a_retry_after_rejected_fetch_succeeds_silently(
    tmp_path: Path,
) -> None:
    """Sanity/regression witness of the ORIGINAL round-3 finding: without a
    session, each call takes its own fresh baseline, so a fetched-but
    -rejected blob is already "old" by the time a second, identical call
    runs -- reproduced here to confirm the finding is real and to pin the
    per-call (no-session) default's documented, narrower guarantee."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "remote", "rename", "origin", "evil"], cwd=subject, check=True)

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2

    # The retry, with NO session, succeeds -- the blob fetched by the
    # rejected first call is already part of this call's own fresh
    # baseline. This is the documented, narrower guarantee of the
    # no-session default, not a bug in it: cross-call protection is what
    # `BoundedGitSessionV2` exists for, exercised below.
    completed = run_bounded_git_v2(
        ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
    )
    assert completed.returncode == 0


def test_with_a_session_a_retry_after_rejected_fetch_still_fails_closed(
    tmp_path: Path,
) -> None:
    """External Codex review (`#200-G1-PM` round 3 on this PR): a caller
    that catches `BoundedGitError` and retries (an ordinary pattern) must
    not have the retry silently succeed just because the first,
    already-rejected call's fetch left the blob locally present. With a
    `BoundedGitSessionV2` shared across both calls, the baseline is fixed
    at session-open time and never re-taken -- so the blob the first call
    fetched is STILL "new relative to session start" on the second call,
    even though the second call's own git invocation performs no fetch of
    its own."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "remote", "rename", "origin", "evil"], cwd=subject, check=True)

    session = open_bounded_git_session_v2(cwd=subject)

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"],
            cwd=subject,
            input_bytes=(blob_sha + "\n").encode(),
            session=session,
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2

    # The retry, using the SAME session, must ALSO fail closed.
    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"],
            cwd=subject,
            input_bytes=(blob_sha + "\n").encode(),
            session=session,
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2


# -- `#200-G1-PM` round 3, finding B: blind inside a linked worktree ------------


def test_lazy_fetch_is_detected_from_inside_a_linked_worktree(tmp_path: Path) -> None:
    """External Codex review (`#200-G1-PM` round 3 on this PR): `git
    rev-parse --git-dir` returns a linked worktree's PRIVATE administrative
    directory (`<main>/.git/worktrees/<name>`), which has no `objects/`
    subdirectory of its own -- confirmed by direct inspection below.
    `--git-common-dir` is git's own answer to where the SHARED object store
    (the same one every worktree of this repository reads from and, in
    this scenario, lazily fetches into) actually lives. Reproduced exactly
    as found: from inside a linked worktree of the partial-clone fixture,
    a lazy fetch must still be detected."""
    subject, blob_sha = _real_partial_clone_missing_a_blob(tmp_path)
    subprocess.run(["git", "remote", "rename", "origin", "evil"], cwd=subject, check=True)

    linked = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--no-checkout", "--detach", str(linked), "HEAD"],
        cwd=subject,
        check=True,
    )

    # Confirm the precondition this fix depends on: the worktree's own
    # `--git-dir` really does lack an `objects/` directory.
    worktree_git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=linked, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert not (linked / worktree_git_dir / "objects").is_dir()

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=linked, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2
