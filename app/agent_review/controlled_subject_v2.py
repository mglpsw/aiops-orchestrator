"""`#200-E` -- reviewer-controlled TARGET subject materialization (issue
#200, successor to the `FROZEN_FORENSIC` `#274`).

`#274`'s architecture executed semantic Git operations INSIDE the target's
own `.git` (a disposable `git worktree add` off the target's checkout).
Three independent adversarial rounds each found a new target-controlled
execution vector reachable from that position (hooks, filter drivers,
`fsmonitor`, `includeIf`, lazy-fetch transport helpers, `core.attributesFile`)
-- the pattern, not a shortage of enumeration, is why `#274` is frozen: the
checkout step itself always had the target's config, hooks, index and admin
state in scope.

This module changes which repository semantic operations execute against.
The TARGET is read from exactly once, to compute and pack a bounded closure
of the objects reachable from the declared `base_sha`/`head_sha` -- no
config, hooks, filters, index, or admin state is ever read or copied. Those
objects are imported into a reviewer-owned scratch repository with no
alternates, no remote, and no shared storage; every subsequent semantic
operation runs there.

## `TARGET_SUBJECT_MATERIALIZATION_INVARIANT`

Once a subject reaches `SEALED` (this context manager has yielded), its
semantic operations remain valid even if the original target checkout
becomes unavailable. Verified directly (see the checkpoint's spike section):
`mv <source> <source>.SEVERED` then `git diff`/`git cat-file` against the
scratch subject produced byte-identical output to the un-severed case.

## What this rejects, deliberately, rather than silently handles

`git clone --shared` was tried and rejected WITH EVIDENCE, not merely
distrusted: it retains `objects/info/alternates` pointing at the source,
which is a real, load-bearing dependency -- severing the source made it
fail (`fatal: unable to normalize alternate object path`). The mechanism
here (bounded `rev-list --objects` closure, `pack-objects`/`index-pack`
import) has no alternates file at all.

A source repository whose OWN `objects/info/alternates` is non-empty is
refused outright (`TARGET_OBJECT_ALTERNATES_PRESENT` in the spec; this
module's reason code below) rather than silently followed -- there is no
established, proven-safe semantics here for transitively trusting whatever
that alternates chain points at.

A missing object after the closure computation is a typed refusal, never a
lazy fetch: `GIT_NO_LAZY_FETCH=1` is part of the bounded environment for
every step, including the closure computation itself (not merely later
operations) -- verified directly against a genuinely missing loose object
behind a hostile `ext::` transport helper: the helper never executed, and
`git rev-list --objects` failed loudly (`fatal: missing blob object`, exit
128) before any pack/import step began.
"""

from __future__ import annotations

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

_SHA_RE = __import__("re").compile(r"^[0-9a-f]{40}$")

CONTROLLED_SUBJECT_INVALID_REF_REASON_V2 = "controlled_subject_invalid_ref"
CONTROLLED_SUBJECT_SOURCE_ROOT_UNUSABLE_REASON_V2 = "controlled_subject_source_root_unusable"
CONTROLLED_SUBJECT_SOURCE_LAYOUT_UNSUPPORTED_REASON_V2 = (
    "controlled_subject_source_layout_unsupported"
)
CONTROLLED_SUBJECT_ALTERNATES_PRESENT_REASON_V2 = "controlled_subject_alternates_present"
CONTROLLED_SUBJECT_OBJECT_CLOSURE_INCOMPLETE_REASON_V2 = (
    "controlled_subject_object_closure_incomplete"
)
CONTROLLED_SUBJECT_GIT_UNAVAILABLE_REASON_V2 = "controlled_subject_git_unavailable"
CONTROLLED_SUBJECT_IMPORT_FAILED_REASON_V2 = "controlled_subject_import_failed"


class ControlledSubjectError(ValueError):
    """A refusal this authority names explicitly. `reason_code` is stable
    and content-free: never a target path, never raw Git stderr, never
    reviewed bytes -- the same discipline `#274`'s typed families used."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ControlledTargetSubjectV2:
    """Private, non-wire, non-persisted carrier. Contains only facts a
    caller legitimately needs -- never exposed in artifacts or reason
    codes."""

    root: Path
    base_sha: str
    head_sha: str


def _classify_source_layout_v2(source_root: Path) -> Path:
    """Support or refuse the source `.git` layout explicitly (§13). This
    phase supports exactly one layout: an ordinary, complete repository
    with a `.git` DIRECTORY (not a linked-worktree `.git` FILE, not bare,
    not shallow) and no `objects/info/alternates`. Every other layout is a
    typed refusal, not a guessed fallback.
    """

    if not source_root.is_dir():
        raise ControlledSubjectError(CONTROLLED_SUBJECT_SOURCE_ROOT_UNUSABLE_REASON_V2)
    git_dir = source_root / ".git"
    if not git_dir.is_dir():
        # Covers: missing .git entirely, a linked-worktree `.git` FILE
        # (unsupported this phase, per §13's recommended posture), and a
        # bare repository laid out as `source_root` itself (also refused
        # here rather than silently reinterpreted).
        raise ControlledSubjectError(CONTROLLED_SUBJECT_SOURCE_LAYOUT_UNSUPPORTED_REASON_V2)
    shallow_marker = git_dir / "shallow"
    if shallow_marker.is_file():
        raise ControlledSubjectError(CONTROLLED_SUBJECT_SOURCE_LAYOUT_UNSUPPORTED_REASON_V2)
    alternates = git_dir / "objects" / "info" / "alternates"
    if alternates.is_file() and alternates.read_text(encoding="utf-8", errors="replace").strip():
        raise ControlledSubjectError(CONTROLLED_SUBJECT_ALTERNATES_PRESENT_REASON_V2)
    return git_dir


@contextmanager
def materialize_controlled_target_subject_v2(
    source_root: Path, *, base_sha: str, head_sha: str
) -> Iterator[ControlledTargetSubjectV2]:
    """Materialize a self-contained, reviewer-owned subject for the
    declared `base_sha`/`head_sha` closure, read once from ``source_root``,
    then never again. Yields a :class:`ControlledTargetSubjectV2`; the
    scratch root is removed on every exit path.

    ``base_sha``/``head_sha`` must each be a full lowercase 40-character
    commit SHA -- never a branch, tag, or caller-supplied ref string.
    """

    if not _SHA_RE.match(base_sha) or not _SHA_RE.match(head_sha):
        raise ControlledSubjectError(CONTROLLED_SUBJECT_INVALID_REF_REASON_V2)

    source_git_dir = _classify_source_layout_v2(source_root)

    holder = Path(tempfile.mkdtemp(prefix="agent-review-controlled-subject-v2-"))
    try:
        source_env = bounded_child_env_v2(isolated_home=holder / "source-home")
        (holder / "source-home").mkdir(parents=True, exist_ok=True)

        closure = run_bounded_git_v2(
            ["git", "--git-dir", str(source_git_dir), "rev-list", "--objects", base_sha, head_sha],
            cwd=source_root, env=source_env,
        )
        if closure.returncode != 0:
            raise ControlledSubjectError(CONTROLLED_SUBJECT_OBJECT_CLOSURE_INCOMPLETE_REASON_V2)

        pack = run_bounded_git_v2(
            ["git", "--git-dir", str(source_git_dir), "pack-objects", "--stdout"],
            cwd=source_root, env=source_env, input_bytes=closure.stdout,
        )
        if pack.returncode != 0:
            raise ControlledSubjectError(CONTROLLED_SUBJECT_IMPORT_FAILED_REASON_V2)

        scratch_root = holder / "subject"
        scratch_root.mkdir(parents=True)
        scratch_home = holder / "scratch-home"
        scratch_home.mkdir(parents=True)
        scratch_env = bounded_child_env_v2(isolated_home=scratch_home)

        init = run_bounded_git_v2(["git", "init", "--quiet", "."], cwd=scratch_root, env=scratch_env)
        if init.returncode != 0:
            raise ControlledSubjectError(CONTROLLED_SUBJECT_GIT_UNAVAILABLE_REASON_V2)

        imported = run_bounded_git_v2(
            ["git", "index-pack", "--stdin"], cwd=scratch_root, env=scratch_env,
            input_bytes=pack.stdout,
        )
        if imported.returncode != 0:
            raise ControlledSubjectError(CONTROLLED_SUBJECT_IMPORT_FAILED_REASON_V2)

        for ref_name, sha in (("refs/scratch/base", base_sha), ("refs/scratch/head", head_sha)):
            ref = run_bounded_git_v2(
                ["git", "update-ref", ref_name, sha], cwd=scratch_root, env=scratch_env
            )
            if ref.returncode != 0:
                raise ControlledSubjectError(CONTROLLED_SUBJECT_IMPORT_FAILED_REASON_V2)

        yield ControlledTargetSubjectV2(root=scratch_root, base_sha=base_sha, head_sha=head_sha)
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def run_semantic_git_in_subject_v2(subject: ControlledTargetSubjectV2, argv: list[str]):
    """Run a semantic `git` operation against the materialized subject.
    ``argv`` must begin with ``"git"``; the subject's own root is used as
    both `--git-dir`-equivalent context and cwd, under a fresh bounded
    environment scoped to the subject's own holder directory -- never the
    source's."""

    env = bounded_child_env_v2(isolated_home=subject.root.parent / "op-home")
    (subject.root.parent / "op-home").mkdir(parents=True, exist_ok=True)
    return run_bounded_git_v2(argv, cwd=subject.root, env=env)
