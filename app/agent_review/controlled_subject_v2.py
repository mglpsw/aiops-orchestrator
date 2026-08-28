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


CONTROLLED_SUBJECT_CHECKOUT_FAILED_REASON_V2 = "controlled_subject_checkout_failed"
CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2 = (
    "controlled_subject_reference_path_unsupported"
)
CONTROLLED_SUBJECT_REFERENCE_MATERIALIZATION_FAILED_REASON_V2 = (
    "controlled_subject_reference_materialization_failed"
)

_REGULAR_FILE_MODES_V2 = ("100644", "100755")
_REFERENCE_SYMLINK_MODE_V2 = "120000"
_REFERENCE_GITLINK_MODE_V2 = "160000"
_REFERENCE_TREE_MODE_V2 = "040000"


CONTROLLED_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2 = (
    "controlled_subject_symlink_or_gitlink_present"
)

_TARGET_SYMLINK_MODE_V2 = "120000"
_TARGET_GITLINK_MODE_V2 = "160000"


def _audit_checkout_tree_for_symlinks_and_gitlinks_v2(subject: ControlledTargetSubjectV2) -> None:
    """`#200-E` Phase 3 correction, found by independent review: a
    committed symlink blob (tree mode `120000`) at the declared subject
    checks out as a REAL filesystem symlink -- reproduced directly, an
    absolute-path symlink to a host file outside the subject was readable
    through the checked-out subject after `checkout_head_into_subject_v2`.
    `toolrepo_execution_subject_v2.py` already closes the identical class
    for the TOOLREPO side (`git archive` + an `ls-tree` mode audit before
    extracting); this was the same class left open on the TARGET side --
    an inconsistency, not a considered design choice, in exactly the
    pattern that repeatedly falsified `#274`. Audits the FULL tree here
    (not merely the paths a caller happens to read later), since nothing
    about `acquire_diff_v2`/`extract_review_content_v2` bounds which paths
    of the checked-out subject a future change might read directly from
    the filesystem rather than via git object plumbing.
    """

    ls_tree = run_semantic_git_in_subject_v2(
        subject, ["git", "ls-tree", "-r", "-z", subject.head_sha]
    )
    if ls_tree.returncode != 0:
        raise ControlledSubjectError(CONTROLLED_SUBJECT_CHECKOUT_FAILED_REASON_V2)
    for record in ls_tree.stdout.split(b"\x00"):
        if not record:
            continue
        meta, _, _path = record.partition(b"\t")
        mode = meta.split(b" ", 1)[0].decode("ascii", errors="replace")
        if mode in (_TARGET_SYMLINK_MODE_V2, _TARGET_GITLINK_MODE_V2):
            raise ControlledSubjectError(CONTROLLED_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2)


def checkout_head_into_subject_v2(subject: ControlledTargetSubjectV2) -> None:
    """Check ``subject.head_sha`` out into the subject's own working tree.

    `#200-E` Phase 3 (`#200`). Git's attribute resolution (used by
    `acquire_diff_v2` and downstream owners) walks the WORKING TREE for a
    `.gitattributes` file when no `--attr-source` is given -- unavailable on
    this host's Git (`#274`'s own finding, still true). Left un-checked-out,
    the subject's working tree is empty, so a committed `.gitattributes`
    at `head_sha` would have NO effect, which is wrong (not a security gap,
    but a correctness one: §8 requires the declared subject's own committed
    attributes to be a genuine semantic input). Checking out here makes the
    committed attributes visible on disk exactly where Git looks for them --
    safely, because Phase 2 already proved a checkout inside this
    reviewer-owned scratch repo triggers no hook/filter/fsmonitor/includeIf
    execution (the scratch's own config/hooks/index are freshly initialized
    and never populated from the source).

    The tree is audited for symlink/gitlink entries BEFORE the checkout
    itself -- refused, not resolved or silently skipped, the same
    fail-closed shape `toolrepo_execution_subject_v2.py` already uses for
    the identical class.
    """

    _audit_checkout_tree_for_symlinks_and_gitlinks_v2(subject)
    result = run_semantic_git_in_subject_v2(
        subject, ["git", "checkout", "--quiet", subject.head_sha, "--", "."]
    )
    if result.returncode != 0:
        raise ControlledSubjectError(CONTROLLED_SUBJECT_CHECKOUT_FAILED_REASON_V2)


def materialize_controlled_reference_root_v2(
    subject: ControlledTargetSubjectV2, *, declared_paths: tuple[str, ...]
) -> Path:
    """`#200-E` Phase 3, §7 -- the controlled-subject replacement for
    `#274`'s `reference_source_v2.py` (NOT ported; re-derived).

    `payload_references_v2.build_payload_artifact_references_v2`/
    `build_payload_contract_references_v2` and
    `payload_builder_v2.build_chunk_payloads_from_profile_v2` read
    profile-declared artifact/contract paths from the WORKING TREE at a
    caller-supplied `repo_root` via ordinary filesystem reads -- they are
    not git-object-bound the way `acquire_diff_v2` is. Pointing `repo_root`
    at the original target checkout would reintroduce exactly the TOCTOU
    `#274` closed for diff/content: identical `(base_sha, head_sha)` inputs
    binding different bytes depending on what the target's mutable
    filesystem happened to contain at read time.

    This builds a SEPARATE, narrow, reviewer-owned root containing ONLY the
    declared reference paths, each read directly from `subject`'s own Git
    object database via `ls-tree`/`cat-file` -- never a general checkout,
    and never the caller's working tree. Existing callers
    (`build_payload_artifact_references_v2` etc.) are then pointed at THIS
    root, unmodified: the same required/optional-missing semantics they
    already implement apply unchanged, because a genuinely absent declared
    path simply has nothing written for it here (no second missing-artifact
    taxonomy is created).

    A declared path that resolves to anything other than a regular file
    (symlink, gitlink/submodule, or a directory) is refused outright, the
    same fail-closed shape `toolrepo_execution_subject_v2.py` uses for the
    identical class of entry.
    """

    env = bounded_child_env_v2(isolated_home=subject.root.parent / "ref-home")
    (subject.root.parent / "ref-home").mkdir(parents=True, exist_ok=True)
    reference_root = subject.root.parent / "reference-root"
    reference_root.mkdir(parents=True, exist_ok=True)

    for declared_path in declared_paths:
        ls_tree = run_bounded_git_v2(
            ["git", "ls-tree", subject.head_sha, "--", declared_path],
            cwd=subject.root, env=env,
        )
        if ls_tree.returncode != 0:
            raise ControlledSubjectError(
                CONTROLLED_SUBJECT_REFERENCE_MATERIALIZATION_FAILED_REASON_V2
            )
        line = ls_tree.stdout.decode("utf-8", errors="replace").strip()
        if not line:
            continue  # genuinely absent at head_sha -- existing callers handle this
        meta, _, _path = line.partition("\t")
        mode, _obj_type, blob_sha = meta.split(" ", 2)
        if mode in (_REFERENCE_SYMLINK_MODE_V2, _REFERENCE_GITLINK_MODE_V2, _REFERENCE_TREE_MODE_V2):
            raise ControlledSubjectError(CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2)
        if mode not in _REGULAR_FILE_MODES_V2:
            raise ControlledSubjectError(CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2)

        blob = run_bounded_git_v2(["git", "cat-file", "-p", blob_sha], cwd=subject.root, env=env)
        if blob.returncode != 0:
            raise ControlledSubjectError(
                CONTROLLED_SUBJECT_REFERENCE_MATERIALIZATION_FAILED_REASON_V2
            )
        destination = reference_root / declared_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob.stdout)

    return reference_root
