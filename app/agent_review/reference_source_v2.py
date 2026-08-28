"""`#200-D` successor: immutable head-bound reference material (issue #200).

## Why this module exists

`build_chunk_payloads_from_profile_v2` reads a profile's declared
`artifacts`/`contracts` from a caller-supplied `repo_root` -- specifically
`Path.is_file()`/`read_text()`/`read_bytes()` against the WORKING TREE
(`payload_references_v2.py`). Diff and content, by contrast, come from Git
OBJECTS at `base_sha`/`head_sha` (`diff_acquisition_v2.acquire_authoritative_
diff_v2`). So the same `(base_sha, head_sha)` inputs can bind different
payload reference bytes depending only on which revision the checkout
happens to sit at, or on staged/untracked/modified files with no observable
difference in those inputs at all.

A first design considered a preflight check -- "does the working tree at
this moment match `head_sha`?" -- followed by letting the payload builder
re-read that same mutable `repo_root`. That is check-then-reread: nothing
prevents the working tree from changing between the check and the read, so
it establishes only `MaterialObservedDuringPreflight ⊆
MaterialProvenAt(head_sha)`, not the property actually needed:
`ConsumedReferenceMaterial ⊆ MaterialProvenAt(head_sha)`. Rejected.

This module instead REPLACES the byte source. `resolve_reference_source_v2`
materializes exactly the profile-declared reference paths from the immutable
Git tree at `head_sha` into a private, per-run, non-target directory, and the
existing payload owner is pointed at that directory instead of the target
checkout. `payload_references_v2`/`payload_builder_v2` are UNCHANGED --
`_build_chunk_payload_from_profile_v2` uses `repo_root` for exactly two
calls (`build_payload_artifact_references_v2`, `build_payload_contract_
references_v2`), and passing this module's materialized root satisfies both
without owner modification.

Precedent: `target_pack_build_v2` learned the identical lesson for a
different subject (`toolrepo_sha` pack material) after finding a dirty
tracked template installed bytes its own `toolrepo_sha` did not describe --
"read from `git show <sha>:<path>`, never `Path.read_bytes()` against the
working tree." This module is that same discipline generalized from a
toolrepo subject to a review-run subject.

## The Git tree entry at `head_sha` is the SOLE decision authority

The working tree is consulted NOWHERE in this module -- not for existence,
not for content, not for the checkout's current HEAD. The decision is a
total function of the tree entry alone:

    regular blob (100644/100755)  -> materialize the exact blob bytes
    no entry                      -> do not materialize; the existing
                                      payload owner's own missing-reference
                                      semantics apply unchanged (required ->
                                      `payload_required_artifact_missing`;
                                      optional -> `optional_artifact_missing`)
    symlink / gitlink / tree /
    other non-blob entry          -> `reference_source_material_unverifiable`

An earlier draft proposed refusing when a declared path was absent from
`head_sha` but present in the working tree, reasoning an operator should not
see "missing" for a file that visibly exists. That is deleted: it would make
an identical immutable subject (`repo_root`, `profile`, `base_sha`,
`head_sha`) produce different semantic results depending on incidental
mutable filesystem state -- exactly the nondeterminism this module exists to
remove. `WorkingTreePresence(path)` never alters the reference set, a
limitation, coverage, or any refusal.

A runtime-generated artifact absent from `head_sha` is therefore never
consumed and falls through to the existing missing-reference semantics --
never implicitly treated as HEAD-owned merely because a file with that name
exists on disk. Supporting a generated artifact as first-class review input
needs its own producer/digest/provenance authority; that is NOT built here
and belongs to the distribution/evidence line (`#203`, `#194`-`#198`).

Checkout HEAD is likewise not a blocking authority: the only `repo_root`
consumers anywhere in the composed run are this module (replaced) and
`review_content_extraction_v2`, whose `acquire_authoritative_diff_v2`/
`acquire_diff_v2` calls are `git diff base..head` reads against the OBJECT
DATABASE -- they require the objects to exist, not the checkout to be
sitting at `head_sha`. `run_assembly_v2`, `payload_set_emission_v2`,
`review_content_v2` and `manifest_v2` reference `repo_root` zero times. No
semantically consumed input depends on checkout HEAD, so this module reads
no working-tree state and resolves no checkout HEAD at all --
`git rev-parse HEAD` belongs only to `toolrepo_identity_v2`'s *different*
subject (the toolrepo, not the target), and is intentionally absent here.

    SemanticRunInputs = GitObjects(base_sha, head_sha)
                      + trusted profile/policy
                      + explicit bound run authorities

    NOT SemanticRunInputs + incidental target working-tree state

## Lifetime is structural, not caller convention

`resolve_reference_source_v2` is a context manager. The private directory is
created on entry and removed on exit -- success, a typed refusal, or an
unexpected defect -- so no caller can forget to clean it up, and no run
reuses another run's directory.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.diff_acquisition_v2 import (
    DiffAcquisitionError,
    read_head_tree_entry_v2,
)

REFERENCE_SOURCE_UNAVAILABLE_REASON_V2 = "reference_source_unavailable"
REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2 = "reference_source_material_unverifiable"

_PRIVATE_ROOT_PREFIX_V2 = "agent-review-reference-source-v2-"


class ReferenceSourceError(ValueError):
    """Raised when immutable head-bound reference material cannot be
    established. Carries a stable ``reason_code`` only -- never a path, an
    observed SHA, bytes, or git stderr."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ReferenceSourceV2:
    """Private, non-wire, non-persisted carrier: a filesystem root
    containing exactly the profile-declared artifact/contract reference
    paths that exist as regular blobs at ``head_sha``, materialized from
    Git objects. Never reused across runs; removed by the context manager
    that produced it. Has no schema, no hash, and is never serialized."""

    root: Path


def _declared_reference_paths_v2(profile: TargetProfileV2) -> tuple[str, ...]:
    return tuple(artifact.path for artifact in profile.artifacts) + tuple(
        contract.path for contract in profile.contracts
    )


def _materialize_v2(*, repo_root: Path, head_sha: str, profile: TargetProfileV2, root: Path) -> None:
    for relative_path in _declared_reference_paths_v2(profile):
        try:
            entry = read_head_tree_entry_v2(repo_root, head_sha=head_sha, relative_path=relative_path)
        except DiffAcquisitionError as exc:
            # `head_sha` was already proven resolvable earlier in the
            # composed run's gate order (diff acquisition runs first), so a
            # failure here is the object database itself refusing to answer
            # -- an environment condition, not a caller-input refusal.
            raise ReferenceSourceError(REFERENCE_SOURCE_UNAVAILABLE_REASON_V2) from exc

        if entry is None:
            # No entry at head_sha: leave unmaterialized. The existing
            # payload owner's own required/optional missing-reference
            # semantics apply unchanged -- this module invents no new
            # taxonomy for a condition its consumer already names.
            continue

        if entry.kind != "blob":
            # symlink, gitlink/submodule, or tree/directory: consuming any
            # of these would let the read leave the declared blob's bytes
            # entirely (a symlink may resolve anywhere the process can
            # reach on the filesystem).
            raise ReferenceSourceError(REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2)

        destination = (root / relative_path).resolve()
        if root.resolve() not in destination.parents:
            # `relative_path` comes from `contracts_v2.RelativePath`, which
            # already forbids traversal -- this is a structural assertion
            # that the invariant still holds, not a re-implementation of
            # that contract's own validation.
            raise ReferenceSourceError(REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2)

        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or destination.exists():
            # Every path is materialized at most once per fresh, private
            # directory; a second write to the same destination would mean
            # two profile entries share a path, which is unreachable given
            # `TargetProfileV2`'s own uniqueness validation -- kept as a
            # defensive refusal rather than a silent overwrite.
            raise ReferenceSourceError(REFERENCE_SOURCE_MATERIAL_UNVERIFIABLE_REASON_V2)
        destination.write_bytes(entry.content)


@contextmanager
def resolve_reference_source_v2(
    *, repo_root: Path, head_sha: str, profile: TargetProfileV2
) -> Iterator[ReferenceSourceV2]:
    """Materialize every profile-declared artifact/contract reference path
    that exists as a regular blob at ``head_sha`` into a private, per-run
    directory, and yield a carrier pointing at it. The directory is removed
    on exit regardless of outcome -- success, a typed refusal raised inside
    the ``with`` block, or an unexpected defect.

    ``repo_root`` is used only to locate the Git object database; its
    working-tree content is never read.
    """

    tmp_dir = Path(tempfile.mkdtemp(prefix=_PRIVATE_ROOT_PREFIX_V2))
    try:
        _materialize_v2(repo_root=repo_root, head_sha=head_sha, profile=profile, root=tmp_dir)
        yield ReferenceSourceV2(root=tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
