from __future__ import annotations

import os
import pwd
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SCRATCH = Path("/tmp/ar274-review-r3/laneA/q3q9-agent")
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


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None,
        check: bool = True, text: bool = True, input: str | bytes | None = None):
    result = subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=text, input=input, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"command failed: {argv!r}\nstdout={result.stdout!r}\nstderr={result.stderr!r}")
    return result


def git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None):
    return run(["git", *args], cwd=repo, check=check, env=env)


def init_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "--quiet", "-b", "main", ".")
    git(repo, "config", "user.email", "review@example.invalid")
    git(repo, "config", "user.name", "review")
    return repo


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def base_head(root: Path) -> tuple[Path, str, str]:
    repo = init_repo(root)
    (repo / "reviewed.txt").write_text("base\n", encoding="utf-8")
    base = commit_all(repo, "base")
    (repo / "reviewed.txt").write_text("head\n", encoding="utf-8")
    head = commit_all(repo, "head")
    return repo, base, head


def fresh(name: str) -> Path:
    root = SCRATCH / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def show(label: str, value: object) -> None:
    print(f"[{label}] {value}")


def probe_core_worktree() -> None:
    root = fresh("core-worktree")
    repo, base, head = base_head(root)
    baseline = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    git(repo, "config", "core.worktree", str(repo))
    try:
        attacked = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        show("core.worktree.result", "success")
        show("core.worktree.equal_baseline", attacked == baseline)
        show("core.worktree.binary", "GIT binary patch" in attacked)
    except Exception as exc:
        show("core.worktree.result", f"{type(exc).__name__}:{exc}")
    show("core.worktree.registrations", git(repo, "worktree", "list", "--porcelain", check=False).stdout)


def probe_conditional_filter() -> None:
    root = fresh("conditional-filter")
    repo, base, head = base_head(root)
    (repo / ".gitattributes").write_text("reviewed.txt filter=evil\n", encoding="utf-8")
    head = commit_all(repo, "attributes")
    marker = root / "smudge-ran"
    filter_script = root / "filter.sh"
    filter_script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n", encoding="utf-8")
    filter_script.chmod(0o755)
    include_file = root / "included.cfg"
    include_file.write_text(
        f"[filter \"evil\"]\n\tsmudge = {filter_script}\n\trequired = false\n",
        encoding="utf-8",
    )
    # Match only the generated disposable worktree, never the original repo.
    git(repo, "config", "includeIf.gitdir:**/.git/worktrees/**.path", str(include_file))
    detector = has_executable_local_filter_config_v2(repo, env=sealed_git_child_env_v2())
    show("conditional.detector_before", detector)
    try:
        result = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        show("conditional.acquire", "success")
        show("conditional.diff_has_head", "+head" in result)
    except Exception as exc:
        show("conditional.acquire", f"{type(exc).__name__}:{exc}")
    show("conditional.marker_exists", marker.exists())
    show("conditional.registrations", git(repo, "worktree", "list", "--porcelain", check=False).stdout)


def probe_conditional_attributes() -> None:
    root = fresh("conditional-attributes")
    repo, base, head = base_head(root)
    baseline = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    outside = root / "outside.attributes"
    outside.write_text("reviewed.txt -diff\n", encoding="utf-8")
    include_file = root / "included.cfg"
    include_file.write_text(f"[core]\n\tattributesFile = {outside}\n", encoding="utf-8")
    git(repo, "config", "includeIf.gitdir:**/.git/worktrees/**.path", str(include_file))
    attacked = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    show("conditional-attr.equal_baseline", attacked == baseline)
    show("conditional-attr.binary", "GIT binary patch" in attacked)


def probe_conditional_core_worktree() -> None:
    root = fresh("conditional-core-worktree")
    repo, base, head = base_head(root)
    baseline = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    include_file = root / "included.cfg"
    include_file.write_text(f"[core]\n\tworktree = {repo}\n", encoding="utf-8")
    git(repo, "config", "includeIf.gitdir:**/.git/worktrees/**.path", str(include_file))
    try:
        attacked = acquire_diff_v2(repo, base_sha=base, head_sha=head)
        show("conditional-worktree.result", "success")
        show("conditional-worktree.equal_baseline", attacked == baseline)
        show("conditional-worktree.binary", "GIT binary patch" in attacked)
    except Exception as exc:
        show("conditional-worktree.result", f"{type(exc).__name__}:{exc}")
    show("conditional-worktree.target_attributes_still_exists", (repo / ".gitattributes").exists())


def chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        os.chown(path, uid, gid, follow_symlinks=False)


def chmod_tree_world_writable(root: Path) -> None:
    root.chmod(0o777)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o777)
        elif not path.is_symlink():
            path.chmod(0o666)


def run_as_foreign_uid(repo: Path, base: str, head: str, runner_tmp: Path, global_cfg: Path):
    runner_tmp.mkdir()
    runner_tmp.chmod(0o777)
    env = {
        **os.environ,
        "TMPDIR": str(runner_tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": str(global_cfg),
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return run(
        [
            "setpriv", "--reuid=65533", "--regid=65533", "--clear-groups",
            str(Path(sys.executable)), str(SCRATCH / "run_as_uid.py"),
            str(repo), base, head,
        ],
        cwd=runner_tmp,
        env=env,
        check=False,
    )


def run_without_dac_override(repo: Path, base: str, head: str, runner_tmp: Path):
    runner_tmp.mkdir()
    runner_tmp.chmod(0o777)
    env = {
        **os.environ,
        "TMPDIR": str(runner_tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return run(
        [
            "setpriv", "--bounding-set=-dac_override,-dac_read_search",
            "--inh-caps=-all", "--ambient-caps=-all", "--",
            str(Path(sys.executable)), str(SCRATCH / "run_as_uid.py"),
            str(repo), base, head,
        ],
        cwd=runner_tmp,
        env=env,
        check=False,
    )


def probe_foreign_owned_checkout() -> None:
    root = fresh("foreign-owned")
    writable_repo, base, head = base_head(root / "writable-case")
    global_cfg = root / "safe.cfg"
    global_cfg.write_text(f"[safe]\n\tdirectory = {writable_repo}\n", encoding="utf-8")
    chmod_tree_world_writable(writable_repo)
    writable = run_as_foreign_uid(
        writable_repo, base, head, root / "runner-tmp-writable", global_cfg
    )
    show("foreign-writable.runner_rc", writable.returncode)
    print(writable.stdout, end="")
    show("foreign-writable.runner_stderr", writable.stderr.strip())

    readonly_repo, base, head = base_head(root / "readonly-case")
    readonly_cfg = root / "safe-readonly.cfg"
    readonly_cfg.write_text(f"[safe]\n\tdirectory = {readonly_repo}\n", encoding="utf-8")
    readonly = run_as_foreign_uid(
        readonly_repo, base, head, root / "runner-tmp-readonly", readonly_cfg
    )
    show("foreign-readonly.runner_rc", readonly.returncode)
    print(readonly.stdout, end="")
    show("foreign-readonly.runner_stderr", readonly.stderr.strip())
    show("foreign-readonly.worktrees_dir_exists", (readonly_repo / ".git" / "worktrees").exists())


def probe_readonly_git_metadata() -> None:
    root = fresh("readonly-metadata")
    repo, base, head = base_head(root)
    for path in sorted(repo.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        elif not path.is_symlink():
            path.chmod(0o444)
    repo.chmod(0o555)
    result = run_without_dac_override(repo, base, head, root / "runner-tmp")
    show("readonly-metadata.runner_rc", result.returncode)
    print(result.stdout, end="")
    show("readonly-metadata.runner_stderr", result.stderr.strip())
    show("readonly-metadata.worktrees_dir_exists", (repo / ".git" / "worktrees").exists())


def probe_linked_worktree_input() -> None:
    root = fresh("linked-input")
    main_repo, base, head = base_head(root)
    linked = root / "linked"
    git(main_repo, "worktree", "add", "--quiet", "--detach", str(linked), head)
    try:
        (linked / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
        acquired = acquire_diff_v2(linked, base_sha=base, head_sha=head)
        show("linked-input.acquire", "success")
        show("linked-input.has_head", "+head" in acquired)
        show("linked-input.binary", "GIT binary patch" in acquired)
        listing = git(linked, "worktree", "list", "--porcelain").stdout
        show("linked-input.registration_count", listing.count("worktree "))
    finally:
        git(main_repo, "worktree", "remove", "--force", str(linked))


def probe_alternate_object_environment() -> None:
    root = fresh("alternate-env")
    source, base, head = base_head(root / "source")
    target = init_repo(root / "target-root", "target")
    alt_env = {**os.environ, "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source / ".git" / "objects")}
    git(target, "update-ref", "refs/heads/main", head, env=alt_env)

    direct = git(target, "diff", "--no-ext-diff", "--no-textconv", "--binary", f"{base}...{head}", env=alt_env)
    show("alternate.direct_rc", direct.returncode)
    show("alternate.direct_has_head", "+head" in direct.stdout)

    manual_wt = root / "manual-wt"
    added = git(target, "worktree", "add", "--quiet", "--detach", str(manual_wt), head, env=alt_env, check=False)
    show("alternate.unsealed_worktree_add_rc", added.returncode)
    if added.returncode == 0:
        git(target, "worktree", "remove", "--force", str(manual_wt), env=alt_env)

    old_alt = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    os.environ["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(source / ".git" / "objects")
    try:
        try:
            acquire_diff_v2(target, base_sha=base, head_sha=head)
        except DiffAcquisitionError as exc:
            show("alternate.sealed_error", exc.reason_code)
        else:
            show("alternate.sealed_error", None)
    finally:
        if old_alt is None:
            os.environ.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
        else:
            os.environ["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = old_alt

    alternates = target / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{source / '.git' / 'objects'}\n", encoding="utf-8")
    persistent = acquire_diff_v2(target, base_sha=base, head_sha=head)
    show("alternate.persistent_config_success", "+head" in persistent)


def probe_local_lfs_config() -> None:
    root = fresh("local-lfs")
    repo, base, head = base_head(root)
    baseline = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    show("lfs.baseline", "+head" in baseline)
    git(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")
    git(repo, "config", "filter.lfs.smudge", "git-lfs smudge -- %f")
    git(repo, "config", "filter.lfs.process", "git-lfs filter-process")
    git(repo, "config", "filter.lfs.required", "true")
    try:
        acquire_diff_v2(repo, base_sha=base, head_sha=head)
    except DiffAcquisitionError as exc:
        show("lfs.local_config_error", exc.reason_code)
    else:
        show("lfs.local_config_error", None)


def probe_benign_hooks_path() -> None:
    root = fresh("benign-hooks")
    repo, base, head = base_head(root)
    hooks = root / "hooks"
    hooks.mkdir()
    marker = root / "post-checkout-ran"
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))
    acquired = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    show("hooks.acquire_success", "+head" in acquired)
    show("hooks.marker_exists", marker.exists())


def probe_safe_directory_mechanism() -> None:
    root = fresh("safe-directory-mechanism")
    repo, _base, _head = base_head(root)
    global_cfg = root / "global.cfg"
    global_cfg.write_text(f"[safe]\n\tdirectory = {repo}\n", encoding="utf-8")
    assumed_foreign_env = {
        **os.environ,
        "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
        "GIT_CONFIG_GLOBAL": str(global_cfg),
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    global_safe = git(repo, "status", "--short", env=assumed_foreign_env, check=False)
    show("safe.global_config_rc", global_safe.returncode)

    old_assumption = os.environ.get("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
    try:
        sealed_env = sealed_git_child_env_v2()
        without_argv = run(["git", "status", "--short"], cwd=repo, env=sealed_env, check=False)
        with_argv = run(
            sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=repo),
            cwd=repo, env=sealed_env, check=False,
        )
    finally:
        if old_assumption is None:
            os.environ.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
        else:
            os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = old_assumption
    show("safe.sealed_env_without_argv_rc", without_argv.returncode)
    show("safe.sealed_env_without_argv_dubious", "dubious ownership" in without_argv.stderr)
    show("safe.sealed_argv_rc", with_argv.returncode)

    previous_cwd = Path.cwd()
    old_assumption = os.environ.get("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
    try:
        os.chdir(root)
        relative_env = sealed_git_child_env_v2()
        relative = run(
            sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=Path("repo")),
            cwd=Path("repo"), env=relative_env, check=False,
        )
        absolute = run(
            sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=repo.resolve()),
            cwd=Path("repo"), env=relative_env, check=False,
        )
    finally:
        os.chdir(previous_cwd)
        if old_assumption is None:
            os.environ.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
        else:
            os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = old_assumption
    show("safe.relative_path_rc", relative.returncode)
    show("safe.relative_path_dubious", "dubious ownership" in relative.stderr)
    show("safe.absolute_path_rc", absolute.returncode)

    link = root / "repo-link"
    link.symlink_to(repo, target_is_directory=True)
    old_assumption = os.environ.get("GIT_TEST_ASSUME_DIFFERENT_OWNER")
    os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
    try:
        link_env = sealed_git_child_env_v2()
        through_symlink = run(
            sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=link),
            cwd=link, env=link_env, check=False,
        )
    finally:
        if old_assumption is None:
            os.environ.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
        else:
            os.environ["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = old_assumption
    show("safe.symlink_path_rc", through_symlink.returncode)
    show("safe.symlink_path_dubious", "dubious ownership" in through_symlink.stderr)


def probe_shared_diff_config() -> None:
    root = fresh("shared-diff-config")
    repo = init_repo(root)
    (repo / "reviewed.txt").write_text(
        "one\ntwo\nthree\nfour\nfive\nsix\nseven\n", encoding="utf-8"
    )
    base = commit_all(repo, "base")
    (repo / "reviewed.txt").write_text(
        "one\ntwo\nthree\nCHANGED\nfive\nsix\nseven\n", encoding="utf-8"
    )
    head = commit_all(repo, "head")
    baseline = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    git(repo, "config", "diff.context", "0")
    zero_context = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    show("diff-context.equal", zero_context == baseline)
    show("diff-context.baseline_hunk", next(line for line in baseline.splitlines() if line.startswith("@@")))
    show("diff-context.configured_hunk", next(line for line in zero_context.splitlines() if line.startswith("@@")))

    git(repo, "config", "--unset", "diff.context")
    (repo / ".gitattributes").write_text("reviewed.txt diff=evil\n", encoding="utf-8")
    head_with_attrs = commit_all(repo, "head with driver")
    text_result = acquire_diff_v2(repo, base_sha=base, head_sha=head_with_attrs)
    git(repo, "config", "diff.evil.binary", "true")
    binary_result = acquire_diff_v2(repo, base_sha=base, head_sha=head_with_attrs)
    show("diff-binary.text_before", "GIT binary patch" not in text_result and "+CHANGED" in text_result)
    show("diff-binary.binary_after", "GIT binary patch" in binary_result)
    show("diff-binary.equal", binary_result == text_result)


def probe_isolation_and_add_failure() -> None:
    root = fresh("isolation")
    repo, base, head = base_head(root)
    expected = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    git(repo, "checkout", "--quiet", "--detach", base)
    (repo / ".gitattributes").write_text("reviewed.txt -diff\n", encoding="utf-8")
    attacked = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    show("isolation.equal", attacked == expected)
    show("isolation.binary", "GIT binary patch" in attacked)
    show("isolation.target_content", (repo / "reviewed.txt").read_text(encoding="utf-8").strip())
    show("isolation.registration_count", git(repo, "worktree", "list", "--porcelain").stdout.count("worktree "))
    try:
        acquire_diff_v2(repo, base_sha=base, head_sha="0" * 40)
    except DiffAcquisitionError as exc:
        show("isolation.invalid_object_error", exc.reason_code)
    else:
        show("isolation.invalid_object_error", None)


def main() -> None:
    os.environ["TMPDIR"] = str(SCRATCH)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    probe_core_worktree()
    probe_conditional_filter()
    probe_conditional_attributes()
    probe_conditional_core_worktree()
    probe_isolation_and_add_failure()
    probe_linked_worktree_input()
    # Foreign-UID simulation is unavailable on this filesystem: chown and
    # setresuid both return EINVAL. The read-only metadata probe below still
    # isolates the newly introduced write requirement.
    probe_readonly_git_metadata()
    probe_alternate_object_environment()
    probe_local_lfs_config()
    probe_benign_hooks_path()
    probe_safe_directory_mechanism()
    probe_shared_diff_config()


if __name__ == "__main__":
    main()
