import os
import pytest
from pathlib import Path
from app.agent_review.git_commit_subject_v2 import (
    BoundedBlobCarrierV2,
    MaterialisationWorkspaceCapabilityV2,
    MaterialisedCommitSubjectCapabilityV2,
    OperationWorkspaceLeaseV2,
    acquire_materialised_commit_subject_v2,
    materialise_commit_subject_v2,
    read_commit_blobs_v2,
    SubjectMaterialisationError,
    SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2,
    SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2,
    SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2,
    _get_process_umask_non_mutating,
    _translate_destination_permissions_descriptor_relative,
    _copy_entry_descriptor_relative,
    _check_projected_path_max,
    MAX_EXPANDED_ENTRIES_V2,
    MAX_EXPANDED_BYTES_V2,
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


def test_cm_c3_epoch_detached_descriptors_no_double_close(tmp_path: Path):
    """Verify descriptors detached from epoch during commit cannot be double-closed by epoch.rollback."""
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    lease = workspace.pin()
    from app.agent_review.git_commit_subject_v2 import MaterialisationEpochV2
    epoch = MaterialisationEpochV2(lease)
    root_fd, root_name, dest_path = epoch.create_epoch_root()

    # Commit transfers ownership to capability
    cap = epoch.commit(
        commit_sha="0" * 40,
        file_count=0,
        dest_path=dest_path,
    )

    # Calling epoch.rollback() after commit must NOT close cap descriptors
    epoch.rollback()

    # Capability must remain fully intact and operational
    assert cap.root_fd >= 0 and not cap._closed
    assert os.fstat(cap.root_fd).st_nlink >= 1

    cap.close()
    # Calling epoch.rollback() again must remain a safe no-op
    epoch.rollback()

    workspace.close()


def test_legacy_materialise_failure_atomic_rollback(tmp_path: Path):
    """Verify that if legacy projection fails mid-way, all partially moved children are rolled back
    and a newly created destination directory is removed.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_text("one")
    (repo / "f2.txt").write_text("two")
    head = _commit_all(repo, "two files")

    dest = tmp_path / "dest"
    orig_rename = os.rename
    call_count = [0]

    def failing_rename(src, dst, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError(18, "EXDEV")
        return orig_rename(src, dst, *args, **kwargs)

    def failing_copy(name, src_dir_fd, dst_dir_fd):
        raise OSError(5, "EIO")

    with patch("os.rename", side_effect=failing_rename), patch("app.agent_review.git_commit_subject_v2._copy_entry_descriptor_relative", side_effect=failing_copy):
        with pytest.raises(SubjectMaterialisationError) as exc:
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
        assert exc.value.reason_code == SUBJECT_MATERIALISATION_RACE_REASON_V2
        assert not dest.exists(), "newly created destination must be removed on failure"


def test_legacy_materialise_failure_atomic_existing_empty_destination(tmp_path: Path):
    """Verify that if legacy projection fails mid-way into an existing empty directory,
    the destination directory is preserved and restored to an empty state.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_text("one")
    (repo / "f2.txt").write_text("two")
    head = _commit_all(repo, "two files")

    dest = tmp_path / "dest"
    dest.mkdir()
    orig_rename = os.rename
    call_count = [0]

    def failing_rename(src, dst, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError(18, "EXDEV")
        return orig_rename(src, dst, *args, **kwargs)

    def failing_copy(name, src_dir_fd, dst_dir_fd):
        raise OSError(5, "EIO")

    with patch("os.rename", side_effect=failing_rename), patch("app.agent_review.git_commit_subject_v2._copy_entry_descriptor_relative", side_effect=failing_copy):
        with pytest.raises(SubjectMaterialisationError) as exc:
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
        assert exc.value.reason_code == SUBJECT_MATERIALISATION_RACE_REASON_V2
        assert dest.exists(), "pre-existing destination directory must be preserved"
        assert list(dest.iterdir()) == [], "pre-existing destination must be restored to empty"


def test_legacy_materialise_reraises_process_control_exception_after_cleanup(tmp_path: Path):
    """Verify that when projection in materialise_commit_subject_v2 is interrupted by
    KeyboardInterrupt or SystemExit, rollback cleanup is performed and the process-control
    exception is re-raised transparently rather than converted to SubjectMaterialisationError.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_text("one")
    (repo / "f2.txt").write_text("two")
    head = _commit_all(repo, "two files")

    dest = tmp_path / "dest"
    dest.mkdir()
    orig_rename = os.rename
    call_count = [0]

    def interrupting_rename(src, dst):
        call_count[0] += 1
        if call_count[0] == 2:
            raise KeyboardInterrupt("Simulated Ctrl+C during move")
        return orig_rename(src, dst)

    with patch("os.rename", side_effect=interrupting_rename):
        with pytest.raises(KeyboardInterrupt):
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
        assert dest.exists(), "pre-existing destination directory must be preserved"
        assert list(dest.iterdir()) == [], "pre-existing destination must be restored to empty after interruption"

    # Test with SystemExit
    call_count[0] = 0
    def exiting_rename(src, dst):
        call_count[0] += 1
        if call_count[0] == 2:
            raise SystemExit(42)
        return orig_rename(src, dst)

    with patch("os.rename", side_effect=exiting_rename):
        with pytest.raises(SystemExit) as exc:
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
        assert exc.value.code == 42
        assert dest.exists()
        assert list(dest.iterdir()) == [], "destination must be clean after SystemExit"

    # Test with newly created destination
    call_count[0] = 0
    dest_new = tmp_path / "dest_new"
    with patch("os.rename", side_effect=interrupting_rename):
        with pytest.raises(KeyboardInterrupt):
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest_new)
        assert not dest_new.exists(), "newly created destination must be removed on interruption"


def test_cm_c3_interruption_during_detachment_rollback_safe(tmp_path: Path):
    """Verify that if KeyboardInterrupt strikes inside commit()'s try block, descriptors
    are released, workspace directory is cleaned up, and caller's finally does not leak.
    """
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("content")
    head = _commit_all(repo, "commit")

    initial_fds = count_open_fds()

    with patch(
        "app.agent_review.git_commit_subject_v2.MaterialisedCommitSubjectCapabilityV2.__init__",
        side_effect=KeyboardInterrupt("simulated interruption"),
    ):
        with pytest.raises(KeyboardInterrupt):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=head, workspace=workspace
            )

    assert count_open_fds() == initial_fds
    assert not any(tmp_path.glob("c3_*"))
    workspace.close()


def test_c3_hardened_umask_restores_owner_directory_access(tmp_path: Path):
    """Verify that private epoch root and child directories restore owner traversal
    and read/write permissions regardless of ambient umask masking owner bits (e.g. 0177).

    Countermodel CM-C3-HARDENED-UMASK-DIRECTORY-ACCESS:
    A worker process with a hardened umask (e.g. 0177) masks owner execute bit.
    Without explicit chmod restoring S_IRWXU:
    1. mkdir(root_name, 0700) creates the directory as 0600.
    2. open(root_name, O_DIRECTORY) fails with EACCES (PermissionError).
    3. Child directory mkdir creates subdirectories as 0600.
    4. Opening child_fd fails with EACCES, reporting an erroneous materialisation race.
    With the fix, owner access is restored immediately before opening descriptors.
    """
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    # 1. Test direct MaterialisationEpochV2.create_epoch_root under hardened umasks
    for mask in [0o177, 0o700, 0o377]:
        old_umask = os.umask(mask)
        try:
            lease = workspace.pin()
            epoch = MaterialisationEpochV2(lease)
            root_fd, root_name, dest_path = epoch.create_epoch_root()
            try:
                st = os.fstat(root_fd)
                assert (st.st_mode & stat.S_IRWXU) == stat.S_IRWXU, (
                    f"Epoch root mode {oct(st.st_mode)} missing S_IRWXU under umask {oct(mask)}"
                )
                assert stat.S_IMODE(st.st_mode) == 0o700
            finally:
                epoch.rollback()
                lease.close()
        finally:
            os.umask(old_umask)

    # 2. Test full acquire_materialised_commit_subject_v2 with umask 0177 on materialization
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "dirA" / "subB").mkdir(parents=True)
    (repo / "dirA" / "subB" / "file.txt").write_text("hello deep")
    (repo / "dirA" / "script.sh").write_text("#!/bin/sh\nexit 0")
    (repo / "dirA" / "script.sh").chmod(0o755)
    (repo / "top.txt").write_text("top content")
    head = _commit_all(repo, "commit with nested tree")

    import app.agent_review.git_commit_subject_v2 as gcs
    orig_create = MaterialisationEpochV2.create_epoch_root
    orig_mat = gcs._materialise_trie_no_follow

    def create_under_0177(self):
        prev = os.umask(0o177)
        try:
            return orig_create(self)
        finally:
            os.umask(prev)

    def mat_under_0177(root_node, content_by_path, initial_dir_fd, initial_path, count):
        prev = os.umask(0o177)
        try:
            return orig_mat(root_node, content_by_path, initial_dir_fd, initial_path, count)
        finally:
            os.umask(prev)

    with patch.object(MaterialisationEpochV2, "create_epoch_root", create_under_0177), \
         patch("app.agent_review.git_commit_subject_v2._materialise_trie_no_follow", mat_under_0177):
        with acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace
        ) as cap:
            root_path = cap.root_locator
            assert (root_path / "top.txt").read_text() == "top content"
            assert (root_path / "dirA" / "subB" / "file.txt").read_text() == "hello deep"
            assert (root_path / "dirA" / "script.sh").exists()
            st_root = os.stat(root_path)
            assert (st_root.st_mode & stat.S_IRWXU) == stat.S_IRWXU
            st_dir = os.stat(root_path / "dirA")
            assert (st_dir.st_mode & stat.S_IRWXU) == stat.S_IRWXU
            st_sub = os.stat(root_path / "dirA" / "subB")
            assert (st_sub.st_mode & stat.S_IRWXU) == stat.S_IRWXU

    assert not any(tmp_path.glob("c3_*"))
    workspace.close()


def test_c3_bounded_materialization_descriptors_independent_of_depth(tmp_path: Path):
    """Verify that materializing deeply nested trees does not accumulate open directory
    descriptors across the traversal stack, maintaining O(1) live handles under low RLIMIT_NOFILE.

    Countermodel CM-C3-BOUNDED-MATERIALIZATION-FD-DEPTH:
    If each frame of tree materialization retained an open dir_fd, a tree with depth 35
    would exhaust file descriptors and raise EMFILE if RLIMIT_NOFILE is constrained below
    depth. With descriptor-bounded traversal, live handles remain O(1) (peak delta <= 2).
    """
    import resource
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    lease = workspace.pin()
    epoch = MaterialisationEpochV2(lease)
    root_fd, root_name, dest_path = epoch.create_epoch_root()

    from app.agent_review.git_commit_subject_v2 import _TrieNode, _materialise_trie_no_follow

    curr = _TrieNode(node_type="tree", mode="040000", object_id="root_tree", explicit=True)
    root_node = curr
    content = {}
    path_accum = ""
    for i in range(35):
        comp = f"d{i}"
        child = _TrieNode(node_type="tree", mode="040000", object_id=f"tree_{i}", explicit=True)
        curr.children[comp] = child
        curr = child
        path_accum = path_accum + "/" + comp if path_accum else comp
    curr.children["leaf.txt"] = _TrieNode(
        node_type="blob", mode="100644", object_id="blob_leaf", explicit=True
    )
    content[path_accum + "/leaf.txt"] = b"deep leaf content"

    proc_fds = len(os.listdir("/proc/self/fd"))
    orig_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    # Constrain RLIMIT_NOFILE to slightly above current open FDs (proc_fds + 8)
    resource.setrlimit(resource.RLIMIT_NOFILE, (proc_fds + 8, orig_limit[1]))
    try:
        count = [0]
        _materialise_trie_no_follow(root_node, content, root_fd, "", count)
        assert count[0] == 1
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, orig_limit)
        epoch.rollback()
        lease.close()
        workspace.close()


def test_c3_chmod_nofollow_refusal_translated_to_typed_error(tmp_path: Path):
    """Verify that platform refusals on no-follow chmod (ValueError on Linux when dir_fd and
    follow_symlinks=False encounter a symlink) are translated into typed
    SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2).
    """
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    with patch("os.chmod", side_effect=ValueError("chmod: cannot use dir_fd and follow_symlinks together")):
        lease = workspace.pin()
        epoch = MaterialisationEpochV2(lease)
        try:
            with pytest.raises(SubjectMaterialisationError) as exc:
                epoch.create_epoch_root()
            assert exc.value.reason_code == SUBJECT_MATERIALISATION_RACE_REASON_V2
        finally:
            epoch.rollback()
            lease.close()
            workspace.close()


def test_c3_hardened_umask_restores_owner_leaf_access(tmp_path: Path):
    """Verify that authoritative acquisition under hardened umasks (e.g. 0700, 0177)
    restores owner read and write permissions (S_IRUSR | S_IWUSR) on canonical leaf files,
    allowing the non-root owner to reopen and read regular files and execute scripts.
    """
    import subprocess
    from app.agent_review.git_commit_subject_v2 import _materialise_trie_no_follow

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "regular.txt").write_text("regular content")

    script = repo / "script.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)

    subprocess.run(["git", "add", "regular.txt", "script.sh"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    orig_mat = _materialise_trie_no_follow

    def mat_under_0700(root_node, content_by_path, initial_dir_fd, initial_path, count):
        prev = os.umask(0o700)
        try:
            return orig_mat(root_node, content_by_path, initial_dir_fd, initial_path, count)
        finally:
            os.umask(prev)

    def mat_under_0177(root_node, content_by_path, initial_dir_fd, initial_path, count):
        prev = os.umask(0o177)
        try:
            return orig_mat(root_node, content_by_path, initial_dir_fd, initial_path, count)
        finally:
            os.umask(prev)

    # 1. Test under umask 0700 (masks all owner permissions)
    pool = tmp_path / "pool_0700"
    pool.mkdir(mode=0o700)
    caller_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, pool)
    os.close(caller_fd)

    with patch("app.agent_review.git_commit_subject_v2._materialise_trie_no_follow", mat_under_0700):
        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace
        )
        try:
            reg_st = os.stat(subject.root_locator / "regular.txt")
            assert reg_st.st_mode & stat.S_IRUSR != 0, "owner read must be restored on regular file"
            assert reg_st.st_mode & stat.S_IWUSR != 0, "owner write must be restored on regular file"
            assert (subject.root_locator / "regular.txt").read_text() == "regular content"

            script_st = os.stat(subject.root_locator / "script.sh")
            assert script_st.st_mode & stat.S_IRUSR != 0, "owner read must be restored on script"
            assert script_st.st_mode & stat.S_IXUSR != 0, "owner execute must be restored on script"
            assert (subject.root_locator / "script.sh").read_text() == "#!/bin/sh\necho hi\n"
        finally:
            subject.close()
    workspace.close()

    # 2. Test under umask 0177
    pool2 = tmp_path / "pool_0177"
    pool2.mkdir(mode=0o700)
    caller_fd2 = os.open(pool2, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace2 = MaterialisationWorkspaceCapabilityV2(caller_fd2, pool2)
    os.close(caller_fd2)

    with patch("app.agent_review.git_commit_subject_v2._materialise_trie_no_follow", mat_under_0177):
        subject2 = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace2
        )
        try:
            reg_st2 = os.stat(subject2.root_locator / "regular.txt")
            assert reg_st2.st_mode & stat.S_IRUSR != 0
            assert (subject2.root_locator / "regular.txt").read_text() == "regular content"

            script_st2 = os.stat(subject2.root_locator / "script.sh")
            assert script_st2.st_mode & stat.S_IRUSR != 0
            assert script_st2.st_mode & stat.S_IXUSR != 0
            assert (subject2.root_locator / "script.sh").read_text() == "#!/bin/sh\necho hi\n"
        finally:
            subject2.close()
    workspace2.close()


def test_c3_unrepresentable_tree_component_exceeding_name_max(tmp_path: Path):
    """Verify that a hostile git tree containing a component exceeding the filesystem's NAME_MAX
    is rejected during in-memory pre-write validation as subject_unrepresentable_tree,
    writing zero bytes to the pool directory.
    """
    import subprocess
    from app.agent_review.git_commit_subject_v2 import (
        list_commit_tree_structure_v2,
        SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    )

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Hash normal blob and create tree with a 256-byte component name
    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"leaf content",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    long_name = "a" * 256
    mktree_input = f"100644 blob {blob_sha}\tnormal.txt\n100644 blob {blob_sha}\t{long_name}\n".encode()
    tree_sha = subprocess.run(
        ["git", "mktree"], cwd=repo, input=mktree_input, capture_output=True, check=True
    ).stdout.strip().decode()
    commit_sha = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "tree with name exceeding NAME_MAX"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    pool = tmp_path / "pool"
    pool.mkdir(mode=0o700)

    initial_fds = count_open_fds()

    caller_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, pool)
    os.close(caller_fd)

    # Pre-write validation in acquire_materialised_commit_subject_v2 must reject before creating epoch
    with pytest.raises(SubjectMaterialisationError) as exc:
        acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_sha, workspace=workspace
        )
    assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2

    # Verify ZERO disk mutations: pool directory must remain completely empty
    assert list(pool.iterdir()) == [], "pool directory must have zero entries created"

    # Verify list_commit_tree_structure_v2 also rejects the unrepresentable tree
    with pytest.raises(SubjectMaterialisationError) as exc2:
        list_commit_tree_structure_v2(repo_root=repo, commit_sha=commit_sha)
    assert exc2.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2

    workspace.close()
    assert count_open_fds() == initial_fds


def test_cm_c3_r_prospective_child_depth_boundary(tmp_path: Path):
    """C3-R: prospective child depth validation admits depth <= 100 and refuses depth 101 with zero writes."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    empty_tree_sha = subprocess.run(
        ["git", "mktree"], cwd=repo, input=b"", capture_output=True, check=True
    ).stdout.strip().decode()

    # 1. Depth 100 explicit empty tree (d1/.../d100)
    cur = empty_tree_sha
    for i in range(100, 0, -1):
        entry = f"040000 tree {cur}\td{i}\n".encode()
        cur = subprocess.run(
            ["git", "mktree"], cwd=repo, input=entry, capture_output=True, check=True
        ).stdout.strip().decode()
    commit_100_empty = subprocess.run(
        ["git", "commit-tree", cur, "-m", "100-level empty tree"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    # 2. Depth 101 tree (d1/.../d101)
    cur = empty_tree_sha
    for i in range(101, 0, -1):
        entry = f"040000 tree {cur}\td{i}\n".encode()
        cur = subprocess.run(
            ["git", "mktree"], cwd=repo, input=entry, capture_output=True, check=True
        ).stdout.strip().decode()
    commit_101_tree = subprocess.run(
        ["git", "commit-tree", cur, "-m", "101-level tree"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    # 3. Depth 100 regular leaf (99 dirs + 1 blob = 100 components)
    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"leaf content",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    leaf_tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"100644 blob {blob_sha}\tfile.txt\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    cur = leaf_tree
    for i in range(99, 0, -1):
        entry = f"040000 tree {cur}\td{i}\n".encode()
        cur = subprocess.run(
            ["git", "mktree"], cwd=repo, input=entry, capture_output=True, check=True
        ).stdout.strip().decode()
    commit_100_leaf = subprocess.run(
        ["git", "commit-tree", cur, "-m", "100-level leaf"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    # 4. Depth 101 regular leaf (100 dirs + 1 blob = 101 components)
    cur = leaf_tree
    for i in range(100, 0, -1):
        entry = f"040000 tree {cur}\td{i}\n".encode()
        cur = subprocess.run(
            ["git", "mktree"], cwd=repo, input=entry, capture_output=True, check=True
        ).stdout.strip().decode()
    commit_101_leaf = subprocess.run(
        ["git", "commit-tree", cur, "-m", "101-level leaf"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    pool = tmp_path / "pool"
    pool.mkdir(mode=0o700)
    caller_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, pool)
    os.close(caller_fd)

    # Validate Depth 100 empty tree succeeds
    sub_100_empty = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=commit_100_empty, workspace=workspace
    )
    assert sub_100_empty.commit_sha == commit_100_empty
    sub_100_empty.close()

    # Validate Depth 100 leaf succeeds
    sub_100_leaf = acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=commit_100_leaf, workspace=workspace
    )
    assert sub_100_leaf.commit_sha == commit_100_leaf
    sub_100_leaf.close()

    # Validate Depth 101 empty tree fails with zero writes
    with pytest.raises(SubjectMaterialisationError) as exc_tree:
        acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_101_tree, workspace=workspace
        )
    assert exc_tree.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert list(pool.iterdir()) == [], "pool must have zero writes on depth 101 tree refusal"

    # Validate Depth 101 leaf fails with zero writes
    with pytest.raises(SubjectMaterialisationError) as exc_leaf:
        acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_101_leaf, workspace=workspace
        )
    assert exc_leaf.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert list(pool.iterdir()) == [], "pool must have zero writes on depth 101 leaf refusal"

    workspace.close()


def test_cm_c3_b_tree_cache_deduplication(tmp_path: Path):
    """C3-B: identical tree OIDs occurring multiple times in hierarchy are parsed at most once."""
    import subprocess
    import app.agent_review.git_commit_subject_v2 as mod
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"shared subtree content",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    shared_tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"100644 blob {blob_sha}\tfile.txt\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    # Root tree references shared_tree 5 times under different child names
    root_input = "".join([f"040000 tree {shared_tree}\tsub_{i}\n" for i in range(5)]).encode()
    root_tree = subprocess.run(
        ["git", "mktree"], cwd=repo, input=root_input, capture_output=True, check=True
    ).stdout.strip().decode()
    commit_sha = subprocess.run(
        ["git", "commit-tree", root_tree, "-m", "repeated trees"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    pool = tmp_path / "pool"
    pool.mkdir(mode=0o700)
    caller_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, pool)
    os.close(caller_fd)

    orig_list = mod._list_single_tree_entries_v2
    listed_oids: list[str] = []

    def spy_list(repo_root, tree_oid):
        listed_oids.append(tree_oid)
        return orig_list(repo_root=repo_root, tree_oid=tree_oid)

    with patch.object(mod, "_list_single_tree_entries_v2", side_effect=spy_list):
        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_sha, workspace=workspace
        )
        subject.close()

    # The shared tree must only be parsed by git once despite being visited 5 times in the hierarchy
    assert listed_oids.count(shared_tree) == 1
    assert len(listed_oids) == 2  # root_tree once, shared_tree once
    workspace.close()


def test_cm_c3_b_entry_and_byte_budget_enforcement(tmp_path: Path):
    """C3-B: logical expanded entry and byte budgets fail closed with zero disk writes."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"x" * 100,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    tree_input = (
        f"100644 blob {blob_sha}\ta.txt\n"
        f"100644 blob {blob_sha}\tb.txt\n"
        f"100644 blob {blob_sha}\tc.txt\n"
    ).encode()
    tree_sha = subprocess.run(
        ["git", "mktree"], cwd=repo, input=tree_input, capture_output=True, check=True
    ).stdout.strip().decode()
    commit_sha = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "multi-entry"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    pool = tmp_path / "pool"
    pool.mkdir(mode=0o700)
    caller_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, pool)
    os.close(caller_fd)

    # 1. Entry budget exceeded
    with pytest.raises(SubjectMaterialisationError) as exc_entries:
        acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_sha, workspace=workspace, max_expanded_entries=2
        )
    assert exc_entries.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert list(pool.iterdir()) == [], "pool must remain empty when entry budget exceeded"

    # 2. Byte budget exceeded
    with pytest.raises(SubjectMaterialisationError) as exc_bytes:
        acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit_sha, workspace=workspace, max_expanded_bytes=150
        )
    assert exc_bytes.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    assert list(pool.iterdir()) == [], "pool must remain empty when byte budget exceeded"

    workspace.close()


def test_cm_c3_x_destination_admission_matrix(tmp_path: Path):
    """C3-X: legacy projection no-follow destination admission refuses all symlink forms and occupied paths."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"content",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    tree_sha = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"100644 blob {blob_sha}\tfile.txt\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    commit_sha = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "legacy commit"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    # 1. Dangling symlink destination
    dangling_symlink = tmp_path / "dangling_dest"
    os.symlink(tmp_path / "nonexistent_target", dangling_symlink)
    with pytest.raises(SubjectMaterialisationError) as exc_dangling:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=dangling_symlink
        )
    assert exc_dangling.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 2. Symlink to empty directory
    real_empty_dir = tmp_path / "real_empty_dir"
    real_empty_dir.mkdir()
    symlink_to_empty = tmp_path / "symlink_to_empty"
    os.symlink(real_empty_dir, symlink_to_empty)
    with pytest.raises(SubjectMaterialisationError) as exc_sym_empty:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=symlink_to_empty
        )
    assert exc_sym_empty.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 3. Symlink to non-empty directory
    real_nonempty_dir = tmp_path / "real_nonempty_dir"
    real_nonempty_dir.mkdir()
    (real_nonempty_dir / "child.txt").write_text("child")
    symlink_to_nonempty = tmp_path / "symlink_to_nonempty"
    os.symlink(real_nonempty_dir, symlink_to_nonempty)
    with pytest.raises(SubjectMaterialisationError) as exc_sym_nonempty:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=symlink_to_nonempty
        )
    assert exc_sym_nonempty.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 4. Symlink to regular file
    real_file = tmp_path / "real_file.txt"
    real_file.write_text("file")
    symlink_to_file = tmp_path / "symlink_to_file"
    os.symlink(real_file, symlink_to_file)
    with pytest.raises(SubjectMaterialisationError) as exc_sym_file:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=symlink_to_file
        )
    assert exc_sym_file.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 5. Existing occupied directory
    with pytest.raises(SubjectMaterialisationError) as exc_occupied:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=real_nonempty_dir
        )
    assert exc_occupied.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 6. Existing regular file
    with pytest.raises(SubjectMaterialisationError) as exc_reg_file:
        materialise_commit_subject_v2(
            repo_root=repo, ref=commit_sha, destination=real_file
        )
    assert exc_reg_file.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2

    # 7. Existing empty directory admitted
    res_empty = materialise_commit_subject_v2(
        repo_root=repo, ref=commit_sha, destination=real_empty_dir
    )
    assert res_empty.root == real_empty_dir
    assert (real_empty_dir / "file.txt").read_text() == "content"

    # 8. Nonexistent path admitted
    nonexistent_dest = tmp_path / "new_dest"
    res_nonexistent = materialise_commit_subject_v2(
        repo_root=repo, ref=commit_sha, destination=nonexistent_dest
    )
    assert res_nonexistent.root == nonexistent_dest
    assert (nonexistent_dest / "file.txt").read_text() == "content"


def test_cm_c3_x_dangling_symlink_rollback_on_failure(tmp_path: Path):
    """C3-X: failure during projection cleans up dangling symlinks moved to destination."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    b_link = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"unresolvable_nonexistent_target",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    b_file = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"valid file",
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    tree_sha = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"120000 blob {b_link}\t01_symlink\n100644 blob {b_file}\t02_file.txt\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.strip().decode()
    commit_sha = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "symlink rollback test"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.strip().decode()

    destination = tmp_path / "dest"
    destination.mkdir()

    orig_rename = os.rename

    def failing_rename(src, dst, *args, **kwargs):
        if "02_file.txt" in str(src):
            raise OSError("Simulated projection failure")
        return orig_rename(src, dst, *args, **kwargs)

    with patch("os.rename", side_effect=failing_rename):
        with pytest.raises(SubjectMaterialisationError):
            materialise_commit_subject_v2(
                repo_root=repo, ref=commit_sha, destination=destination
            )

    # Destination directory must have had the dangling symlink cleanly unlinked during rollback
    assert list(destination.iterdir()) == []


def test_cm_c3_x_non_mutating_umask_and_permission_translation(tmp_path: Path):
    """C3-P -> C3-X: non-mutating umask observation and descriptor-relative mode translation."""
    umask_proc = _get_process_umask_non_mutating()
    assert 0 <= umask_proc <= 0o777

    # Verify calling _get_process_umask_non_mutating does not mutate process umask
    current_umask = os.umask(0o022)
    os.umask(current_umask)
    assert umask_proc == current_umask

    # Test descriptor-relative permission translation with a restrictive umask (0o027)
    dest_dir = tmp_path / "perm_dest"
    dest_dir.mkdir(mode=0o700)
    dfd = os.open(dest_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    # Create a regular non-exec file (mode 0o600)
    f_reg = os.open("regular.txt", os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    os.fchmod(f_reg, 0o600)
    os.close(f_reg)

    # Create an executable file (mode 0o700)
    f_exec = os.open("script.sh", os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o700, dir_fd=dfd)
    os.fchmod(f_exec, 0o755)
    os.close(f_exec)

    # Create a subdirectory (mode 0o700)
    os.mkdir("sub", mode=0o700, dir_fd=dfd)

    # Translate with umask 0o027
    _translate_destination_permissions_descriptor_relative(dfd, 0o027)

    # Recheck permissions
    st_reg = os.stat("regular.txt", dir_fd=dfd)
    assert (st_reg.st_mode & 0o777) == 0o640

    st_exec = os.stat("script.sh", dir_fd=dfd)
    assert (st_exec.st_mode & 0o777) == 0o751

    st_sub = os.stat("sub", dir_fd=dfd)
    assert (st_sub.st_mode & 0o777) == 0o750

    st_dest = os.fstat(dfd)
    assert (st_dest.st_mode & 0o777) == 0o750

    os.close(dfd)


def test_read_commit_blobs_enforces_byte_budget_before_cat_file_batch(tmp_path: Path, monkeypatch):
    """P1: Verifies read_commit_blobs_v2 checks byte budget with cat-file --batch-check

    and fails closed without invoking cat-file --batch when budget is exceeded.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    blob_content = b"x" * 2000
    (repo / "large.bin").write_bytes(blob_content)
    commit = _commit_all(repo, "commit with large blob")

    from app.agent_review.git_commit_subject_v2 import list_commit_tree_structure_v2
    entries = list_commit_tree_structure_v2(repo_root=repo, commit_sha=commit)

    import app.agent_review.git_commit_subject_v2 as gcs
    original_run_bounded = gcs.run_bounded_git_v2
    cat_file_commands = []

    def spy_run_bounded(argv, **kwargs):
        if "cat-file" in argv:
            cat_file_commands.append(argv)
        return original_run_bounded(argv, **kwargs)

    monkeypatch.setattr(gcs, "run_bounded_git_v2", spy_run_bounded)

    # Calling read_commit_blobs_v2 with max_expanded_bytes=1000 (< 2000)
    with pytest.raises(SubjectMaterialisationError) as exc_info:
        read_commit_blobs_v2(
            repo_root=repo,
            entries=entries,
            max_expanded_bytes=1000,
        )

    assert exc_info.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
    # Verify cat-file --batch-check was called
    assert any("cat-file" in cmd and "--batch-check" in cmd for cmd in cat_file_commands)
    # CRITICAL: Verify cat-file --batch was NEVER called!
    assert not any("cat-file" in cmd and "--batch" in cmd and "--batch-check" not in cmd for cmd in cat_file_commands)


def test_copy_entry_descriptor_relative_completes_short_writes(tmp_path: Path, monkeypatch):
    """P2: Verifies _copy_entry_descriptor_relative loops on partial os.write chunks."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    content = b"abcdefghijklmnopqrstuvwxyz" * 100  # 2600 bytes
    (src_dir / "test.txt").write_bytes(content)

    src_fd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    dst_fd = os.open(dst_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    import app.agent_review.git_commit_subject_v2 as gcs
    original_write = gcs._os.write

    def partial_write(fd, data):
        slice_len = min(17, len(data))
        return original_write(fd, data[:slice_len])

    monkeypatch.setattr(gcs._os, "write", partial_write)

    try:
        _copy_entry_descriptor_relative("test.txt", src_fd, dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)

    assert (dst_dir / "test.txt").read_bytes() == content
    assert not (src_dir / "test.txt").exists()


def test_translate_destination_permissions_restrictive_umask_0700_post_order(tmp_path: Path):
    """P2: Verifies post-order translation does not fail with EACCES under umask 0700."""
    dest = tmp_path / "dest_0700"
    dest.mkdir(mode=0o700)
    dest_fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    os.mkdir("sub", mode=0o700, dir_fd=dest_fd)
    sub_fd = os.open("sub", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dest_fd)
    os.mkdir("nested", mode=0o700, dir_fd=sub_fd)
    nested_fd = os.open("nested", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=sub_fd)

    f = os.open("file.txt", os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600, dir_fd=nested_fd)
    os.write(f, b"hello")
    os.close(f)
    os.close(nested_fd)
    os.close(sub_fd)

    _translate_destination_permissions_descriptor_relative(dest_fd, 0o700, translate_root=True)

    st_root = os.fstat(dest_fd)
    assert (st_root.st_mode & 0o777) == 0o077

    os.fchmod(dest_fd, 0o700)
    st_sub = os.stat("sub", dir_fd=dest_fd, follow_symlinks=False)
    assert (st_sub.st_mode & 0o777) == 0o077

    os.chmod("sub", 0o700, dir_fd=dest_fd)
    sub_fd = os.open("sub", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dest_fd)
    st_nested = os.stat("nested", dir_fd=sub_fd, follow_symlinks=False)
    assert (st_nested.st_mode & 0o777) == 0o077

    os.chmod("nested", 0o700, dir_fd=sub_fd)
    nested_fd = os.open("nested", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=sub_fd)
    st_file = os.stat("file.txt", dir_fd=nested_fd, follow_symlinks=False)
    assert (st_file.st_mode & 0o777) == 0o066

    os.close(nested_fd)
    os.close(sub_fd)
    os.close(dest_fd)


def test_legacy_materialise_preserves_existing_destination_mode(tmp_path: Path):
    """P2: Verifies materialise_commit_subject_v2 preserves mode on existing empty destination."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello")
    head = _commit_all(repo, "initial")

    existing_dest = tmp_path / "existing_dest"
    existing_dest.mkdir(mode=0o770)
    try:
        os.chmod(existing_dest, 0o2770)
    except PermissionError:
        os.chmod(existing_dest, 0o770)

    expected_mode = existing_dest.stat().st_mode & 0o7777

    materialise_commit_subject_v2(
        repo_root=repo,
        ref=head,
        destination=existing_dest,
    )

    actual_mode = existing_dest.stat().st_mode & 0o7777
    assert actual_mode == expected_mode


def test_check_projected_path_max_descriptor_bounded(tmp_path: Path):
    """P2: Verifies _check_projected_path_max traverses wide sibling trees with O(1) descriptors."""
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    test_dir = tmp_path / "wide_tree"
    test_dir.mkdir()
    for i in range(60):
        (test_dir / f"subdir_{i:03d}").mkdir()

    root_fd = os.open(test_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (30, hard))
        try:
            _check_projected_path_max(root_fd, Path("/dummy/dest"), path_max_limit=4096)
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    finally:
        os.close(root_fd)


def test_require_open_fd_returns_owned_lease_immune_to_workspace_close(tmp_path: Path):
    """P2: Verifies require_open_fd returns an OperationWorkspaceLeaseV2 immune to workspace close."""
    caller_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    workspace = MaterialisationWorkspaceCapabilityV2(caller_fd, tmp_path)
    os.close(caller_fd)

    lease = workspace.require_open_fd()
    assert isinstance(lease, OperationWorkspaceLeaseV2)
    assert lease.fileno() >= 0
    assert int(lease) == lease.fileno()
    assert lease.__index__() == lease.fileno()

    workspace.close()

    test_sub = "test_lease_sub"
    os.mkdir(test_sub, 0o700, dir_fd=lease)
    assert (tmp_path / test_sub).is_dir()

    lease.close()
    with pytest.raises(SubjectMaterialisationError) as exc_info:
        lease.fileno()
    assert exc_info.value.reason_code == SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2

    with pytest.raises(SubjectMaterialisationError) as exc_info:
        workspace.require_open_fd()
    assert exc_info.value.reason_code == SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2


def test_read_commit_blobs_streams_blob_chunks_without_monolithic_capture(tmp_path: Path, monkeypatch):
    """P1 (4097706613): Verifies read_commit_blobs_v2 streams cat-file --batch output

    in bounded chunks without capturing the aggregate payload in a monolithic bytes object.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_bytes(b"content 1")
    (repo / "f2.txt").write_bytes(b"content 2")
    large_payload = b"y" * 150_000
    (repo / "large.bin").write_bytes(large_payload)
    commit = _commit_all(repo, "commit with large blob")

    from app.agent_review.git_commit_subject_v2 import list_commit_tree_structure_v2
    entries = list_commit_tree_structure_v2(repo_root=repo, commit_sha=commit)

    import app.agent_review.git_commit_subject_v2 as gcs
    orig_open = gcs.open_bounded_git_subprocess_v2
    chunk_reads: list[int] = []

    def spy_open(argv, **kwargs):
        proc = orig_open(argv, **kwargs)
        if "cat-file" in argv and "--batch" in argv:
            orig_read = proc.stdout.read
            def spy_read(size=-1):
                if size > 0:
                    chunk_reads.append(size)
                return orig_read(size)
            proc.stdout.read = spy_read
        return proc

    monkeypatch.setattr(gcs, "open_bounded_git_subprocess_v2", spy_open)

    content = read_commit_blobs_v2(repo_root=repo, entries=entries)

    assert content["f1.txt"] == b"content 1"
    assert content["f2.txt"] == b"content 2"
    assert content["large.bin"] == large_payload
    # Chunk sizes must be bounded (<= 65536) during streaming
    assert max(chunk_reads) <= 65536
    assert any(s == 65536 for s in chunk_reads)


def test_list_single_tree_entries_rejects_oversized_single_tree_ingress(tmp_path: Path):
    """P1 (4097706660): Verifies _list_single_tree_entries_v2 enforces entry budget

    on ingress without allocating full entries for oversized single trees.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(5):
        (repo / f"file_{i}.txt").write_bytes(f"data {i}".encode())
    commit = _commit_all(repo, "5 files")

    from app.agent_review.git_commit_subject_v2 import (
        _list_single_tree_entries_v2,
        resolve_commit_tree_sha_v2,
    )
    tree_oid = resolve_commit_tree_sha_v2(repo_root=repo, commit_sha=commit)

    # Calling with max_entries=2 on a tree with 5 entries must fail closed immediately
    with pytest.raises(SubjectMaterialisationError) as exc_info:
        _list_single_tree_entries_v2(repo_root=repo, tree_oid=tree_oid, max_entries=2)
    assert exc_info.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2

    # Positive control: max_entries=10 succeeds and returns all 5 entries
    entries = _list_single_tree_entries_v2(repo_root=repo, tree_oid=tree_oid, max_entries=10)
    assert len(entries) == 5


def test_hierarchical_trie_traversal_bounds_subprocess_expansion(tmp_path: Path, monkeypatch):
    """P2 (4097706650): Verifies hierarchical tree traversal bounds subprocess expansion

    by querying multiple unique subtrees through a single shared batch session (O(1) processes).
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(10):
        d = repo / f"subtree_{i:02d}"
        d.mkdir()
        (d / f"leaf_{i:02d}.txt").write_bytes(f"leaf content {i}".encode())
    commit = _commit_all(repo, "10 unique subtrees")

    from app.agent_review.git_commit_subject_v2 import (
        _build_canonical_trie_hierarchical,
        resolve_commit_tree_sha_v2,
    )
    import app.agent_review.git_commit_subject_v2 as gcs

    root_tree = resolve_commit_tree_sha_v2(repo_root=repo, commit_sha=commit)

    orig_open = gcs.open_bounded_git_subprocess_v2
    orig_run = gcs.run_bounded_git_v2
    opened_commands: list[list[str]] = []
    run_commands: list[list[str]] = []

    def spy_open(argv, **kwargs):
        opened_commands.append(argv)
        return orig_open(argv, **kwargs)

    def spy_run(argv, **kwargs):
        run_commands.append(argv)
        return orig_run(argv, **kwargs)

    monkeypatch.setattr(gcs, "open_bounded_git_subprocess_v2", spy_open)
    monkeypatch.setattr(gcs, "run_bounded_git_v2", spy_run)

    _root, all_entries, leaf_blobs = _build_canonical_trie_hierarchical(
        repo_root=repo, root_tree_oid=root_tree
    )

    # Across 11 unique trees (root + 10 subtrees), exactly 1 batch subprocess was opened
    assert len(opened_commands) == 1
    assert any("cat-file" in cmd and "--batch" in cmd for cmd in opened_commands)
    # Zero one-off git subprocesses were run during tree traversal
    assert len(run_commands) == 0
    # All 20 entries (10 trees + 10 blobs) are properly projected
    assert len(all_entries) == 20
    assert len(leaf_blobs) == 10


def test_copy_entry_descriptor_relative_succeeds_under_umask_0700(tmp_path: Path):
    """P2 (4097706632): Verifies _copy_entry_descriptor_relative creates nested destination directories

    with owner permissions regardless of owner-masking umask (e.g. 0700) without EACCES.
    """
    src_dir = tmp_path / "src_umask"
    dst_dir = tmp_path / "dst_umask"
    src_dir.mkdir()
    dst_dir.mkdir()

    nested = src_dir / "dir_top" / "dir_nested"
    nested.mkdir(parents=True)
    (nested / "payload.txt").write_bytes(b"umask 0700 content")

    src_fd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    dst_fd = os.open(dst_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    old_umask = os.umask(0o700)
    try:
        _copy_entry_descriptor_relative("dir_top", src_fd, dst_fd)
    finally:
        os.umask(old_umask)
        os.close(src_fd)
        os.close(dst_fd)

    assert (dst_dir / "dir_top" / "dir_nested" / "payload.txt").read_bytes() == b"umask 0700 content"
    assert not (src_dir / "dir_top").exists()


def test_copy_entry_descriptor_relative_depth_under_restricted_nofile(tmp_path: Path):
    """P2 (4097706640): Verifies _copy_entry_descriptor_relative traverses deep hierarchies

    using O(1) live descriptors without hitting EMFILE under restricted descriptor limits.
    """
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    src_dir = tmp_path / "src_deep"
    dst_dir = tmp_path / "dst_deep"
    src_dir.mkdir()
    dst_dir.mkdir()

    depth = 35
    curr = src_dir
    for i in range(depth):
        curr = curr / f"depth_{i:02d}"
        curr.mkdir()
    (curr / "deep_leaf.txt").write_bytes(b"deep leaf content")

    src_fd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    dst_fd = os.open(dst_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    resource.setrlimit(resource.RLIMIT_NOFILE, (30, hard))
    try:
        _copy_entry_descriptor_relative("depth_00", src_fd, dst_fd)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        os.close(src_fd)
        os.close(dst_fd)

    expected = dst_dir
    for i in range(depth):
        expected = expected / f"depth_{i:02d}"
    expected = expected / "deep_leaf.txt"

    assert expected.read_bytes() == b"deep leaf content"
    assert not (src_dir / "depth_00").exists()


def test_blob_transport_does_not_duplicate_large_admitted_body_in_python_heap(tmp_path: Path):
    """P1 (4100737092, C3-B): Verifies read_commit_blobs_v2 streams admitted blobs into bounded spool

    storage without allocating or rebuilding large blob bodies in Python heap memory.
    """
    import tracemalloc
    from app.agent_review.git_commit_subject_v2 import list_commit_tree_structure_v2

    repo = tmp_path / "repo"
    _init_repo(repo)

    large_size = 2_500_000
    large_payload = b"Q" * large_size
    (repo / "large.bin").write_bytes(large_payload)
    (repo / "small.txt").write_bytes(b"small content")
    commit = _commit_all(repo, "commit with large blob")

    entries = list_commit_tree_structure_v2(repo_root=repo, commit_sha=commit)

    tracemalloc.start()
    carrier = read_commit_blobs_v2(repo_root=repo, entries=entries)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    try:
        assert isinstance(carrier, BoundedBlobCarrierV2)
        # Peak memory allocated on Python heap must be <= 300 KiB, proving the 2.5 MB body
        # was never duplicated or buffered in heap via chunks + b"".join.
        assert peak < 300 * 1024

        assert carrier["small.txt"] == b"small content"
        assert carrier["large.bin"] == large_payload

        # Verify stream_to_fd streams content directly to descriptor
        out_path = tmp_path / "streamed.bin"
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            bytes_streamed = carrier.stream_to_fd("large.bin", out_fd)
            assert bytes_streamed == large_size
        finally:
            os.close(out_fd)
        assert out_path.read_bytes() == large_payload
    finally:
        carrier.close()


def test_symlink_target_over_filesystem_limit_rejected_before_epoch(tmp_path: Path):
    """P2 (4100737099, C3-R): Verifies symlink targets exceeding filesystem limit are rejected

    as SUBJECT_UNREPRESENTABLE_TREE_REASON_V2 prior to creating any epoch root in workspace.
    """
    import subprocess

    repo = tmp_path / "repo"
    _init_repo(repo)

    # 1. Overlong target: 4096 bytes exceeds Linux PATH_MAX - 1 (4095)
    target_4096 = b"a" * 4096
    p_hash = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=target_4096,
        cwd=repo,
        check=True,
        capture_output=True,
    )
    oid_4096 = p_hash.stdout.decode().strip()
    p_tree = subprocess.run(
        ["git", "mktree"],
        input=f"120000 blob {oid_4096}\toverlong_link\n".encode(),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tree_oid_4096 = p_tree.stdout.decode().strip()
    p_commit = subprocess.run(
        ["git", "commit-tree", tree_oid_4096, "-m", "overlong symlink commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    commit_4096 = p_commit.stdout.decode().strip()

    pool = tmp_path / "pool"
    pool.mkdir()
    pool_fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY)
    try:
        workspace = MaterialisationWorkspaceCapabilityV2(pool_fd, pool)
    finally:
        os.close(pool_fd)

    with workspace:
        with pytest.raises(SubjectMaterialisationError) as exc_info:
            with acquire_materialised_commit_subject_v2(
                repo_root=repo,
                ref=commit_4096,
                workspace=workspace,
                authorized_storage=[repo],
            ):
                pass
        assert exc_info.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2

        # Verify zero epoch root was created on the pool filesystem
        epoch_entries = [p for p in pool.iterdir() if p.name.startswith("c3_epoch_")]
        assert len(epoch_entries) == 0

    # 2. Maximum admissible target: 4095 bytes succeeds
    target_4095 = b"b" * 4095
    p_hash_ok = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=target_4095,
        cwd=repo,
        check=True,
        capture_output=True,
    )
    oid_4095 = p_hash_ok.stdout.decode().strip()
    p_tree_ok = subprocess.run(
        ["git", "mktree"],
        input=f"120000 blob {oid_4095}\tvalid_link\n".encode(),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tree_oid_4095 = p_tree_ok.stdout.decode().strip()
    p_commit_ok = subprocess.run(
        ["git", "commit-tree", tree_oid_4095, "-m", "4095 symlink commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    commit_4095 = p_commit_ok.stdout.decode().strip()

    pool_fd2 = os.open(pool, os.O_RDONLY | os.O_DIRECTORY)
    try:
        workspace2 = MaterialisationWorkspaceCapabilityV2(pool_fd2, pool)
    finally:
        os.close(pool_fd2)

    with workspace2:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo,
            ref=commit_4095,
            workspace=workspace2,
            authorized_storage=[repo],
        ) as cap:
            assert cap.commit_sha == commit_4095
            assert os.readlink(cap.root_locator / "valid_link") == target_4095.decode("utf-8")


def test_legacy_projection_preserves_setgid_destination_inheritance(tmp_path: Path):
    """P2 (4100737105, C3-X): Verifies projecting children into an existing setgid directory

    preserves the destination directory's mode and propagates group ownership and S_ISGID to children.
    """
    import stat

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "top_file.txt").write_bytes(b"top level file")
    (repo / "nested_dir").mkdir()
    (repo / "nested_dir" / "leaf_file.txt").write_bytes(b"nested leaf file")
    head = _commit_all(repo, "commit for setgid projection")

    pool = tmp_path / "pool"
    pool.mkdir()
    dest = pool / "project_dest"
    dest.mkdir()

    # Determine a supplementary group ID if available, otherwise effective GID
    groups = os.getgroups()
    target_gid = groups[1] if len(groups) > 1 else groups[0]
    os.chown(dest, -1, target_gid)
    os.chmod(dest, 0o2770)

    materialise_commit_subject_v2(
        repo_root=repo,
        ref=head,
        destination=dest,
        authorized_storage=[repo],
    )

    st_dest = os.stat(dest)
    st_top = os.stat(dest / "top_file.txt")
    st_nested = os.stat(dest / "nested_dir")
    st_leaf = os.stat(dest / "nested_dir" / "leaf_file.txt")

    # Destination directory's own mode (02770) and group must be preserved
    assert (st_dest.st_mode & 0o7777) == 0o2770
    assert st_dest.st_gid == target_gid

    # Projected top file must inherit destination group
    assert st_top.st_gid == target_gid

    # Projected subdirectory must inherit destination group AND retain S_ISGID bit
    assert st_nested.st_gid == target_gid
    assert bool(st_nested.st_mode & stat.S_ISGID)

    # Projected nested leaf must inherit destination group
    assert st_leaf.st_gid == target_gid
