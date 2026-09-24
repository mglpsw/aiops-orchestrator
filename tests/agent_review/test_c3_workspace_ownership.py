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
