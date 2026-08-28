from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time


SCRATCH = Path(os.environ["TMPDIR"]).resolve()
SUBJECT = Path("/opt/agent-tools/ar-200d-successor")
sys.path.insert(0, str(SUBJECT))

from app.agent_review._sealed_git_execution_v2 import (  # noqa: E402
    has_semantically_active_info_attributes_v2,
    sealed_git_argv_v2,
    sealed_git_child_env_v2,
)
from app.agent_review.diff_acquisition_v2 import (  # noqa: E402
    DiffAcquisitionError,
    acquire_diff_v2,
)


ROOT = Path(tempfile.mkdtemp(prefix="q4-probe-", dir=SCRATCH))


def clean_git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(SCRATCH)
    return env


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=clean_git_env(),
        check=check,
        capture_output=True,
        text=True,
    )


def init_two_commit_repo(path: Path, *, separate_git_dir: Path | None = None) -> tuple[str, str]:
    path.mkdir(parents=True)
    init_args = ["init", "--quiet"]
    if separate_git_dir is not None:
        separate_git_dir.parent.mkdir(parents=True, exist_ok=True)
        init_args.append(f"--separate-git-dir={separate_git_dir}")
    init_args.append(str(path))
    subprocess.run(init_args if init_args[0] == "git" else ["git", *init_args], env=clean_git_env(), check=True)
    git(path, "config", "user.name", "Q4 Probe")
    git(path, "config", "user.email", "q4@example.invalid")
    (path / "reviewed.txt").write_text("hello\n", encoding="utf-8")
    git(path, "add", "reviewed.txt")
    git(path, "commit", "--quiet", "-m", "base")
    base = git(path, "rev-parse", "HEAD").stdout.strip()
    (path / "reviewed.txt").write_text("hello\nworld\n", encoding="utf-8")
    git(path, "add", "reviewed.txt")
    git(path, "commit", "--quiet", "-m", "head")
    head = git(path, "rev-parse", "HEAD").stdout.strip()
    return base, head


def common_git_path(repo: Path) -> Path:
    result = subprocess.run(
        sealed_git_argv_v2(
            ["git", "rev-parse", "--git-path", "info/attributes"],
            trusted_repo_root=repo,
        ),
        cwd=repo,
        env=sealed_git_child_env_v2(),
        check=True,
        capture_output=True,
        text=True,
    )
    return (repo / result.stdout.strip()).resolve()


def acquire_outcome(repo: Path, base: str, head: str) -> dict[str, object]:
    try:
        diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    except DiffAcquisitionError as exc:
        return {"kind": "refusal", "reason": exc.reason_code}
    return {
        "kind": "diff",
        "binary": "GIT binary patch" in diff,
        "contains_world": "world" in diff,
        "length": len(diff),
        "text": diff,
    }


def probe_normal() -> dict[str, object]:
    repo = ROOT / "normal"
    base, head = init_two_commit_repo(repo)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("reviewed.txt -diff\n", encoding="utf-8")
    return {
        "repo": str(repo),
        "git_path": str(info),
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "acquire": acquire_outcome(repo, base, head),
    }


def probe_linked_worktree() -> dict[str, object]:
    repo = ROOT / "linked-main"
    base, head = init_two_commit_repo(repo)
    linked = ROOT / "linked-wt"
    git(repo, "worktree", "add", "--quiet", "--detach", str(linked), head)
    info = common_git_path(linked)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("reviewed.txt -diff\n", encoding="utf-8")
    return {
        "repo": str(linked),
        "dotgit_is_file": (linked / ".git").is_file(),
        "dotgit_text": (linked / ".git").read_text(encoding="utf-8").strip(),
        "git_path": str(info),
        "common_info_expected": str((repo / ".git" / "info" / "attributes").resolve()),
        "helper_active": has_semantically_active_info_attributes_v2(
            linked, env=sealed_git_child_env_v2()
        ),
        "acquire": acquire_outcome(linked, base, head),
    }


def probe_separate_git_dir() -> dict[str, object]:
    repo = ROOT / "separate-worktree"
    git_dir = ROOT / "separate-gitdir"
    base, head = init_two_commit_repo(repo, separate_git_dir=git_dir)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("reviewed.txt -diff\n", encoding="utf-8")
    return {
        "repo": str(repo),
        "dotgit_is_file": (repo / ".git").is_file(),
        "dotgit_text": (repo / ".git").read_text(encoding="utf-8").strip(),
        "git_path": str(info),
        "expected_gitdir_info": str((git_dir / "info" / "attributes").resolve()),
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "acquire": acquire_outcome(repo, base, head),
    }


def probe_unreadable() -> dict[str, object]:
    repo = ROOT / "unreadable"
    base, head = init_two_commit_repo(repo)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    # This runner is in a one-id user namespace (only uid 0 is mapped), so a
    # mode-000 file remains readable by the only executable uid.  A symlink to
    # procfs' regular-but-unreadable kcore entry exercises the same real
    # Path.read_text OSError branch without monkeypatching the helper.
    info.symlink_to("/proc/kcore")
    read_error = None
    try:
        info.resolve().read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        read_error = f"{type(exc).__name__}: {exc}"
    return {
        "repo": str(repo),
        "symlink_target": os.readlink(info),
        "resolved_is_file": info.resolve().is_file(),
        "direct_read_error": read_error,
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "acquire": acquire_outcome(repo, base, head),
        "uid_map": Path("/proc/self/uid_map").read_text(encoding="ascii").strip(),
    }


def probe_inert_noncomment_false_positive() -> dict[str, object]:
    repo = ROOT / "inert-noncomment"
    base, head = init_two_commit_repo(repo)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("reviewed.txt\n", encoding="utf-8")
    check_attr = git(repo, "check-attr", "-a", "--", "reviewed.txt", check=False)
    raw_diff = git(repo, "diff", "--binary", f"{base}...{head}", check=False)
    return {
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "check_attr_rc": check_attr.returncode,
        "check_attr_stdout": check_attr.stdout,
        "check_attr_stderr": check_attr.stderr,
        "plain_git_diff_binary": "GIT binary patch" in raw_diff.stdout,
        "plain_git_diff_contains_world": "world" in raw_diff.stdout,
        "acquire": acquire_outcome(repo, base, head),
    }


def probe_leading_space_comment() -> dict[str, object]:
    repo = ROOT / "leading-space-comment"
    base, head = init_two_commit_repo(repo)
    special = repo / "#special"
    special.write_text("old\n", encoding="utf-8")
    git(repo, "add", "#special")
    git(repo, "commit", "--quiet", "-m", "special base")
    special_base = git(repo, "rev-parse", "HEAD").stdout.strip()
    special.write_text("old\nnew\n", encoding="utf-8")
    git(repo, "add", "#special")
    git(repo, "commit", "--quiet", "-m", "special head")
    special_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("  #special -diff\n", encoding="utf-8")
    check_attr = git(repo, "check-attr", "diff", "--", "#special", check=False)
    raw_diff = git(repo, "diff", "--binary", f"{special_base}...{special_head}", check=False)
    return {
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "check_attr_rc": check_attr.returncode,
        "check_attr_stdout": check_attr.stdout,
        "check_attr_stderr": check_attr.stderr,
        "plain_git_diff_binary": "GIT binary patch" in raw_diff.stdout,
    }


def probe_unicode_whitespace_false_negative() -> dict[str, object]:
    repo = ROOT / "unicode-whitespace"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], env=clean_git_env(), check=True)
    git(repo, "config", "user.name", "Q4 Probe")
    git(repo, "config", "user.email", "q4@example.invalid")
    attacked_name = "\u00a0#special"
    attacked = repo / attacked_name
    attacked.write_text("old\n", encoding="utf-8")
    git(repo, "add", attacked_name)
    git(repo, "commit", "--quiet", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    attacked.write_text("old\nnew\n", encoding="utf-8")
    git(repo, "add", attacked_name)
    git(repo, "commit", "--quiet", "-m", "head")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(f"{attacked_name} -diff\n", encoding="utf-8")
    check_attr = git(repo, "check-attr", "diff", "--", attacked_name, check=False)
    raw_diff = git(repo, "diff", "--binary", f"{base}...{head}", check=False)
    return {
        "line_utf8_hex": info.read_bytes().hex(),
        "python_stripped": info.read_text(encoding="utf-8").strip(),
        "helper_active": has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        ),
        "check_attr_rc": check_attr.returncode,
        "check_attr_stdout": check_attr.stdout,
        "check_attr_stderr": check_attr.stderr,
        "plain_git_diff_binary": "GIT binary patch" in raw_diff.stdout,
        "acquire": acquire_outcome(repo, base, head),
    }


def probe_fifo() -> dict[str, object]:
    repo = ROOT / "fifo"
    base, head = init_two_commit_repo(repo)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(info)
    helper_active = has_semantically_active_info_attributes_v2(
        repo, env=sealed_git_child_env_v2()
    )

    stop = threading.Event()
    writes = 0
    errors: list[str] = []

    def feed_fifo() -> None:
        nonlocal writes
        payload = b"reviewed.txt -diff\n"
        while not stop.is_set():
            try:
                fd = os.open(info, os.O_WRONLY)
            except OSError as exc:
                errors.append(f"open:{exc.errno}:{exc}")
                return
            try:
                os.write(fd, payload)
                writes += 1
            except OSError as exc:
                errors.append(f"write:{exc.errno}:{exc}")
            finally:
                os.close(fd)

    feeder = threading.Thread(target=feed_fifo, daemon=True)
    feeder.start()
    try:
        outcome = acquire_outcome(repo, base, head)
    finally:
        stop.set()
        feeder.join(timeout=0.5)
    return {
        "path_is_file": info.is_file(),
        "path_is_fifo": stat.S_ISFIFO(info.stat().st_mode),
        "helper_active": helper_active,
        "feed_writes": writes,
        "feed_errors": errors,
        "acquire": outcome,
    }


def probe_symlink_loop() -> dict[str, object]:
    repo = ROOT / "symlink-loop"
    base, head = init_two_commit_repo(repo)
    info = common_git_path(repo)
    info.parent.mkdir(parents=True, exist_ok=True)
    info.symlink_to("attributes")
    try:
        helper: object = has_semantically_active_info_attributes_v2(
            repo, env=sealed_git_child_env_v2()
        )
    except BaseException as exc:
        helper = {"exception": type(exc).__name__, "detail": str(exc)}
    try:
        acquire: object = acquire_outcome(repo, base, head)
    except BaseException as exc:
        acquire = {"exception": type(exc).__name__, "detail": str(exc)}
    return {
        "symlink_target": os.readlink(info),
        "helper": helper,
        "acquire": acquire,
    }


print(f"fixture_root={ROOT}")
for label, probe in (
    ("normal_active", probe_normal),
    ("linked_worktree", probe_linked_worktree),
    ("separate_git_dir", probe_separate_git_dir),
    ("unreadable_file", probe_unreadable),
    ("inert_noncomment", probe_inert_noncomment_false_positive),
    ("leading_space_comment", probe_leading_space_comment),
    ("unicode_whitespace", probe_unicode_whitespace_false_negative),
    ("fifo", probe_fifo),
    ("symlink_loop", probe_symlink_loop),
):
    try:
        result = probe()
    except BaseException as exc:
        result = {"probe_exception": type(exc).__name__, "detail": str(exc)}
    print(f"{label}={json.dumps(result, sort_keys=True)}", flush=True)
