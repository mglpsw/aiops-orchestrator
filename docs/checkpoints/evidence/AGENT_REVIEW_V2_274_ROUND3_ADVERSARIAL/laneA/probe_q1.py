from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


SCRATCH = Path("/tmp/ar274-review-r3/laneA/q1-agent")
SUBJECT = Path("/opt/agent-tools/ar-200d-successor")
sys.path.insert(0, str(SUBJECT))

from app.agent_review._sealed_git_execution_v2 import (  # noqa: E402
    has_executable_local_filter_config_v2,
    sealed_git_argv_v2,
    sealed_git_child_env_v2,
)
from app.agent_review.diff_acquisition_v2 import (  # noqa: E402
    DiffAcquisitionError,
    acquire_diff_v2,
)
from app.agent_review.toolrepo_identity_v2 import (  # noqa: E402
    ToolrepoIdentityError,
    _assert_bounded_source_clean_v2,
    _run_toolrepo_git_v2,
    establish_toolrepo_source_identity_v2,
)
import app.agent_review.toolrepo_identity_v2 as toolrepo_identity_module  # noqa: E402


def run(argv: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=check)


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    run(["git", "init", "--quiet", "-b", "main", "."], repo)
    run(["git", "config", "user.email", "review@example.invalid"], repo)
    run(["git", "config", "user.name", "Review Fixture"], repo)


def commit_all(repo: Path, message: str) -> str:
    run(["git", "add", "-A"], repo)
    run(["git", "commit", "--quiet", "-m", message], repo)
    return run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def reset_dir(name: str) -> Path:
    path = SCRATCH / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def probe_toolrepo_clean_filter() -> dict[str, object]:
    root = reset_dir("toolrepo-clean")
    repo = root / "repo"
    init_repo(repo)
    (repo / "app").mkdir()
    (repo / "scripts").mkdir()
    (repo / "app" / "victim.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    (repo / "scripts" / "aiops-review-run-v2.py").write_text("print('cli')\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("app/*.py filter=evil\n", encoding="utf-8")
    commit_all(repo, "base")

    marker = root / "CLEAN_EXECUTED"
    command = f"sh -c 'printf executed > {marker}; cat'"
    run(["git", "config", "filter.evil.clean", command], repo)
    run(["git", "config", "filter.evil.required", "true"], repo)
    (repo / "app" / "victim.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")

    logical = [
        "git", "diff", "--name-only", "-z", "HEAD", "--",
        "app", "scripts/aiops-review-run-v2.py",
    ]
    actual = sealed_git_argv_v2(logical, trusted_repo_root=repo)
    result = _run_toolrepo_git_v2(logical, toolrepo_root=repo)
    identity_error = None
    try:
        _assert_bounded_source_clean_v2(repo)
    except ToolrepoIdentityError as exc:
        identity_error = exc.reason_code
    return {
        "logical_argv": logical,
        "actual_argv": actual,
        "returncode": result.returncode,
        "stdout_nul_rendered": result.stdout.replace("\0", "<NUL>"),
        "stderr": result.stderr,
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
        "identity_error_after_execution": identity_error,
    }


def probe_toolrepo_process_filter() -> dict[str, object]:
    root = reset_dir("toolrepo-process")
    repo = root / "repo"
    init_repo(repo)
    (repo / "app").mkdir()
    (repo / "scripts").mkdir()
    (repo / "app" / "victim.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    (repo / "scripts" / "aiops-review-run-v2.py").write_text("print('cli')\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("app/*.py filter=evil\n", encoding="utf-8")
    commit_all(repo, "base")

    marker = root / "PROCESS_EXECUTED"
    command = f"sh -c 'printf executed > {marker}; exit 1'"
    run(["git", "config", "filter.evil.process", command], repo)
    run(["git", "config", "filter.evil.required", "true"], repo)
    (repo / "app" / "victim.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")

    logical = [
        "git", "diff", "--name-only", "-z", "HEAD", "--",
        "app", "scripts/aiops-review-run-v2.py",
    ]
    result = _run_toolrepo_git_v2(logical, toolrepo_root=repo)
    return {
        "logical_argv": logical,
        "returncode": result.returncode,
        "stdout_nul_rendered": result.stdout.replace("\0", "<NUL>"),
        "stderr_first_line": result.stderr.splitlines()[0] if result.stderr else "",
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
    }


def probe_toolrepo_clean_filter_identity_bypass() -> dict[str, object]:
    root = reset_dir("toolrepo-clean-bypass")
    repo = root / "repo"
    init_repo(repo)
    (repo / "app" / "agent_review").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "app" / "agent_review" / "__init__.py").write_text("# fixture package\n", encoding="utf-8")
    (repo / "app" / "victim.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    cli = repo / "scripts" / "aiops-review-run-v2.py"
    cli.write_text("print('cli')\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("app/victim.py filter=evil\n", encoding="utf-8")
    head = commit_all(repo, "base")

    marker = root / "CLEAN_BYPASS_EXECUTED"
    helper = root / "clean-bypass.sh"
    helper.write_text(
        f"#!/bin/sh\nprintf executed > {marker}\nprintf \"VALUE = 'committed'\\n\"\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    run(["git", "config", "filter.evil.clean", str(helper)], repo)
    run(["git", "config", "filter.evil.required", "true"], repo)
    (repo / "app" / "victim.py").write_text("VALUE = 'MALICIOUS_DIRTY_BYTES'\n", encoding="utf-8")

    original_package_file = toolrepo_identity_module._agent_review_package_v2.__file__
    outcome = "returned"
    returned_sha = None
    error = None
    try:
        toolrepo_identity_module._agent_review_package_v2.__file__ = str(
            repo / "app" / "agent_review" / "__init__.py"
        )
        try:
            identity = establish_toolrepo_source_identity_v2(
                declared_toolrepo_sha=head,
                executing_script=cli,
            )
            returned_sha = identity.toolrepo_sha
        except ToolrepoIdentityError as exc:
            outcome = "ToolrepoIdentityError"
            error = exc.reason_code
    finally:
        toolrepo_identity_module._agent_review_package_v2.__file__ = original_package_file

    return {
        "public_identity_outcome": outcome,
        "returned_sha": returned_sha,
        "error": error,
        "declared_head": head,
        "actual_worktree_bytes": (repo / "app" / "victim.py").read_text(encoding="utf-8"),
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
    }


def probe_ambient_git_config_parameters_filter() -> dict[str, object]:
    root = reset_dir("ambient-config-parameters")
    repo = root / "repo"
    init_repo(repo)
    (repo / "app").mkdir()
    (repo / "scripts").mkdir()
    (repo / "app" / "victim.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    (repo / "scripts" / "aiops-review-run-v2.py").write_text("print('cli')\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("app/victim.py filter=evil\n", encoding="utf-8")
    commit_all(repo, "base")
    (repo / "app" / "victim.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")

    marker = root / "AMBIENT_FILTER_EXECUTED"
    helper = root / "ambient-clean.sh"
    helper.write_text(
        f"#!/bin/sh\nprintf executed > {marker}\nprintf \"VALUE = 'committed'\\n\"\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    injected = f"'filter.evil.clean={helper}' 'filter.evil.required=true'"
    old = os.environ.get("GIT_CONFIG_PARAMETERS")
    outcome = "returned"
    error = None
    preserved = False
    try:
        os.environ["GIT_CONFIG_PARAMETERS"] = injected
        preserved = sealed_git_child_env_v2().get("GIT_CONFIG_PARAMETERS") == injected
        try:
            _assert_bounded_source_clean_v2(repo)
        except ToolrepoIdentityError as exc:
            outcome = "ToolrepoIdentityError"
            error = exc.reason_code
    finally:
        if old is None:
            os.environ.pop("GIT_CONFIG_PARAMETERS", None)
        else:
            os.environ["GIT_CONFIG_PARAMETERS"] = old
    return {
        "sealed_env_preserved_GIT_CONFIG_PARAMETERS": preserved,
        "identity_clean_check_outcome": outcome,
        "error": error,
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
        "actual_worktree_bytes": (repo / "app" / "victim.py").read_text(encoding="utf-8"),
    }


def probe_acquisition_gitdir_conditional_filter() -> dict[str, object]:
    root = reset_dir("acq-conditional-filter")
    repo = root / "repo"
    init_repo(repo)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "f.txt").write_text("base\nhead\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("f.txt filter=evil\n", encoding="utf-8")
    head = commit_all(repo, "head")

    marker = root / "CONDITIONAL_SMUDGE_EXECUTED"
    helper = root / "conditional-smudge.sh"
    helper.write_text(
        f"#!/bin/sh\nprintf executed > {marker}\ncat\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    included = root / "worktree-only.cfg"
    included.write_text(
        "[filter \"evil\"]\n"
        f"\tsmudge = {helper}\n"
        "\trequired = true\n",
        encoding="utf-8",
    )
    pattern = f"{repo / '.git'}/worktrees/**"
    run(["git", "config", f"includeIf.gitdir:{pattern}.path", str(included)], repo)

    detector = has_executable_local_filter_config_v2(repo, env=sealed_git_child_env_v2())
    outcome = "success"
    detail = ""
    try:
        text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        detail = "head" if "head" in text else text[:120]
    except DiffAcquisitionError as exc:
        outcome = "DiffAcquisitionError"
        detail = exc.reason_code

    return {
        "include_condition": pattern,
        "detector_before_worktree": detector,
        "acquisition_outcome": outcome,
        "acquisition_detail": detail,
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
    }


def probe_noexec_config_surface() -> dict[str, object]:
    root = reset_dir("noexec-surface")
    repo = root / "repo"
    init_repo(repo)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("f.txt diff=evil merge=evil\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "f.txt").write_text("base\nhead\n", encoding="utf-8")
    head = commit_all(repo, "head")

    keys = {
        "core.pager": "pager",
        "core.editor": "editor",
        "core.sshCommand": "ssh",
        "credential.helper": "credential",
        "diff.external": "diff_external",
        "diff.evil.command": "diff_command",
        "diff.evil.textconv": "textconv",
        "merge.evil.driver": "merge_driver",
        "uploadpack.packObjectsHook": "pack_hook",
        "core.gitProxy": "git_proxy",
        "core.alternateRefsCommand": "alternate_refs",
    }
    markers: dict[str, Path] = {}
    for key, label in keys.items():
        marker = root / f"EXEC_{label}"
        markers[label] = marker
        command = f"sh -c 'printf executed > {marker}; cat >/dev/null'"
        if key == "credential.helper":
            command = f"!sh -c 'printf executed > {marker}; cat >/dev/null'"
        run(["git", "config", key, command], repo)
    run(["git", "config", "core.fsmonitor", f"sh -c 'printf executed > {root / 'EXEC_fsmonitor'}; echo'"], repo)
    markers["fsmonitor"] = root / "EXEC_fsmonitor"
    hook_marker = root / "EXEC_post_checkout_hook"
    hooks_dir = root / "evil-hooks"
    hooks_dir.mkdir()
    post_checkout = hooks_dir / "post-checkout"
    post_checkout.write_text(
        f"#!/bin/sh\nprintf executed > {hook_marker}\n",
        encoding="utf-8",
    )
    post_checkout.chmod(0o755)
    run(["git", "config", "core.hooksPath", str(hooks_dir)], repo)
    markers["post_checkout_hook"] = hook_marker

    outcome = "success"
    detail = ""
    try:
        text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        detail = "head" if "head" in text else text[:120]
    except DiffAcquisitionError as exc:
        outcome = "DiffAcquisitionError"
        detail = exc.reason_code

    return {
        "acquisition_outcome": outcome,
        "acquisition_detail": detail,
        "markers": {label: path.exists() for label, path in markers.items()},
    }


def probe_toolrepo_diff_helpers() -> dict[str, object]:
    root = reset_dir("toolrepo-diff-helpers")
    repo = root / "repo"
    init_repo(repo)
    (repo / "app").mkdir()
    (repo / "scripts").mkdir()
    (repo / "app" / "victim.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    (repo / "scripts" / "aiops-review-run-v2.py").write_text("print('cli')\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("app/*.py diff=evil merge=evil\n", encoding="utf-8")
    commit_all(repo, "base")
    (repo / "app" / "victim.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")

    keys = {
        "core.pager": "pager",
        "core.editor": "editor",
        "core.sshCommand": "ssh",
        "credential.helper": "credential",
        "diff.external": "diff_external",
        "diff.evil.command": "diff_command",
        "diff.evil.textconv": "textconv",
        "merge.evil.driver": "merge_driver",
        "uploadpack.packObjectsHook": "pack_hook",
        "core.gitProxy": "git_proxy",
        "core.alternateRefsCommand": "alternate_refs",
    }
    markers: dict[str, Path] = {}
    for key, label in keys.items():
        marker = root / f"EXEC_{label}"
        markers[label] = marker
        command = f"sh -c 'printf executed > {marker}; cat >/dev/null'"
        if key == "credential.helper":
            command = f"!sh -c 'printf executed > {marker}; cat >/dev/null'"
        run(["git", "config", key, command], repo)
    run(["git", "config", "core.fsmonitor", f"sh -c 'printf executed > {root / 'EXEC_fsmonitor'}; echo'"], repo)
    markers["fsmonitor"] = root / "EXEC_fsmonitor"

    logical = [
        "git", "diff", "--name-only", "-z", "HEAD", "--",
        "app", "scripts/aiops-review-run-v2.py",
    ]
    result = _run_toolrepo_git_v2(logical, toolrepo_root=repo)
    return {
        "returncode": result.returncode,
        "stdout_nul_rendered": result.stdout.replace("\0", "<NUL>"),
        "stderr": result.stderr,
        "markers": {label: path.exists() for label, path in markers.items()},
    }


def probe_acquisition_submodule_update_command() -> dict[str, object]:
    root = reset_dir("acq-submodule-update")
    subsrc = root / "subsrc"
    init_repo(subsrc)
    (subsrc / "sub.txt").write_text("one\n", encoding="utf-8")
    sub_one = commit_all(subsrc, "sub one")
    (subsrc / "sub.txt").write_text("one\ntwo\n", encoding="utf-8")
    sub_two = commit_all(subsrc, "sub two")

    repo = root / "repo"
    init_repo(repo)
    run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(subsrc), "sub"], repo)
    run(["git", "checkout", "--quiet", sub_one], repo / "sub")
    run(["git", "add", ".gitmodules", "sub"], repo)
    run(["git", "commit", "--quiet", "-m", "base submodule"], repo)
    base = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    run(["git", "checkout", "--quiet", sub_two], repo / "sub")
    run(["git", "add", "sub"], repo)
    run(["git", "commit", "--quiet", "-m", "head submodule"], repo)
    head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    marker = root / "SUBMODULE_UPDATE_EXECUTED"
    helper = root / "submodule-update.sh"
    helper.write_text(f"#!/bin/sh\nprintf executed > {marker}\nexit 0\n", encoding="utf-8")
    helper.chmod(0o755)
    run(["git", "config", "submodule.recurse", "true"], repo)
    run(["git", "config", "submodule.sub.update", f"!{helper}"], repo)
    run(["git", "config", "diff.submodule", "diff"], repo)

    outcome = "success"
    detail = ""
    try:
        text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        detail = text[:240]
    except DiffAcquisitionError as exc:
        outcome = "DiffAcquisitionError"
        detail = exc.reason_code
    return {
        "acquisition_outcome": outcome,
        "acquisition_detail": detail,
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
    }


def probe_acquisition_promisor_ext_execution() -> dict[str, object]:
    root = reset_dir("acq-promisor-ext")
    source = root / "source"
    init_repo(source)
    (source / "f.txt").write_text("base\n", encoding="utf-8")
    base = commit_all(source, "base")
    (source / "f.txt").write_text("base\nhead\n", encoding="utf-8")
    head = commit_all(source, "head")
    run(["git", "config", "uploadpack.allowFilter", "true"], source)
    run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], source)

    repo = root / "repo"
    clone = subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "clone", "--quiet",
            "--filter=blob:none", "--no-checkout", f"file://{source}", str(repo),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    marker = root / "PROMISOR_EXT_EXECUTED"
    helper = root / "promisor-transport.sh"
    helper.write_text(
        f"#!/bin/sh\nprintf executed > {marker}\nexec git upload-pack {source}\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    run(["git", "config", "remote.origin.url", f"ext::{helper}"], repo)
    run(["git", "config", "protocol.ext.allow", "always"], repo)

    missing_before = run(
        ["git", "rev-list", "--objects", "--all", "--missing=print"], repo
    ).stdout.splitlines()
    missing_blobs = [line for line in missing_before if line.startswith("?")]

    outcome = "success"
    detail = ""
    try:
        text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        detail = text[:120]
    except DiffAcquisitionError as exc:
        outcome = "DiffAcquisitionError"
        detail = exc.reason_code
    return {
        "clone_returncode": clone.returncode,
        "clone_stderr": clone.stderr,
        "missing_object_count_before": len(missing_blobs),
        "missing_object_sample": missing_blobs[:3],
        "acquisition_outcome": outcome,
        "acquisition_detail": detail,
        "marker_exists": marker.exists(),
        "marker_content": marker.read_text() if marker.exists() else None,
    }


def probe_promisor_named_transport_commands() -> dict[str, object]:
    root = reset_dir("promisor-named-transports")
    source = root / "source"
    init_repo(source)
    (source / "f.txt").write_text("base\n", encoding="utf-8")
    base = commit_all(source, "base")
    (source / "f.txt").write_text("base\nhead\n", encoding="utf-8")
    head = commit_all(source, "head")
    run(["git", "config", "uploadpack.allowFilter", "true"], source)

    def partial_clone(name: str) -> Path:
        repo = root / name
        result = subprocess.run(
            [
                "git", "-c", "protocol.file.allow=always", "clone", "--quiet",
                "--filter=blob:none", "--no-checkout", f"file://{source}", str(repo),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return repo

    def acquire_outcome(repo: Path) -> str:
        try:
            acquire_diff_v2(repo, base_sha=base, head_sha=head)
            return "success"
        except DiffAcquisitionError as exc:
            return f"DiffAcquisitionError:{exc.reason_code}"

    ssh_repo = partial_clone("ssh-repo")
    ssh_marker = root / "CORE_SSH_COMMAND_EXECUTED"
    ssh_helper = root / "fake-ssh.sh"
    ssh_helper.write_text(f"#!/bin/sh\nprintf executed > {ssh_marker}\nexit 1\n", encoding="utf-8")
    ssh_helper.chmod(0o755)
    run(["git", "config", "remote.origin.url", "ssh://example.invalid/repo"], ssh_repo)
    run(["git", "config", "core.sshCommand", str(ssh_helper)], ssh_repo)
    ssh_outcome = acquire_outcome(ssh_repo)

    proxy_repo = partial_clone("proxy-repo")
    proxy_marker = root / "CORE_GIT_PROXY_EXECUTED"
    proxy_helper = root / "fake-git-proxy.sh"
    proxy_helper.write_text(f"#!/bin/sh\nprintf executed > {proxy_marker}\nexit 1\n", encoding="utf-8")
    proxy_helper.chmod(0o755)
    run(["git", "config", "remote.origin.url", "git://example.invalid/repo"], proxy_repo)
    run(["git", "config", "core.gitProxy", str(proxy_helper)], proxy_repo)
    proxy_outcome = acquire_outcome(proxy_repo)

    uploadpack_repo = partial_clone("uploadpack-repo")
    uploadpack_marker = root / "REMOTE_UPLOADPACK_EXECUTED"
    uploadpack_helper = root / "fake-uploadpack.sh"
    uploadpack_helper.write_text(
        f"#!/bin/sh\nprintf executed > {uploadpack_marker}\nexec git upload-pack {source}\n",
        encoding="utf-8",
    )
    uploadpack_helper.chmod(0o755)
    run(["git", "config", "remote.origin.url", f"file://{source}"], uploadpack_repo)
    run(["git", "config", "remote.origin.uploadpack", str(uploadpack_helper)], uploadpack_repo)
    uploadpack_outcome = acquire_outcome(uploadpack_repo)

    return {
        "core_sshCommand": {
            "outcome": ssh_outcome,
            "marker_exists": ssh_marker.exists(),
            "marker_content": ssh_marker.read_text() if ssh_marker.exists() else None,
        },
        "core_gitProxy": {
            "outcome": proxy_outcome,
            "marker_exists": proxy_marker.exists(),
            "marker_content": proxy_marker.read_text() if proxy_marker.exists() else None,
        },
        "remote_origin_uploadpack": {
            "outcome": uploadpack_outcome,
            "marker_exists": uploadpack_marker.exists(),
            "marker_content": uploadpack_marker.read_text() if uploadpack_marker.exists() else None,
        },
    }


def main() -> None:
    probes = {
        "toolrepo_clean_filter": probe_toolrepo_clean_filter(),
        "toolrepo_process_filter": probe_toolrepo_process_filter(),
        "toolrepo_clean_filter_identity_bypass": probe_toolrepo_clean_filter_identity_bypass(),
        "ambient_git_config_parameters_filter": probe_ambient_git_config_parameters_filter(),
        "acquisition_gitdir_conditional_filter": probe_acquisition_gitdir_conditional_filter(),
        "acquisition_other_config_sinks": probe_noexec_config_surface(),
        "toolrepo_other_config_sinks": probe_toolrepo_diff_helpers(),
        "acquisition_submodule_update_command": probe_acquisition_submodule_update_command(),
        "acquisition_promisor_ext_execution": probe_acquisition_promisor_ext_execution(),
        "promisor_named_transport_commands": probe_promisor_named_transport_commands(),
    }
    print(json.dumps(probes, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
