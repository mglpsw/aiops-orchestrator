"""`#200-D` successor: toolrepo SOURCE checkout identity (issue #200).

## What this authority proves, and what it does not

Precedent: `scripts/install-agent-review-toolrepo.sh` already verifies
`--toolrepo-sha` against `git rev-parse HEAD` and rejects a branch, a tag or
a short SHA -- but it accepts a dirty tracked tree, because installation
copies a lockfile, not the engine's own importable source. The operational
runner is a different subject: it EXECUTES this toolrepo's Python, so the
property this module establishes is strictly stronger than the installer's.

    toolrepo_sha      = identity of the AgentReview TOOLREPO SOURCE CHECKOUT
    toolchain_digest  = identity of the execution/toolchain environment

Git proves the SOURCE TREE. It does not prove every executed byte -- the
Python interpreter, third-party dependencies, site-packages, compiled
``.pyc`` caches, native extensions are outside a source checkout's identity
and are `toolchain_digest`'s subject, not this module's. This module never
claims more than the source tree it can actually observe.

## TOOLREPO_SOURCE_IDENTITY_INVARIANT

    ReviewRun.toolrepo_sha == S iff
      1. the executing AgentReview package resolves inside TOOLREPO_ROOT;
      2. the executing CLI script belongs to that same root;
      3. TOOLREPO_ROOT is a resolvable Git checkout;
      4. HEAD == S;
      5. tracked executable source in the bounded surface is unmodified;
      6. no untracked executable/importable source exists in that bounded set.

The caller's declared SHA is NEVER a fallback for any of the six. Treating
``CallerDeclared(A)`` as ``ExecutedCodeProven(A)`` is exactly the authority
inversion this module exists to prevent -- on any failure: no semantic
review, no Router call.

## Bounded executable source set -- defined structurally, not by heuristic

    app/                              (the WHOLE package tree, not just
                                       app/agent_review -- the composed
                                       review path imports across package
                                       boundaries, e.g. review_transport_v2,
                                       required_check_provenance_v2 and
                                       authoritative_ci_snapshot_v2 all import
                                       from app.common.strict_json, which
                                       sits outside app/agent_review)
    scripts/aiops-review-run-v2.py    (this CLI only, not all of scripts/)

An earlier revision bounded this to `app/agent_review` only, on the
unverified assumption that the composed path imports nothing else. It does:
grep confirms multiple modules the composer's own call graph reaches
(`review_transport_v2.py`, `required_check_provenance_v2.py`,
`authoritative_ci_snapshot_v2.py`, `authoritative_check_policy_v2.py`,
`authoritative_producer_evidence_v2.py`, `required_check_assembly_v2.py`,
`_router_receipt_v2.py`, `target_pack_receipt_v2.py`) import
`app.common.strict_json`. A dirty `app/common/strict_json.py` would have
executed as part of the review while this authority still reported a clean
source checkout -- found on independent review of this same PR, before
Ready. Widening to the whole `app/` package closes it without building a
dynamic import-graph inference, at the cost of a dirty file anywhere in
`app/` (even one this composed path does not import) blocking a run --
accepted as the simpler, correct-by-construction alternative for this
slice. `scripts/` stays narrowed to the one CLI this slice adds; a dirty,
unrelated *toolrepo* file (a doc, an eval fixture, another script) still
must not refuse a run. Git plumbing is used with explicit path arguments
and NUL-delimited output -- never fragile text parsing of a status line.

## `#200-D` correction: sealed Git execution, honest ignore/deletion semantics

Independent review found three further gaps, each closed here:

- **Ambient Git state and replacement objects.** Every Git call in this
  module now runs under `_sealed_git_execution_v2.sealed_git_child_env_v2()`
  -- see that module for the measured invariant this closes (an ambient
  `GIT_DIR`, for one, silently redirects every Git command in a process to
  an unrelated repository regardless of `cwd`, reproduced directly).

- **`--exclude-standard` is not a source-identity authority.**
  `git ls-files --others --exclude-standard` deliberately hides any path
  matched by `.gitignore`/`.git/info/exclude`/the global ignore file --
  reproduced directly: a stray `app/common/_stray_evil.py` became
  completely invisible to the untracked-source check the moment a matching
  `.gitignore` line existed, ignored or not, tracked or not. A file does
  not become non-importable merely because Git has been told to ignore it.
  The untracked-source check now enumerates ALL untracked paths under the
  bounded set (`git ls-files --others`, no `--exclude-standard`) and
  applies its OWN explicit SOURCE_IDENTITY filter -- `.py` files, excluding
  `__pycache__` directory components -- rather than letting ignore
  configuration make that decision. `__pycache__`/`.pyc` are TOOLCHAIN/
  EXECUTION_ENVIRONMENT artifacts, `toolchain_digest`'s subject, not this
  module's; routine Python execution must not be refused merely for
  producing them.

- **A deleted bounded path must not vanish from the proof.** The prior
  implementation pre-filtered `BOUNDED_SOURCE_RELATIVE_PATHS_V2` to paths
  that `.exists()` on disk BEFORE calling `git diff`, so a bounded path
  deleted from disk (e.g. the CLI script itself) was silently excluded from
  the pathspec `git diff` was even asked about -- reproduced directly: `git
  diff --name-only HEAD -- scripts/aiops-review-run-v2.py` DOES report a
  deletion when actually asked, but the prior code never asked, because the
  path had already failed a filesystem `.exists()` check first. The full
  declared `BOUNDED_SOURCE_RELATIVE_PATHS_V2` is now always passed to `git
  diff` unconditionally; a genuinely wrong/empty `TOOLREPO_ROOT` (nothing
  under the bounded set ever existed in the tree at all) is instead detected
  by asking Git's own tree, via `git ls-tree`, never the filesystem.

## Second-order honesty (recorded, not hidden)

This module is itself code that was imported and is running before it has
verified anything. The claim it supports is *"review execution was blocked
before semantic review/transport"* -- never *"zero unverified code
execution"*. Some code necessarily ran to perform the proof; the invariant
above bounds what runs AFTER that point, not before.

## Gitless toolrepo distribution -- out of scope for this slice

    "#200-D":
      supported_toolrepo_execution: {git_checkout: true, gitless_distribution: false}
      gitless: {behavior: fail_closed, reason: toolrepo_identity_unavailable,
                future_owner: "distribution/release (#203 -> #205)"}

A toolrepo deployed without a resolvable Git checkout fails closed with
``toolrepo_identity_unavailable``. This module does not invent a
``.version``-file fallback: a release-artifact identity model (embedded
source SHA, checksums, build provenance) is a distinct authority belonging
to the distribution/release line, not something reconditioned into this one
to make a gitless deployment pass.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import app.agent_review as _agent_review_package_v2
from app.agent_review._sealed_git_execution_v2 import (
    sealed_git_argv_v2,
    sealed_git_child_env_v2,
)

TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2 = "toolrepo_identity_unavailable"
TOOLREPO_IDENTITY_MISMATCH_REASON_V2 = "toolrepo_identity_mismatch"
TOOLREPO_WORKTREE_DIRTY_REASON_V2 = "toolrepo_worktree_dirty"
TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2 = "toolrepo_identity_unverifiable"

_TOOLREPO_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Relative to TOOLREPO_ROOT. Structural, not a heuristic scan: the WHOLE
# app/ package tree (the composed review path imports across package
# boundaries -- see the module docstring's "Bounded executable source set"
# section), plus the one CLI script this slice adds.
BOUNDED_SOURCE_RELATIVE_PATHS_V2: tuple[str, ...] = (
    "app",
    "scripts/aiops-review-run-v2.py",
)


class ToolrepoIdentityError(ValueError):
    """Raised when toolrepo source identity cannot be established. Carries
    a stable ``reason_code`` only -- never a path or git stderr."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ToolrepoSourceIdentityV2:
    """Private, non-wire carrier: the toolrepo root and the SHA independently
    proven to be its current HEAD. Never persisted."""

    toolrepo_root: Path
    toolrepo_sha: str


def resolve_toolrepo_root_v2(*, executing_script: Path | None = None) -> Path:
    """Canonical TOOLREPO_ROOT, resolved from the executing PACKAGE's own
    location (never argv): ``app/agent_review/__init__.py``'s
    grandparent directory. If ``executing_script`` is given, it must resolve
    inside that same root -- otherwise a script from checkout A could run
    ``app.agent_review`` imported from checkout/site-package B, and this
    authority would inspect the wrong tree entirely.
    """

    package_dir = Path(_agent_review_package_v2.__file__).resolve().parent
    toolrepo_root = package_dir.parent.parent
    if executing_script is not None:
        resolved_script = executing_script.resolve()
        if toolrepo_root not in resolved_script.parents:
            raise ToolrepoIdentityError(TOOLREPO_IDENTITY_MISMATCH_REASON_V2)
    return toolrepo_root


def _run_toolrepo_git_v2(argv: list[str], *, toolrepo_root: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            sealed_git_argv_v2(argv, trusted_repo_root=toolrepo_root),
            cwd=toolrepo_root, env=sealed_git_child_env_v2(),
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2) from exc


def _resolve_toolrepo_head_v2(toolrepo_root: Path) -> str:
    if not (toolrepo_root / ".git").exists():
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    result = _run_toolrepo_git_v2(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], toolrepo_root=toolrepo_root
    )
    head = result.stdout.strip()
    if result.returncode != 0 or not _TOOLREPO_SHA_RE.fullmatch(head):
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    return head


# SOURCE_IDENTITY vs TOOLCHAIN/EXECUTION_ENVIRONMENT, made explicit rather
# than left to ignore-file configuration: a `.py` file is source; anything
# under a `__pycache__` directory is a compiled-bytecode cache Python itself
# creates during ordinary execution, and is `toolchain_digest`'s subject.
_TOOLCHAIN_DIRECTORY_COMPONENTS_V2 = frozenset({"__pycache__"})


def _is_toolchain_artifact_v2(relative_path: str) -> bool:
    return "__pycache__" in Path(relative_path).parts


def _bounded_source_present_in_tree_v2(toolrepo_root: Path) -> bool:
    """Whether HEAD's own tree -- not the filesystem -- has anything under
    the declared bounded set. Replaces a `.exists()` filesystem prefilter
    that silently dropped a deleted bounded path from every check below it;
    this is asked once, up front, only to detect a genuinely wrong or empty
    `TOOLREPO_ROOT`, never to shrink the pathspec `git diff` is asked
    about."""

    result = _run_toolrepo_git_v2(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", *BOUNDED_SOURCE_RELATIVE_PATHS_V2],
        toolrepo_root=toolrepo_root,
    )
    if result.returncode != 0:
        return False
    return any(entry for entry in result.stdout.split("\0") if entry)


def _assert_no_untracked_source_v2(toolrepo_root: Path) -> None:
    # NO --exclude-standard: reproduced directly, that flag makes a stray
    # source file matched by ANY .gitignore/.git/info/exclude/global-ignore
    # entry invisible here, ignored or not, even though the file is fully
    # present and, if imported, fully executable. Ignore configuration is
    # not a source-identity authority.
    untracked = _run_toolrepo_git_v2(
        ["git", "ls-files", "--others", "-z", "--", *BOUNDED_SOURCE_RELATIVE_PATHS_V2],
        toolrepo_root=toolrepo_root,
    )
    if untracked.returncode != 0:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    source_candidates = [
        entry
        for entry in untracked.stdout.split("\0")
        if entry and entry.endswith(".py") and not _is_toolchain_artifact_v2(entry)
    ]
    if source_candidates:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2)


def _assert_bounded_source_clean_v2(toolrepo_root: Path) -> None:
    if not _bounded_source_present_in_tree_v2(toolrepo_root):
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)

    # The FULL declared set, unconditionally -- a bounded path deleted from
    # disk must surface as a tracked deletion here, never silently drop out
    # of the pathspec before Git is even asked about it.
    modified = _run_toolrepo_git_v2(
        ["git", "diff", "--name-only", "-z", "HEAD", "--", *BOUNDED_SOURCE_RELATIVE_PATHS_V2],
        toolrepo_root=toolrepo_root,
    )
    if modified.returncode != 0:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    if any(entry for entry in modified.stdout.split("\0") if entry):
        raise ToolrepoIdentityError(TOOLREPO_WORKTREE_DIRTY_REASON_V2)

    _assert_no_untracked_source_v2(toolrepo_root)


def establish_toolrepo_source_identity_v2(
    *, declared_toolrepo_sha: str, executing_script: Path | None = None
) -> ToolrepoSourceIdentityV2:
    """Prove all six clauses of ``TOOLREPO_SOURCE_IDENTITY_INVARIANT`` before
    any semantic review or transport. The caller's ``declared_toolrepo_sha``
    is checked against independently observed state -- it is never treated
    as evidence of what actually executed.
    """

    if not _TOOLREPO_SHA_RE.fullmatch(declared_toolrepo_sha):
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_MISMATCH_REASON_V2)

    toolrepo_root = resolve_toolrepo_root_v2(executing_script=executing_script)
    observed_head = _resolve_toolrepo_head_v2(toolrepo_root)
    if observed_head != declared_toolrepo_sha:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_MISMATCH_REASON_V2)

    _assert_bounded_source_clean_v2(toolrepo_root)

    return ToolrepoSourceIdentityV2(toolrepo_root=toolrepo_root, toolrepo_sha=observed_head)
