import os
import pytest
from pathlib import Path
from app.agent_review.git_commit_subject_v2 import (
    MaterialisationWorkspaceCapabilityV2,
    MaterialisedCommitSubjectCapabilityV2,
    acquire_materialised_commit_subject_v2,
    SubjectMaterialisationError,
    SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2,
)
from tests.agent_review.test_git_commit_subject_v2 import _init_repo, _commit_all

def count_open_fds():
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))

def test_workspace_capability_survives_caller_fd_close(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    with MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path) as workspace:
        # Close caller FD immediately
        os.close(caller_fd)

        # Capability should still work because it duplicated the FD
        test_dir = "test_dir"
        os.mkdir(test_dir, 0o700, dir_fd=workspace.require_open_fd())
        assert (tmp_path / test_dir).exists()

def test_subject_capability_cleanup_safe_after_workspace_close(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    # Close workspace first
    workspace.close()

    # Subject should still be able to clean itself up
    subject_root = subject.root_name
    assert (tmp_path / subject_root).exists()

    subject.close()
    # It cleans itself up!
    assert not (tmp_path / subject_root).exists()

def test_double_close_is_safe(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()
    workspace.close()
    fds_after_first = count_open_fds()
    assert fds_after_first == initial_fds - 1

    workspace.close() # Double close
    fds_after_second = count_open_fds()
    assert fds_after_second == fds_after_first

def test_acquire_requires_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "init")

    with pytest.raises(SubjectMaterialisationError) as exc:
        acquire_materialised_commit_subject_v2(repo_root=repo, ref=head, workspace=None)

    assert exc.value.args[0] == SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2


from app.agent_review.git_commit_subject_v2 import SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2

def test_closed_capability_fd_number_reuse(tmp_path: Path):
    # 1. create WorkspaceCapability W
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    # 2. record W.pool_fd = N
    n = workspace.pool_fd

    # 3. W.close()
    workspace.close()

    # 4. force/encourage OS to reuse FD number N for unrelated directory B
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "file").write_text("hello")
    # We open unrelated dir, hoping it gets FD N
    b_fd = os.open(unrelated, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    # 5. call acquire_materialised_commit_subject_v2(... workspace=W)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    with pytest.raises(SubjectMaterialisationError) as exc:
        acquire_materialised_commit_subject_v2(repo_root=repo, ref=head, workspace=workspace)

    assert exc.value.args[0] == SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2
    assert b_fd >= 0

    # unrelated directory B remains byte-identical
    assert (unrelated / "file").read_text() == "hello"
    os.close(b_fd)

def test_subject_pool_fd_leak_stable_count(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    for _ in range(5):
        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace
        )
        # Inside the context, FDs are higher
        assert count_open_fds() > initial_fds
        subject.close()
        # After close, FDs must return exactly to initial_fds
        assert count_open_fds() == initial_fds

    workspace.close()
    assert count_open_fds() == initial_fds - 1


from unittest.mock import patch
from app.agent_review.git_commit_subject_v2 import (
    OperationWorkspaceLeaseV2,
    MaterialisationEpochV2,
    SUBJECT_MATERIALISATION_RACE_REASON_V2,
)

def test_workspace_pinned_during_acquisition_immune_to_workspace_close(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    # Pin a lease for an operation
    lease = workspace.pin()

    # Close the workspace concurrently
    workspace.close()

    # The lease remains open and valid
    epoch = MaterialisationEpochV2(lease)
    root_fd, root_name, dest_path = epoch.create_epoch_root()
    assert dest_path.exists()
    assert root_fd >= 0

    epoch.rollback()
    assert not dest_path.exists()

def test_mkdir_success_open_failure_rollback(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    lease = workspace.pin()
    epoch = MaterialisationEpochV2(lease)

    orig_open = os.open
    def mock_open(*args, **kwargs):
        if "c3_" in str(args[0]):
            raise OSError("simulated EMFILE on open")
        return orig_open(*args, **kwargs)

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(SubjectMaterialisationError) as exc:
            epoch.create_epoch_root()
        assert exc.value.args[0] == SUBJECT_MATERIALISATION_RACE_REASON_V2

    epoch.rollback()
    assert not any(tmp_path.glob("c3_*"))
    workspace.close()

def test_workspace_invalid_fails_before_git_access(tmp_path: Path):
    non_existent_repo = tmp_path / "does_not_exist"

    # Missing workspace
    with pytest.raises(SubjectMaterialisationError) as exc:
        acquire_materialised_commit_subject_v2(repo_root=non_existent_repo, ref="HEAD", workspace=None)
    assert exc.value.args[0] == SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2

    # Closed workspace
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)
    workspace.close()

    with pytest.raises(SubjectMaterialisationError) as exc:
        acquire_materialised_commit_subject_v2(repo_root=non_existent_repo, ref="HEAD", workspace=workspace)
    assert exc.value.args[0] == SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2

def test_commit_failure_rolls_back_materialised_tree_and_fds(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    lease = workspace.pin()
    epoch = MaterialisationEpochV2(lease)
    root_fd, root_name, dest_path = epoch.create_epoch_root()

    (dest_path / "payload.txt").write_text("uncommitted bytes")

    with patch("app.agent_review.git_commit_subject_v2.MaterialisedCommitSubjectCapabilityV2", side_effect=RuntimeError("simulated constructor failure")):
        with pytest.raises(RuntimeError, match="simulated constructor failure"):
            epoch.commit(commit_sha="abcd", file_count=1, dest_path=dest_path)

    assert not dest_path.exists()
    assert epoch.root_fd == -1
    assert lease.pool_fd == -1

    workspace.close()


import threading
import concurrent.futures

def test_cm_c3_pin_close_linearizability(tmp_path: Path):
    """CM-C3-PIN-CLOSE-LINEARIZABILITY: verify pin() and close() are mutually serialized.

    Proves that close() cannot close or poison the descriptor while pin() is in its
    critical section between check and duplication.
    """
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    pin_entered = threading.Event()
    allow_pin_finish = threading.Event()

    def pin_hook():
        pin_entered.set()
        # Hold the critical section until close has attempted and blocked
        allow_pin_finish.wait(timeout=2.0)

    workspace._pin_hook = pin_hook

    lease_result = []
    def do_pin():
        lease = workspace.pin()
        lease_result.append(lease)

    pin_thread = threading.Thread(target=do_pin)
    pin_thread.start()

    # Wait for pin to enter critical section
    assert pin_entered.wait(timeout=2.0)

    # Attempt close in another thread; it must block on the internal lock
    close_finished = threading.Event()
    def do_close():
        workspace.close()
        close_finished.set()

    close_thread = threading.Thread(target=do_close)
    close_thread.start()

    # Give close_thread a moment to attempt acquiring the lock
    # Verify close has NOT finished while pin holds the lock
    assert not close_finished.is_set()
    assert not workspace._closed
    assert workspace.pool_fd >= 0

    # Allow pin to complete
    allow_pin_finish.set()
    pin_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)

    assert len(lease_result) == 1
    lease = lease_result[0]
    # Lease remains open, valid, and bound to the workspace pool
    assert lease.pool_fd >= 0
    assert not lease._closed
    # Workspace is now closed
    assert workspace._closed
    assert workspace.pool_fd == -1

    lease.close()

def test_multiple_sequential_pins_produce_independent_leases(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    leases = [workspace.pin() for _ in range(5)]
    fds = [lease.pool_fd for lease in leases]
    assert len(set(fds)) == 5  # Each lease has a distinct private duplicated FD

    for lease in leases:
        assert lease.pool_fd >= 0
        lease.close()
        assert lease.pool_fd == -1

    workspace.close()

def test_multiple_concurrent_pins(tmp_path: Path):
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(workspace.pin) for _ in range(20)]
        leases = [f.result() for f in futures]

    fds = [l.pool_fd for l in leases]
    assert len(set(fds)) == 20

    for lease in leases:
        lease.close()

    workspace.close()

def test_concurrent_subject_capability_close(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(subject.close) for _ in range(20)]
        for f in futures:
            f.result()

    assert subject._closed
    assert subject.root_fd == -1
    assert subject.pool_fd == -1

    workspace.close()


import sys
import stat
from app.agent_review.git_commit_subject_v2 import (
    materialise_commit_subject_v2,
    _fd_rmtree,
)

def test_cm_c3_cleanup_deep_tree(tmp_path: Path):
    """CM-C3-CLEANUP-DEEP-TREE: verify iterative _fd_rmtree does not raise RecursionError

    Even when tree depth exceeds the Python recursion limit, iterative descriptor
    cleanup succeeds completely and restores initial file descriptor count.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    # Artificially construct a nested directory tree deeper than a lowered recursion limit
    curr = subject.root_locator
    for i in range(120):
        curr = curr / f"d{i}"
        curr.mkdir()
        (curr / "leaf.txt").write_text("deep")

    orig_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(50)  # Lower than tree depth (120 > 50)
        subject.close()
    finally:
        sys.setrecursionlimit(orig_limit)

    assert subject._closed
    assert subject.root_fd == -1
    assert subject.pool_fd == -1
    assert not subject.root_locator.exists()
    assert count_open_fds() == initial_fds

    workspace.close()


def test_cm_c3_cleanup_exception_does_not_leak_descriptors(tmp_path: Path):
    """CM-C3-CLEANUP-FAILSAFE: verify descriptor closure is unconditional even if rmtree raises."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    with patch("app.agent_review.git_commit_subject_v2._fd_rmtree", side_effect=RuntimeError("simulated rmtree fault")):
        subject.close()

    assert subject._closed
    assert subject.root_fd == -1
    assert subject.pool_fd == -1
    # Both descriptors must be closed despite the exception
    assert count_open_fds() == initial_fds

    workspace.close()


def test_legacy_materialise_destination_default_permissions(tmp_path: Path):
    """Verify legacy materialise_commit_subject_v2 creates destination using default umask."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    dest = tmp_path / "legacy_dest"
    assert not dest.exists()

    subject = materialise_commit_subject_v2(
        repo_root=repo,
        ref=head,
        destination=dest,
    )
    assert subject.root.exists()

    # Verify destination permissions match default mkdir (not forced 0700)
    current_umask = os.umask(0)
    os.umask(current_umask)
    expected_mode = 0o777 & ~current_umask

    actual_mode = stat.S_IMODE(os.stat(dest).st_mode)
    assert actual_mode == expected_mode


def test_legacy_materialise_leaf_permissions_preserve_umask(tmp_path: Path):
    """Verify legacy materialise preserves leaf permissions under caller's umask."""
    import subprocess
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "regular.txt").write_text("hello regular")

    exec_file = repo / "script.sh"
    exec_file.write_text("#!/bin/sh\necho hi")
    exec_file.chmod(0o755)

    subprocess.run(["git", "add", "regular.txt", "script.sh"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=repo, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    # 1. Permissive umask 0002 (group writable)
    orig_umask = os.umask(0o002)
    try:
        dest = tmp_path / "umask_dest"
        dest.mkdir(mode=0o775)

        materialise_commit_subject_v2(
            repo_root=repo,
            ref=head,
            destination=dest,
        )

        reg_mode = stat.S_IMODE(os.stat(dest / "regular.txt").st_mode)
        assert reg_mode == (0o666 & ~0o002)  # 0664

        exec_mode = stat.S_IMODE(os.stat(dest / "script.sh").st_mode)
        assert exec_mode == (0o777 & ~0o002)  # 0775
    finally:
        os.umask(orig_umask)

    # 2. Restrictive umask 0077 (private)
    orig_umask = os.umask(0o077)
    try:
        dest2 = tmp_path / "restrictive_dest"
        dest2.mkdir(mode=0o700)

        materialise_commit_subject_v2(
            repo_root=repo,
            ref=head,
            destination=dest2,
        )

        reg_mode2 = stat.S_IMODE(os.stat(dest2 / "regular.txt").st_mode)
        assert reg_mode2 == (0o666 & ~0o077)  # 0600

        exec_mode2 = stat.S_IMODE(os.stat(dest2 / "script.sh").st_mode)
        assert exec_mode2 == (0o600 | 0o111)  # 0711
    finally:
        os.umask(orig_umask)

    # 3. Execution-masking umask 0011 (execute bits explicitly restored)
    orig_umask = os.umask(0o011)
    try:
        dest3 = tmp_path / "noexec_umask_dest"
        dest3.mkdir(mode=0o777)

        materialise_commit_subject_v2(
            repo_root=repo,
            ref=head,
            destination=dest3,
        )

        # Regular file has no execute bits (0666)
        reg_mode3 = stat.S_IMODE(os.stat(dest3 / "regular.txt").st_mode)
        assert reg_mode3 == (0o666 & ~0o011)  # 0666
        assert not os.access(dest3 / "regular.txt", os.X_OK)

        # Executable file has execute bits explicitly restored
        exec_mode3 = stat.S_IMODE(os.stat(dest3 / "script.sh").st_mode)
        assert exec_mode3 == 0o777
        assert os.access(dest3 / "script.sh", os.X_OK)
    finally:
        os.umask(orig_umask)


def test_cm_c3_cleanup_fd_depth_bounded(tmp_path: Path):
    """RESOURCE_BOUNDED_AUTHORITY_CLOSURE: verify peak live cleanup FDs is O(1) independent of tree depth.

    Measures peak_live_cleanup_fds across increasing tree depths (1, 10, 50, 100).
    Proves that live kernel authority handles during cleanup remain strictly bounded
    and constant rather than scaling proportionally with tree depth.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    peaks = {}
    for depth in [1, 10, 50, 100]:
        caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
        os.close(caller_fd)

        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace
        )

        curr = subject.root_locator
        for i in range(depth):
            curr = curr / f"d{i}"
            curr.mkdir()
            (curr / "leaf.txt").write_text("deep")

        active_fds = set()
        peak_fds = 0
        orig_open = os.open
        orig_close = os.close

        def tracking_open(*args, **kwargs):
            nonlocal peak_fds
            fd = orig_open(*args, **kwargs)
            active_fds.add(fd)
            if len(active_fds) > peak_fds:
                peak_fds = len(active_fds)
            return fd

        def tracking_close(fd):
            active_fds.discard(fd)
            return orig_close(fd)

        with patch("os.open", side_effect=tracking_open), patch("os.close", side_effect=tracking_close):
            subject.close()

        peaks[depth] = peak_fds
        workspace.close()

    # Peak live descriptors opened by cleanup must be O(1) (<= 2) and bounded across all depths
    for depth in [1, 10, 50, 100]:
        assert peaks[depth] <= 2, f"Peak live FDs {peaks[depth]} exceeds constant bound for depth {depth}"
    assert peaks[10] == peaks[50] == peaks[100] == 2


def test_cm_c3_cleanup_fd_depth_causal_countermodel(tmp_path: Path):
    """CM-C3-CLEANUP-FD-DEPTH: prove causal refutation of ancestor descriptor accumulation under RLIMIT.

    When RLIMIT_NOFILE budget < tree depth:
    1. An ancestor-retaining descriptor stack exhausts available FDs (EMFILE), leaving directory residue.
    2. The resource-bounded cleanup succeeds completely with zero residue and zero leaked descriptors.
    """
    import resource

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    curr = subject.root_locator
    for i in range(40):
        curr = curr / f"d{i}"
        curr.mkdir()
        (curr / "leaf.txt").write_text("deep")

    # Constrain RLIMIT_NOFILE so remaining budget (15) < tree depth (40)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    current_fds = count_open_fds()
    target_limit = current_fds + 15

    resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, hard))
    try:
        subject.close()
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    assert subject._closed
    assert subject.root_fd == -1
    assert subject.pool_fd == -1
    assert not subject.root_locator.exists()
    assert count_open_fds() == initial_fds

    workspace.close()


def test_cm_c3_cleanup_hostile_mutation_descriptor_release(tmp_path: Path):
    """FilesystemDeletionFailure != AuthorityHandleLeak: verify unconditional FD release.

    If a host authority mutates returned subject namespace post-handoff preventing complete deletion
    (e.g. read-only permissions preventing unlink of child files), descriptor release and state
    invalidation remain strictly mandatory with zero descriptor leaks.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file").write_text("content")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    subject = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )

    # Post-handoff host mutation: create an undeletable file inside a locked subdirectory
    sub = subject.root_locator / "locked"
    sub.mkdir()
    (sub / "unremovable.txt").write_text("locked")
    sub.chmod(0o500)

    orig_unlink = os.unlink
    def injected_unlink(name, *, dir_fd=None):
        if name == "unremovable.txt":
            raise PermissionError("EACCES")
        return orig_unlink(name, dir_fd=dir_fd)

    try:
        with patch("os.unlink", side_effect=injected_unlink):
            subject.close()
    finally:
        if sub.exists():
            sub.chmod(0o700)

    assert subject._closed
    assert subject.root_fd == -1
    assert subject.pool_fd == -1
    # Zero descriptor leaks despite deletion failure
    assert count_open_fds() == initial_fds

    workspace.close()


def test_legacy_materialise_destination_cross_filesystem_exdev(tmp_path: Path):
    """Verify legacy materialise_commit_subject_v2 supports destinations mounted across filesystems (EXDEV)."""
    import errno
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello cross-fs")
    head = _commit_all(repo, "commit")

    dest = tmp_path / "cross_fs_dest"
    dest.mkdir()

    simulated_exdev = False

    def exdev_rename(src, dst):
        nonlocal simulated_exdev
        simulated_exdev = True
        err = OSError("Invalid cross-device link")
        err.errno = errno.EXDEV
        raise err

    with patch("os.rename", side_effect=exdev_rename):
        subject = materialise_commit_subject_v2(
            repo_root=repo,
            ref=head,
            destination=dest,
        )

    assert simulated_exdev
    assert subject.root == dest
    assert (dest / "file.txt").read_text() == "hello cross-fs"


def test_reject_non_blob_object_for_blob_entry(tmp_path: Path):
    """Verify read_commit_blobs_v2 rejects non-blob objects behind blob-mode entries."""
    import subprocess
    import pytest
    from app.agent_review.git_commit_subject_v2 import (
        read_commit_blobs_v2,
        TreeEntryV2,
        SubjectMaterialisationError,
        SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    )
    repo = tmp_path / "repo"
    _init_repo(repo)
    subdir = repo / "subdir"
    subdir.mkdir()
    (subdir / "inner.txt").write_text("inner")
    head = _commit_all(repo, "commit")

    tree_sha = subprocess.run(
        ["git", "rev-parse", f"{head}:subdir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    malformed_entry = TreeEntryV2(
        mode="100644",
        object_type="blob",
        object_id=tree_sha,
        path="bogus_file.txt",
    )

    with pytest.raises(SubjectMaterialisationError) as exc_info:
        read_commit_blobs_v2(repo_root=repo, entries=[malformed_entry])

    assert exc_info.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2


def test_reject_non_blob_object_for_symlink_entry(tmp_path: Path):
    """Verify read_commit_blobs_v2 rejects non-blob objects behind symlink-mode (120000) entries."""
    import subprocess
    import pytest
    from app.agent_review.git_commit_subject_v2 import (
        read_commit_blobs_v2,
        TreeEntryV2,
        SubjectMaterialisationError,
        SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    )
    repo = tmp_path / "repo"
    _init_repo(repo)
    subdir = repo / "subdir"
    subdir.mkdir()
    (subdir / "inner.txt").write_text("inner")
    head = _commit_all(repo, "commit")

    tree_sha = subprocess.run(
        ["git", "rev-parse", f"{head}:subdir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    malformed_entry = TreeEntryV2(
        mode="120000",
        object_type="blob",
        object_id=tree_sha,
        path="bogus_symlink",
    )

    with pytest.raises(SubjectMaterialisationError) as exc_info:
        read_commit_blobs_v2(repo_root=repo, entries=[malformed_entry])

    assert exc_info.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2


def test_cm_c3_listdir_failure_does_not_leak_child_fd(tmp_path: Path):
    """Verify child_fd is closed immediately if os.listdir raises an exception during directory creation."""
    import errno
    repo = tmp_path / "repo"
    _init_repo(repo)
    subdir = repo / "subdir"
    subdir.mkdir()
    (subdir / "inner.txt").write_text("inner")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    orig_listdir = os.listdir
    def failing_listdir(path):
        if isinstance(path, int):
            raise OSError(errno.EIO, "Simulated I/O error during listdir")
        return orig_listdir(path)

    with patch("os.listdir", side_effect=failing_listdir):
        with pytest.raises(SubjectMaterialisationError):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    # All descriptors including the failed child_fd must be closed
    assert count_open_fds() == initial_fds

    workspace.close()


def test_cm_c3_interruption_during_git_acquisition_rolls_back_and_releases_descriptors(tmp_path: Path):
    """Verify KeyboardInterrupt (BaseException) during Git acquisition rolls back the operation lease and conserves FDs."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    with patch("app.agent_review.git_commit_subject_v2.resolve_commit_v2", side_effect=KeyboardInterrupt("Simulated Ctrl+C")):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    # Operation lease pool_fd must be closed, returning FD count to initial
    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()


def test_cm_c3_interruption_during_materialisation_rolls_back_and_cleans_descriptors(tmp_path: Path):
    """Verify KeyboardInterrupt (BaseException) during trie materialization cleans descriptors and removes directory."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    with patch("app.agent_review.git_commit_subject_v2._materialise_trie_no_follow", side_effect=KeyboardInterrupt("Simulated Ctrl+C")):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    # root_fd and lease pool_fd must be closed, and c3_* directory unlinked
    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()


def test_cm_c3_interruption_during_epoch_commit_rolls_back(tmp_path: Path):
    """Verify BaseException during epoch commit triggers rollback."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    with patch("app.agent_review.git_commit_subject_v2.MaterialisedCommitSubjectCapabilityV2.__init__", side_effect=KeyboardInterrupt("Simulated Ctrl+C")):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()


def test_cm_c3_interruption_during_commit_ownership_mutation_cleans_capability(tmp_path: Path):
    """Verify BaseException during ownership mutation inside epoch.commit cleans capability and rolls back."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    orig_init = MaterialisedCommitSubjectCapabilityV2.__init__
    def failing_mutation(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        raise KeyboardInterrupt("Simulated Ctrl+C right after capability creation")

    with patch.object(MaterialisedCommitSubjectCapabilityV2, "__init__", failing_mutation):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    # Capability and epoch must be fully cleaned up
    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()


def test_cm_c3_interruption_between_commit_and_caller_return_cleans_capability(tmp_path: Path):
    """Verify BaseException after epoch.commit returns but before returning to caller cleans capability."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    from app.agent_review.git_commit_subject_v2 import MaterialisationEpochV2
    orig_commit = MaterialisationEpochV2.commit
    def interrupting_commit(self, *args, **kwargs):
        orig_commit(self, *args, **kwargs)
        raise KeyboardInterrupt("Simulated Ctrl+C before caller receives capability")

    with patch.object(MaterialisationEpochV2, "commit", interrupting_commit):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()


def test_cm_c3_abandoned_capability_closed_by_finalizer(tmp_path: Path):
    """Verify abandoned capability is cleaned up when collected by finalizer."""
    import gc
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello")
    head = _commit_all(repo, "commit")

    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    initial_fds = count_open_fds()

    cap = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace
    )
    assert count_open_fds() > initial_fds
    assert any(tmp_path.glob("c3_*"))

    # Abandon cap without calling cap.close()
    del cap
    gc.collect()

    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))

    workspace.close()
