"""`#200-F` authority B -- public argv cannot express inner authority.

## What `#276` did, and why repairing it was refused

The predecessor carried inner authority on private argv flags
(``--_controlled-inner``, ``--_inner-subject-root``,
``--_inner-declared-toolrepo-sha``) and guarded them with a textual blacklist.
``argparse`` accepts unambiguous abbreviations, so ``--_inner-d`` walked
straight past a guard that compared whole tokens. The round-3 note claimed the
hole was "closed at the mechanism"; round 4 refuted that on first exposure.

``allow_abbrev=False`` would have closed *that* bypass. It would not have
closed the class. As long as the public parser has a syntax for inner
authority, every future change to the parser is another chance to reopen it.
So the flags are retired outright:

    **there is no argv spelling of inner authority to get wrong.**

## Two properties, deliberately not conflated

An exclusive channel and an unforgeable one are different claims, and `#276`
came to grief by asserting the second while only building the first.

*Exclusivity* (this module's channel). Inner authority arrives on an inherited
file descriptor the parent creates. Ordinary argv has no field for it, so it
cannot be expressed by abbreviation, duplicate flag, ``--flag=value``, or any
private-looking option a caller invents.

*Unforgeability* (this module's verification). Exclusivity alone would still
let a directly-invoked inner accept whatever document it was handed. So the
document is never trusted on arrival: the inner checks that **every loaded
module of the semantic package** lives under the declared subject root, and
that the bytes under that root digest to the declared value. A document that
disagrees with reality is refused even though it arrived on the right channel.

The "every loaded module" part is load-bearing and was missing in the first
revision, which checked only the entry script. A caller could then *narrow*
the declared root to ``scripts/`` -- which genuinely contains the entry script
-- and compute its digest with this module's own public helper, satisfying
both checks while the digest covered none of ``app/agent_review/``. The
declared toolrepo sha described a tree that had contributed almost nothing to
the run. Narrowing, not substitution, was the hole; checking a *containing*
directory is not the same as checking the code.

Neither property is asked to cover for the other, and neither is described
here as more than it is. A person who can already run arbitrary code on the
host can, of course, run their own program; that is not the boundary. The
boundary is that **a caller of the product CLI cannot cause the product to
emit an artifact whose declared toolrepo identity differs from the code that
produced it**, and cannot reach the inner epoch at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

__all__ = [
    "INNER_CONTROL_CHANNEL_ABSENT_REASON_V2",
    "INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2",
    "INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2",
    "INNER_CONTROL_FD_V2",
    "INNER_CONTROL_SCHEMA_ID_V2",
    "INNER_CONTROL_SUBJECT_DIGEST_MISMATCH_REASON_V2",
    "INNER_CONTROL_SUBJECT_EXCLUDES_LOADED_CODE_REASON_V2",
    "INNER_CONTROL_SUBJECT_ROOT_MISMATCH_REASON_V2",
    "InnerControlChannelError",
    "InnerControlDocumentV2",
    "compute_subject_digest_v2",
    "loaded_semantic_package_files_v2",
    "encode_inner_control_document_v2",
    "read_inner_control_document_v2",
    "verify_inner_control_document_v2",
]


#: The parent creates this descriptor and passes it to the child. The *number*
#: is a convention, not a secret and not the authority: everything that makes
#: the document trustworthy is verified in
#: :func:`verify_inner_control_document_v2`.
INNER_CONTROL_FD_V2 = 3

INNER_CONTROL_SCHEMA_ID_V2 = "agent-review.inner-control.v2"

INNER_CONTROL_CHANNEL_ABSENT_REASON_V2 = "inner_control_channel_absent"
INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2 = "inner_control_document_unreadable"
INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2 = "inner_control_document_malformed"
INNER_CONTROL_SUBJECT_ROOT_MISMATCH_REASON_V2 = "inner_control_subject_root_mismatch"
INNER_CONTROL_SUBJECT_DIGEST_MISMATCH_REASON_V2 = "inner_control_subject_digest_mismatch"
INNER_CONTROL_SUBJECT_EXCLUDES_LOADED_CODE_REASON_V2 = (
    "inner_control_subject_excludes_loaded_code"
)

_MAX_CONTROL_DOCUMENT_BYTES_V2 = 64 * 1024


class InnerControlChannelError(ExpectedOperationalRefusalV2, ValueError):
    """Raised when inner authority is absent, malformed, or contradicted.

    Content-free ``reason_code`` only: the document names local filesystem
    paths, and a refusal must not print them.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class InnerControlDocumentV2:
    """Outer-derived authority for one inner epoch.

    Every field is derived by the parent from material the parent controls.
    None of it is caller-supplied, and none of it has a public argv spelling.
    """

    subject_root: str
    declared_toolrepo_sha: str
    subject_digest: str


def compute_subject_digest_v2(subject_root: Path) -> str:
    """Digest the materialised subject's bytes, deterministically.

    Sorted relative POSIX paths with their file modes and content hashes, so
    the digest is stable across filesystems and independent of directory
    iteration order. Symlinks are hashed as their *target text* rather than
    followed: following them would let a link planted inside the subject pull
    in bytes from outside it and still digest as unchanged.
    """
    entries: list[str] = []
    for path in sorted(subject_root.rglob("*")):
        relative = path.relative_to(subject_root).as_posix()
        if path.is_symlink():
            entries.append(f"l\x00{relative}\x00{os.readlink(path)}")
        elif path.is_dir():
            entries.append(f"d\x00{relative}")
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            executable = "1" if os.access(path, os.X_OK) else "0"
            entries.append(f"f\x00{relative}\x00{executable}\x00{digest}")
        else:
            # A device node, fifo or socket has no reviewable byte content and
            # has no business inside a materialised source subject.
            entries.append(f"?\x00{relative}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def encode_inner_control_document_v2(document: InnerControlDocumentV2) -> bytes:
    """Canonical bytes the parent writes to the channel."""
    return json.dumps(
        {
            "schema_id": INNER_CONTROL_SCHEMA_ID_V2,
            "subject_root": document.subject_root,
            "declared_toolrepo_sha": document.declared_toolrepo_sha,
            "subject_digest": document.subject_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def read_inner_control_document_v2(
    control_fd: int = INNER_CONTROL_FD_V2,
) -> InnerControlDocumentV2:
    """Read inner authority from the inherited descriptor.

    There is deliberately **no argv fallback and no environment fallback**. A
    process started without the channel is not an inner epoch, and says so
    with a typed refusal rather than guessing.
    """
    try:
        with os.fdopen(os.dup(control_fd), "rb", closefd=True) as channel:
            raw = channel.read(_MAX_CONTROL_DOCUMENT_BYTES_V2 + 1)
    except OSError:
        # The overwhelmingly common case is EBADF: nobody passed the channel,
        # i.e. someone tried to run the inner directly.
        raise InnerControlChannelError(INNER_CONTROL_CHANNEL_ABSENT_REASON_V2) from None

    if not raw:
        raise InnerControlChannelError(INNER_CONTROL_CHANNEL_ABSENT_REASON_V2)
    if len(raw) > _MAX_CONTROL_DOCUMENT_BYTES_V2:
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2) from None

    if not isinstance(payload, dict):
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)
    if payload.get("schema_id") != INNER_CONTROL_SCHEMA_ID_V2:
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)

    expected_keys = {"schema_id", "subject_root", "declared_toolrepo_sha", "subject_digest"}
    if set(payload) != expected_keys:
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)

    subject_root = payload["subject_root"]
    declared_toolrepo_sha = payload["declared_toolrepo_sha"]
    subject_digest = payload["subject_digest"]
    if not all(
        isinstance(value, str)
        for value in (subject_root, declared_toolrepo_sha, subject_digest)
    ):
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)
    if len(declared_toolrepo_sha) != 40 or any(
        character not in "0123456789abcdef" for character in declared_toolrepo_sha
    ):
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)
    if len(subject_digest) != 64 or any(
        character not in "0123456789abcdef" for character in subject_digest
    ):
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)
    if not Path(subject_root).is_absolute():
        raise InnerControlChannelError(INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2)

    return InnerControlDocumentV2(
        subject_root=subject_root,
        declared_toolrepo_sha=declared_toolrepo_sha,
        subject_digest=subject_digest,
    )


def loaded_semantic_package_files_v2() -> tuple[Path, ...]:
    """Every file currently loaded from the semantic package.

    Asked of ``sys.modules`` rather than of the filesystem because the
    question is "which code did this interpreter actually import", and only
    the interpreter can answer that.
    """
    discovered: list[Path] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("app.agent_review"):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is not None:
            discovered.append(Path(module_file))
    return tuple(discovered)


def verify_inner_control_document_v2(
    document: InnerControlDocumentV2,
    *,
    executing_module_path: Path,
    loaded_semantic_files: tuple[Path, ...] | None = None,
) -> InnerControlDocumentV2:
    """Check the document against the code that is actually running.

    Arriving on the exclusive channel is *not* sufficient. Two independent
    facts are confirmed against the filesystem:

    1. the module doing the verifying really is inside the declared subject
       root -- otherwise a document could point the run's recorded identity at
       a tree that contributed none of the executing bytes;
    2. the bytes under that root digest to the declared value -- otherwise the
       subject could be swapped between materialisation and use.

    Together these make ``declared_toolrepo_sha`` an assertion *about the code
    that ran*, which is what an emitted artifact claims it to be. This is the
    part `#276` was missing when it called the flag guard a fix.
    """
    subject_root = Path(document.subject_root).resolve()
    resolved_module = executing_module_path.resolve()

    if not resolved_module.is_relative_to(subject_root):
        raise InnerControlChannelError(INNER_CONTROL_SUBJECT_ROOT_MISMATCH_REASON_V2)

    # The entry script being inside the declared root is NOT sufficient, and
    # assuming it was is how a caller could forge inner authority.
    #
    # The attack: declare `subject_root = <repo>/scripts`. That directory
    # really does contain the entry script, so the check above passes, and the
    # caller can compute its digest with this module's own public helper so
    # the check below passes too. But `scripts/` contains none of
    # `app/agent_review/` -- the semantic code that actually performs the
    # review -- so the digest covers no part of it. The declared
    # toolrepo sha then describes a tree that contributed almost nothing to
    # the run, and tampering with the semantic modules is invisible.
    #
    # Every loaded module of the semantic package must therefore live inside
    # the digested root.
    #
    # `loaded_semantic_files` is injectable ONLY so a unit test can describe a
    # synthetic subject; production always takes the default, which reads the
    # real interpreter state. It is not a way to opt out -- passing an empty
    # tuple asserts "no semantic code is loaded", which is a claim a test
    # makes about its own fixture, not something a caller of the product can
    # arrange.
    if loaded_semantic_files is None:
        loaded_semantic_files = loaded_semantic_package_files_v2()
    for module_file in loaded_semantic_files:
        if not module_file.resolve().is_relative_to(subject_root):
            raise InnerControlChannelError(
                INNER_CONTROL_SUBJECT_EXCLUDES_LOADED_CODE_REASON_V2
            )

    if compute_subject_digest_v2(subject_root) != document.subject_digest:
        raise InnerControlChannelError(INNER_CONTROL_SUBJECT_DIGEST_MISMATCH_REASON_V2)

    return document
