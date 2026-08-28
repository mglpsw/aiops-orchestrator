"""`#200-E` -- reviewer-controlled TOOLREPO execution subject (issue #200,
successor to the `FROZEN_FORENSIC` `#274`).

`#274`'s `toolrepo_identity_v2.py` asked Git whether the development
checkout "looked clean" (`git diff --name-only HEAD` empty) and trusted an
empty answer as proof the executing bytes matched the declared
`toolrepo_sha`. That proposition is permanently rejected -- `git
update-index --assume-unchanged`/`--skip-worktree` make Git itself omit a
modified tracked file from that diff, and a repository-local
`filter.*.clean` can emit the committed bytes verbatim while the actual
file on disk is materially different. Independently reproduced through the
REAL `establish_toolrepo_source_identity_v2` function in `#274` round 3:
identity PASSED while the checked file on disk read `# TAMPERED`.

## `TOOLREPO_EXECUTION_SUBJECT_INVARIANT`

For a declared toolrepo SHA `S`, the semantic AgentReview child process
imports project-owned source ONLY from a reviewer-controlled filesystem
materialization of the exact committed project source at `S`. No mutable
development-worktree source participates after the subject is sealed.

This does NOT prove interpreter identity, third-party dependency identity,
OS/kernel identity, or outer-launcher attestation -- those remain
toolchain/bootstrap subjects, owned elsewhere (`toolchain_digest`,
`#203`-`#205`'s distribution trust), never collapsed into this authority.

## Mechanism, each element backed by a reproduced spike result (see the
## checkpoint's toolrepo spike section)

`git archive <S> -- <bounded paths>` materializes the subject, not a
checkout of the development worktree. Verified directly: `git archive`
reads the commit tree object and ignores the index entirely (an
`assume-unchanged`/`skip-worktree`-flagged file still archived with its
COMMITTED bytes) and does not invoke repository-local `filter.*.clean`/
`.smudge` (a configured filter never executed; archived content was the
plain committed bytes). It only ever extracts tracked, committed blobs, so
there is no untracked-file universe (stray `.py`, `.pyc`, a shadow
`scripts/argparse.py`, a root-level shadow module) for a hostile checkout
to hide content in.

`git archive` alone is NOT sufficient in two ways, each found by testing
the mechanism rather than assumed safe by analogy to `.clean`/`.smudge`:

- A committed SYMLINK blob (tree mode `120000`) is extracted as a real
  filesystem symlink and reading through it resolves wherever the target
  points on the actual filesystem -- reproduced directly with an
  absolute-path symlink escaping the subject entirely. This module audits
  the tree (`git ls-tree -r`) BEFORE archiving and refuses on any `120000`
  (symlink) or `160000` (gitlink/submodule) entry under the bounded paths,
  rather than resolving or silently skipping it.
- Unlike `.clean`/`.smudge`, `git archive` DOES invoke a repository-local
  `filter.*.process` -- found while writing this module's own test suite,
  confirmed with an isolated fixture (`.clean`/`.smudge` silent,
  `.process` fires). Refused outright, the same fail-closed shape as the
  symlink case: the driver NAME is attacker-chosen, so unlike a fixed Git
  setting there is no single `-c` override that closes an arbitrary name.

A byte-identity oracle (`cat-file` blob bytes vs. raw materialized
filesystem bytes, per enumerated regular-file entry) runs as defense in
depth on top of the archive mechanism, not instead of it -- this is the
minimum falsifier §8 requires, and it is intentionally NOT the only proof
of import isolation (that proof is architectural: nothing untracked is ever
materialized in the first place).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.agent_review._bounded_git_child_env_v2 import (
    bounded_child_env_v2,
    run_bounded_git_v2,
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_SYMLINK_MODE_V2 = "120000"
_GITLINK_MODE_V2 = "160000"

TOOLREPO_EXECUTION_SUBJECT_INVALID_SHA_REASON_V2 = "toolrepo_execution_subject_invalid_sha"
TOOLREPO_EXECUTION_SUBJECT_ROOT_UNUSABLE_REASON_V2 = "toolrepo_execution_subject_root_unusable"
TOOLREPO_EXECUTION_SUBJECT_SHA_UNRESOLVABLE_REASON_V2 = (
    "toolrepo_execution_subject_sha_unresolvable"
)
TOOLREPO_EXECUTION_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2 = (
    "toolrepo_execution_subject_symlink_or_gitlink_present"
)
TOOLREPO_EXECUTION_SUBJECT_MATERIALIZATION_FAILED_REASON_V2 = (
    "toolrepo_execution_subject_materialization_failed"
)
TOOLREPO_EXECUTION_SUBJECT_BYTE_IDENTITY_MISMATCH_REASON_V2 = (
    "toolrepo_execution_subject_byte_identity_mismatch"
)
TOOLREPO_EXECUTION_SUBJECT_EXECUTABLE_FILTER_CONFIG_PRESENT_REASON_V2 = (
    "toolrepo_execution_subject_executable_filter_config_present"
)


class ToolrepoExecutionSubjectError(ValueError):
    """A refusal this authority names explicitly. Distinct from
    `ControlledSubjectError` -- the two subject authorities remain separate
    error families, never flattened into one."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MaterializedEntryV2:
    path: str
    mode: str
    blob_sha: str


@dataclass(frozen=True)
class ToolrepoExecutionSubjectV2:
    """Private, non-wire, non-persisted carrier."""

    root: Path
    declared_toolrepo_sha: str
    entries: tuple[MaterializedEntryV2, ...]


def _has_executable_process_filter_v2(toolrepo_root: Path, *, env: dict[str, str]) -> bool:
    """`git archive` does NOT invoke a repository-local `filter.*.clean`/
    `.smudge` (verified directly) -- but DOES invoke `filter.*.process`,
    found empirically while adding the CE-11 test for this module, not
    assumed safe from the `.clean`/`.smudge` result. Deliberately
    unscoped (`git config --list`, not `--local`): a target-side
    detector in `#274` that used `--local` was bypassed by
    `include.path`-indirected config, the same lesson applies here.
    """
    result = run_bounded_git_v2(
        ["git", "config", "--list", "--name-only", "-z"], cwd=toolrepo_root, env=env
    )
    if result.returncode != 0:
        return False
    for key in result.stdout.split(b"\x00"):
        key_text = key.decode("utf-8", errors="replace").strip().lower()
        if key_text.startswith("filter.") and key_text.endswith(".process"):
            return True
    return False


def _parse_ls_tree_z_v2(raw: bytes) -> list[MaterializedEntryV2]:
    entries: list[MaterializedEntryV2] = []
    for record in raw.split(b"\x00"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, _, rest = meta.partition(b" ")
        _obj_type, _, blob_sha = rest.partition(b" ")
        entries.append(
            MaterializedEntryV2(
                path=path.decode("utf-8"),
                mode=mode.decode("ascii"),
                blob_sha=blob_sha.decode("ascii"),
            )
        )
    return entries


@contextmanager
def materialize_toolrepo_execution_subject_v2(
    toolrepo_root: Path, *, declared_toolrepo_sha: str, bounded_paths: tuple[str, ...]
) -> Iterator[ToolrepoExecutionSubjectV2]:
    """Materialize the exact committed project source at
    ``declared_toolrepo_sha``, bounded to ``bounded_paths``, into a fresh
    scratch directory. Yields a :class:`ToolrepoExecutionSubjectV2`; the
    scratch root is removed on every exit path.
    """

    if not _SHA_RE.match(declared_toolrepo_sha):
        raise ToolrepoExecutionSubjectError(TOOLREPO_EXECUTION_SUBJECT_INVALID_SHA_REASON_V2)
    if not toolrepo_root.is_dir() or not (toolrepo_root / ".git").exists():
        raise ToolrepoExecutionSubjectError(TOOLREPO_EXECUTION_SUBJECT_ROOT_UNUSABLE_REASON_V2)

    holder = Path(tempfile.mkdtemp(prefix="agent-review-toolrepo-subject-v2-"))
    try:
        home = holder / "home"
        home.mkdir(parents=True)
        env = bounded_child_env_v2(isolated_home=home)

        ls_tree = run_bounded_git_v2(
            [
                "git", "ls-tree", "-r", "-z", declared_toolrepo_sha, "--",
                *bounded_paths,
            ],
            cwd=toolrepo_root, env=env,
        )
        if ls_tree.returncode != 0:
            raise ToolrepoExecutionSubjectError(
                TOOLREPO_EXECUTION_SUBJECT_SHA_UNRESOLVABLE_REASON_V2
            )
        entries = _parse_ls_tree_z_v2(ls_tree.stdout)
        for entry in entries:
            if entry.mode in (_SYMLINK_MODE_V2, _GITLINK_MODE_V2):
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2
                )

        if _has_executable_process_filter_v2(toolrepo_root, env=env):
            raise ToolrepoExecutionSubjectError(
                TOOLREPO_EXECUTION_SUBJECT_EXECUTABLE_FILTER_CONFIG_PRESENT_REASON_V2
            )

        present_paths = tuple(
            p for p in bounded_paths
            if any(e.path == p or e.path.startswith(p + "/") for e in entries)
        )
        subject_root = holder / "subject"
        subject_root.mkdir(parents=True)
        if present_paths:
            archive = run_bounded_git_v2(
                ["git", "archive", declared_toolrepo_sha, "--", *present_paths],
                cwd=toolrepo_root, env=env,
            )
            if archive.returncode != 0:
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_MATERIALIZATION_FAILED_REASON_V2
                )
            _extract_tar_bytes_v2(archive.stdout, into=subject_root)

        for entry in entries:
            expected = run_bounded_git_v2(
                ["git", "cat-file", "-p", entry.blob_sha], cwd=toolrepo_root, env=env
            )
            if expected.returncode != 0:
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_MATERIALIZATION_FAILED_REASON_V2
                )
            materialized_path = subject_root / entry.path
            if not materialized_path.is_file():
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_MATERIALIZATION_FAILED_REASON_V2
                )
            if materialized_path.read_bytes() != expected.stdout:
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_BYTE_IDENTITY_MISMATCH_REASON_V2
                )

        yield ToolrepoExecutionSubjectV2(
            root=subject_root,
            declared_toolrepo_sha=declared_toolrepo_sha,
            entries=tuple(entries),
        )
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _extract_tar_bytes_v2(raw: bytes, *, into: Path) -> None:
    """Extract a `git archive` tar byte stream into ``into``. A thin,
    explicit wrapper (not a shared "run any archive" helper) so the one
    call site stays auditable."""

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise ToolrepoExecutionSubjectError(
                    TOOLREPO_EXECUTION_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2
                )
        tar.extractall(path=into)  # noqa: S202 -- symlinks/hardlinks already refused above
