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
        os.mkdir(test_dir, 0o700, dir_fd=workspace.pool_fd)
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

