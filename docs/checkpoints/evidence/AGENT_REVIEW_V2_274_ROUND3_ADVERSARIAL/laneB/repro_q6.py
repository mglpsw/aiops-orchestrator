from __future__ import annotations

import contextlib
import importlib
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path


SCRATCH = Path("/tmp/ar274-review-r3/laneB/q6")
SUBJECT = Path("/opt/agent-tools/ar-200d-successor")
PYTHON = Path("/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python")

os.environ["TMPDIR"] = str(SCRATCH)
sys.path.insert(0, str(SUBJECT))

import app.agent_review.toolrepo_identity_v2 as identity_mod  # noqa: E402


GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
for name in (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG_COUNT",
):
    GIT_ENV.pop(name, None)


def run(argv: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=GIT_ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def reset_dir(name: str) -> Path:
    path = SCRATCH / name
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def init_fixture(name: str, *, actual_cli: bool = False) -> tuple[Path, str]:
    root = reset_dir(name)
    (root / "app" / "agent_review").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent_review" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent_review" / "mod.py").write_text("VALUE = 'tracked-v1'\n", encoding="utf-8")
    if actual_cli:
        (root / "scripts" / "aiops-review-run-v2.py").write_bytes(
            (SUBJECT / "scripts" / "aiops-review-run-v2.py").read_bytes()
        )
    else:
        (root / "scripts" / "aiops-review-run-v2.py").write_text("# tracked cli\n", encoding="utf-8")
    run(["git", "init", "--quiet", "-b", "main", "."], cwd=root)
    run(["git", "config", "user.email", "review@example.invalid"], cwd=root)
    run(["git", "config", "user.name", "review"], cwd=root)
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "--quiet", "-m", "fixture"], cwd=root)
    head = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return root, head


@contextlib.contextmanager
def fixture_identity_root(root: Path):
    package = identity_mod._agent_review_package_v2
    old_file = package.__file__
    package.__file__ = str(root / "app" / "agent_review" / "__init__.py")
    try:
        yield
    finally:
        package.__file__ = old_file


def identity_result(root: Path, head: str) -> str:
    try:
        with fixture_identity_root(root):
            result = identity_mod.establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head)
        return f"PASS sha={result.toolrepo_sha}"
    except identity_mod.ToolrepoIdentityError as exc:
        return f"REFUSED reason={exc.reason_code}"


def clone_full_subject(name: str) -> tuple[Path, str]:
    root = SCRATCH / name
    if root.exists():
        shutil.rmtree(root)
    run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(SUBJECT), str(root)],
        cwd=SCRATCH,
    )
    run(
        ["git", "checkout", "--quiet", "--detach", "c68a8b9a6b4d57383918f7fc1fa6a85536e331c6"],
        cwd=root,
    )
    return root, run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def untracked_listing(root: Path) -> list[str]:
    raw = run(
        [
            "git",
            "ls-files",
            "--others",
            "-z",
            "--",
            "app",
            "scripts/aiops-review-run-v2.py",
        ],
        cwd=root,
    ).stdout
    return [entry for entry in raw.split("\0") if entry]


def import_from(root: Path, statement: str) -> subprocess.CompletedProcess[str]:
    env = {**GIT_ENV, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [str(PYTHON), "-c", statement],
        cwd=SCRATCH,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def case_untracked_scripts_shadow() -> None:
    root, head = init_fixture("fixture-shadow", actual_cli=True)
    (root / "scripts" / "argparse.py").write_text(
        "print('UNTRACKED_SCRIPTS_ARGPARSE_EXECUTED', flush=True)\n"
        "raise RuntimeError('shadow reached before identity')\n",
        encoding="utf-8",
    )
    print("CASE untracked scripts/ shadow")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = subprocess.run(
        [str(PYTHON), str(root / "scripts" / "aiops-review-run-v2.py"), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("cli_output", child.stdout.strip().replace("\n", " | "))


def case_untracked_scripts_shadow_full_subject() -> None:
    root, head = clone_full_subject("fixture-full-subject")
    (root / "scripts" / "argparse.py").write_text(
        "import importlib.util as _iu\n"
        "import pathlib as _pl\n"
        "import sysconfig as _sc\n"
        "print('UNTRACKED_ARGPARSE_PROXY_EXECUTED', flush=True)\n"
        "_p = _pl.Path(_sc.get_path('stdlib')) / 'argparse.py'\n"
        "_s = _iu.spec_from_file_location('_real_stdlib_argparse', _p)\n"
        "_m = _iu.module_from_spec(_s)\n"
        "_s.loader.exec_module(_m)\n"
        "globals().update({k: v for k, v in vars(_m).items() if not k.startswith('__')})\n",
        encoding="utf-8",
    )
    print("CASE full subject untracked argparse proxy")
    print("fixture_head", head)
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = subprocess.run(
        [str(PYTHON), str(root / "scripts" / "aiops-review-run-v2.py"), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("cli_first_lines", " | ".join(child.stdout.strip().splitlines()[:3]))


def case_untracked_repo_root_shadow() -> None:
    root, head = init_fixture("fixture-root-shadow", actual_cli=True)
    (root / "pydantic.py").write_text(
        "print('UNTRACKED_ROOT_PYDANTIC_EXECUTED', flush=True)\n"
        "raise RuntimeError('root shadow reached before identity')\n",
        encoding="utf-8",
    )
    print("CASE untracked repository-root shadow")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = subprocess.run(
        [str(PYTHON), str(root / "scripts" / "aiops-review-run-v2.py"), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("cli_output", child.stdout.strip().replace("\n", " | "))


def case_symlink_directory() -> None:
    root, head = init_fixture("fixture-symlink-dir")
    outside = reset_dir("symlink-payload")
    (outside / "__init__.py").write_text("print('SYMLINK_PACKAGE_EXECUTED')\n", encoding="utf-8")
    (root / "app" / "agent_review" / "linked_pkg").symlink_to(outside, target_is_directory=True)
    print("CASE untracked symlink directory")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = import_from(root, "import app.agent_review.linked_pkg")
    print("import_rc", child.returncode)
    print("import_output", child.stdout.strip().replace("\n", " | "))


def case_tracked_symlink_module() -> None:
    root, _ = init_fixture("fixture-tracked-symlink")
    outside = reset_dir("tracked-symlink-payload")
    payload = outside / "payload.py"
    payload.write_text("print('TRACKED_SYMLINK_PAYLOAD_V1')\n", encoding="utf-8")
    link = root / "app" / "agent_review" / "linked.py"
    link.symlink_to(payload)
    run(["git", "add", "app/agent_review/linked.py"], cwd=root)
    run(["git", "commit", "--quiet", "-m", "tracked symlink"], cwd=root)
    head = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    before = identity_result(root, head)
    payload.write_text("print('TRACKED_SYMLINK_PAYLOAD_V2_UNBOUND')\n", encoding="utf-8")
    after = identity_result(root, head)
    child = import_from(root, "import app.agent_review.linked")
    print("CASE tracked symlink to mutable external source")
    print("identity_before_external_change", before)
    print("identity_after_external_change", after)
    print("git_status", run(["git", "status", "--porcelain=v1"], cwd=root).stdout.strip() or "<clean>")
    print("import_rc", child.returncode)
    print("import_output", child.stdout.strip().replace("\n", " | "))


def case_nested_git() -> None:
    root, head = init_fixture("fixture-nested-git")
    nested = root / "app" / "agent_review" / "vendor_pkg"
    nested.mkdir()
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "payload.py").write_text("print('NESTED_GIT_PAYLOAD_EXECUTED')\n", encoding="utf-8")
    run(["git", "init", "--quiet", "-b", "main", "."], cwd=nested)
    run(["git", "config", "user.email", "review@example.invalid"], cwd=nested)
    run(["git", "config", "user.name", "review"], cwd=nested)
    run(["git", "add", "-A"], cwd=nested)
    run(["git", "commit", "--quiet", "-m", "nested"], cwd=nested)
    print("CASE untracked nested Git repository")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = import_from(root, "import app.agent_review.vendor_pkg.payload")
    print("import_rc", child.returncode)
    print("import_output", child.stdout.strip().replace("\n", " | "))


def case_namespace_plain() -> None:
    root, head = init_fixture("fixture-namespace")
    ns = root / "app" / "agent_review" / "ns_pkg"
    ns.mkdir()
    (ns / "payload.py").write_text("print('NAMESPACE_PAYLOAD_EXECUTED')\n", encoding="utf-8")
    print("CASE ordinary untracked namespace package")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))


def case_pth() -> None:
    root, head = init_fixture("fixture-pth", actual_cli=True)
    marker = SCRATCH / "pth-marker"
    if marker.exists():
        marker.unlink()
    code = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('EXECUTED')\n"
    (root / "scripts" / "review_hook.pth").write_text(code, encoding="utf-8")
    (root / "app" / "review_hook.pth").write_text(code, encoding="utf-8")
    print("CASE untracked .pth in scripts/ and app/")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = subprocess.run(
        [str(PYTHON), str(root / "scripts" / "aiops-review-run-v2.py"), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("pth_marker", marker.read_text() if marker.exists() else "<not-created>")


def case_case_suffix() -> None:
    root, head = init_fixture("fixture-case")
    (root / "app" / "agent_review" / "CasePayload.PY").write_text(
        "print('UPPERCASE_SUFFIX_EXECUTED')\n", encoding="utf-8"
    )
    print("CASE uppercase .PY on Linux")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = import_from(root, "import app.agent_review.CasePayload")
    print("import_rc", child.returncode)
    print("import_tail", child.stdout.strip().splitlines()[-1] if child.stdout.strip() else "<empty>")


def case_unchecked_pyc_cache() -> None:
    root, head = init_fixture("fixture-pyc")
    tracked = root / "app" / "agent_review" / "mod.py"
    payload = SCRATCH / "unchecked-pyc-payload.py"
    payload.write_text("print('UNCHECKED_PYC_EXECUTED_INSTEAD_OF_TRACKED_SOURCE')\n", encoding="utf-8")
    cache = Path(importlib.util.cache_from_source(str(tracked)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(payload),
        cfile=str(cache),
        dfile=str(tracked),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    print("CASE untracked unchecked-hash bytecode cache")
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = import_from(root, "import app.agent_review.mod")
    print("import_rc", child.returncode)
    print("import_output", child.stdout.strip().replace("\n", " | "))


def case_unchecked_pyc_cache_full_subject() -> None:
    root, head = clone_full_subject("fixture-full-pyc")
    tracked = root / "app" / "agent_review" / "toolrepo_identity_v2.py"
    payload = SCRATCH / "full-unchecked-pyc-payload.py"
    payload.write_text(
        "from pathlib import Path as _Path\n"
        "print('FULL_SUBJECT_UNCHECKED_PYC_EXECUTED', flush=True)\n"
        "_source = _Path(__file__).read_text(encoding='utf-8')\n"
        "exec(compile(_source, __file__, 'exec'), globals(), globals())\n",
        encoding="utf-8",
    )
    cache = Path(importlib.util.cache_from_source(str(tracked)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(payload),
        cfile=str(cache),
        dfile=str(tracked),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    print("CASE full subject unchecked-hash bytecode cache")
    print("fixture_head", head)
    print("identity", identity_result(root, head))
    print("ls-files", untracked_listing(root))
    child = subprocess.run(
        [str(PYTHON), str(root / "scripts" / "aiops-review-run-v2.py"), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("cli_first_lines", " | ".join(child.stdout.strip().splitlines()[:3]))


def case_assume_unchanged_full_subject() -> None:
    root, head = clone_full_subject("fixture-full-assume")
    cli = root / "scripts" / "aiops-review-run-v2.py"
    run(["git", "update-index", "--assume-unchanged", "scripts/aiops-review-run-v2.py"], cwd=root)
    source = cli.read_text(encoding="utf-8")
    source = source.replace(
        "import argparse\n",
        "print('FULL_SUBJECT_ASSUME_UNCHANGED_CODE_EXECUTED', flush=True)\nimport argparse\n",
        1,
    )
    cli.write_text(source, encoding="utf-8")
    print("CASE full subject assume-unchanged modified CLI")
    print("fixture_head", head)
    print("identity", identity_result(root, head))
    print(
        "git_diff",
        repr(
            run(
                ["git", "diff", "--name-only", "HEAD", "--", "scripts/aiops-review-run-v2.py"],
                cwd=root,
            ).stdout
        ),
    )
    print(
        "ls_files_v",
        run(["git", "ls-files", "-v", "scripts/aiops-review-run-v2.py"], cwd=root).stdout.strip(),
    )
    child = subprocess.run(
        [str(PYTHON), str(cli), "--help"],
        cwd=SCRATCH,
        env={**GIT_ENV, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print("cli_rc", child.returncode)
    print("cli_first_lines", " | ".join(child.stdout.strip().splitlines()[:3]))


def case_index_flag(flag: str, deletion: bool) -> None:
    suffix = "delete" if deletion else "modify"
    root, head = init_fixture(f"fixture-{flag}-{suffix}")
    target = root / "app" / "agent_review" / "mod.py"
    run(["git", "update-index", f"--{flag}", "app/agent_review/mod.py"], cwd=root)
    if deletion:
        target.unlink()
    else:
        target.write_text("print('INDEX_FLAG_MODIFIED_SOURCE_EXECUTED')\n", encoding="utf-8")
    print(f"CASE index {flag} {suffix}")
    print("identity", identity_result(root, head))
    print("git_diff", repr(run(["git", "diff", "--name-only", "HEAD", "--", "app"], cwd=root).stdout))
    print("ls_files_v", run(["git", "ls-files", "-v", "app/agent_review/mod.py"], cwd=root).stdout.strip())
    if not deletion:
        child = import_from(root, "import app.agent_review.mod")
        print("import_rc", child.returncode)
        print("import_output", child.stdout.strip().replace("\n", " | "))


def case_index_flag_cli_deletion() -> None:
    root, head = init_fixture("fixture-assume-cli-delete", actual_cli=True)
    run(
        ["git", "update-index", "--assume-unchanged", "scripts/aiops-review-run-v2.py"],
        cwd=root,
    )
    (root / "scripts" / "aiops-review-run-v2.py").unlink()
    print("CASE assume-unchanged exact CLI deletion")
    print("identity", identity_result(root, head))
    print(
        "git_diff",
        repr(
            run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "HEAD",
                    "--",
                    "scripts/aiops-review-run-v2.py",
                ],
                cwd=root,
            ).stdout
        ),
    )
    print(
        "ls_files_v",
        run(["git", "ls-files", "-v", "scripts/aiops-review-run-v2.py"], cwd=root).stdout.strip(),
    )


def main() -> None:
    case_untracked_scripts_shadow()
    case_untracked_scripts_shadow_full_subject()
    case_untracked_repo_root_shadow()
    case_symlink_directory()
    case_tracked_symlink_module()
    case_nested_git()
    case_namespace_plain()
    case_pth()
    case_case_suffix()
    case_unchecked_pyc_cache()
    case_unchecked_pyc_cache_full_subject()
    for flag in ("assume-unchanged", "skip-worktree"):
        for deletion in (False, True):
            case_index_flag(flag, deletion)
    case_index_flag_cli_deletion()
    case_assume_unchanged_full_subject()


if __name__ == "__main__":
    main()
