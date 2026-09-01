"""A caller-refusal-safe temporary workspace (`#200-G4`).

## The witness this module exists to close

The predecessor script's inner semantic step created its target-subject
workspace like this (paraphrased from `#277`'s final, never-fixed state)::

    target_subject_root = Path(tempfile.mkdtemp(prefix="agent-review-target-"))
    target_subject = materialise_controlled_target_subject_v2(...)   # (a)
    diff_text = read_caller_document_text_v2(arguments.diff, ...)    # (b)
    ...
    try:
        return _compose_and_emit_v2(...)
    finally:
        shutil.rmtree(target_subject_root, ignore_errors=True)

``mkdtemp`` ran *before* the ``try``, and so did both (a) -- which can refuse
with a reason such as ``subject_unknown_commit`` for a caller-supplied
``head_sha`` that names no known commit -- and (b) -- which can refuse with
``diff_unreadable`` for a caller-supplied ``--diff`` path. A refusal raised at
either point propagated straight out of the function, skipping the
``finally`` entirely, and left the materialised subject's bytes on disk. That
is a real, repeatable disk-space and information-exposure fault: it happens on
*every* run that refuses at those two specific points, not just adversarial
ones -- an ordinary typo in ``--head-sha`` was enough.

The fix is not "move the ``rmtree`` call" in one call site; the same shape of
bug reappears anywhere a caller-derived workspace is created ahead of the code
that populates it. `#200-G4` closes it structurally instead: nothing in this
codebase should call ``tempfile.mkdtemp`` for caller-derived material without
going through :func:`temp_workspace_v2`, whose ``finally`` is established in
the same expression that creates the directory, so there is no window between
"directory exists" and "cleanup is guaranteed" for any caller to land a
refusal in.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

__all__ = ["temp_workspace_v2"]


@contextlib.contextmanager
def temp_workspace_v2(*, prefix: str) -> Iterator[Path]:
    """Yield a fresh temp directory, guaranteed removed on every exit path.

    ``mkdtemp`` happens inside this function's own ``try``, not at the call
    site, so there is no way to obtain a workspace from this function without
    also obtaining the guarantee that it is removed -- whether the caller's
    block returns normally, or raises an ``OperationalIngressError``, a
    programmer defect, or anything else. Contrast this with creating the
    directory and immediately wrapping *subsequent* code in ``try/finally``:
    every such call site has to get the ordering right on its own, and
    `#277` proved that ordering gets it wrong under review pressure.
    """
    workspace = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
