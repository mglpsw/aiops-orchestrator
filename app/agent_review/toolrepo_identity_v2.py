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
        return subprocess.run(argv, cwd=toolrepo_root, capture_output=True, text=True, check=False)
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


def _assert_bounded_source_clean_v2(toolrepo_root: Path) -> None:
    existing_paths = [
        path for path in BOUNDED_SOURCE_RELATIVE_PATHS_V2 if (toolrepo_root / path).exists()
    ]
    if not existing_paths:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)

    modified = _run_toolrepo_git_v2(
        ["git", "diff", "--name-only", "-z", "HEAD", "--", *existing_paths], toolrepo_root=toolrepo_root
    )
    if modified.returncode != 0:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    if any(entry for entry in modified.stdout.split("\0") if entry):
        raise ToolrepoIdentityError(TOOLREPO_WORKTREE_DIRTY_REASON_V2)

    untracked = _run_toolrepo_git_v2(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *existing_paths],
        toolrepo_root=toolrepo_root,
    )
    if untracked.returncode != 0:
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2)
    if any(entry for entry in untracked.stdout.split("\0") if entry):
        raise ToolrepoIdentityError(TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2)


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
