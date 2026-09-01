"""`#200-G4` -- a caller-refusal cannot leak a materialised workspace.

## The witness this module exists to close

`#277`'s never-fixed final state created its target-subject temp directory
*before* the ``try`` that guarded its cleanup::

    target_subject_root = Path(tempfile.mkdtemp(prefix="agent-review-target-"))
    target_subject = materialise_controlled_target_subject_v2(...)   # can refuse
    diff_text = read_caller_document_text_v2(arguments.diff, ...)    # can refuse
    ...
    try:
        return _compose_and_emit_v2(...)
    finally:
        shutil.rmtree(target_subject_root, ignore_errors=True)

A refusal raised while materialising the subject (``subject_unknown_commit``,
for an ordinary caller typo in ``--head-sha``) or while reading the diff
(``diff_unreadable``) propagated straight out, skipping the ``finally``
entirely, and left the materialised subject's bytes on disk -- independently
reproduced in `#277` round 2, never fixed before the STOP.

``temp_workspace_v2`` closes this structurally: the directory cannot be
obtained without also obtaining the cleanup guarantee, because both are
established by the same context-manager frame.
"""

from __future__ import annotations

import pathlib

import pytest

from app.agent_review.operational_workspace_v2 import temp_workspace_v2


def test_the_workspace_exists_while_the_block_runs() -> None:
    captured: pathlib.Path | None = None
    with temp_workspace_v2(prefix="g4-workspace-test-") as workspace:
        captured = workspace
        assert workspace.is_dir()
        (workspace / "materialised-subject-bytes.txt").write_text("secret bytes", encoding="utf-8")
        assert (workspace / "materialised-subject-bytes.txt").is_file()

    assert captured is not None
    assert not captured.exists()


def test_the_workspace_is_removed_when_the_block_raises_a_typed_refusal() -> None:
    """The exact `#277` shape: a refusal raised *after* materialisation,
    modelling `subject_unknown_commit`."""

    class _SimulatedSubjectUnknownCommitRefusal(Exception):
        pass

    captured: pathlib.Path | None = None
    with pytest.raises(_SimulatedSubjectUnknownCommitRefusal):
        with temp_workspace_v2(prefix="g4-workspace-test-") as workspace:
            captured = workspace
            (workspace / "materialised-subject-bytes.txt").write_text("x", encoding="utf-8")
            raise _SimulatedSubjectUnknownCommitRefusal("subject_unknown_commit")

    assert captured is not None
    assert not captured.exists(), (
        "materialised subject bytes leaked to disk on a refusal path -- "
        "the exact #277 round-2 witness"
    )


def test_the_workspace_is_removed_when_the_block_raises_for_diff_unreadable() -> None:
    """The second `#277` shape: refusal raised while reading the diff,
    strictly after materialisation but before composition."""

    class _SimulatedDiffUnreadableRefusal(Exception):
        pass

    captured: pathlib.Path | None = None
    with pytest.raises(_SimulatedDiffUnreadableRefusal):
        with temp_workspace_v2(prefix="g4-workspace-test-") as workspace:
            captured = workspace
            # materialisation succeeded (bytes written)...
            (workspace / "materialised-subject-bytes.txt").write_text("x", encoding="utf-8")
            # ...then reading the diff refuses.
            raise _SimulatedDiffUnreadableRefusal("diff_unreadable")

    assert captured is not None
    assert not captured.exists()


def test_the_workspace_is_removed_on_a_genuine_programmer_defect_too() -> None:
    """Cleanup is unconditional -- it must not depend on the raised
    exception being a member of any particular family. A defect must still
    be cleaned up after (even though it must NOT be converted into a typed
    refusal; that is asserted elsewhere, this file only owns cleanup)."""
    captured: pathlib.Path | None = None
    with pytest.raises(AssertionError):
        with temp_workspace_v2(prefix="g4-workspace-test-") as workspace:
            captured = workspace
            raise AssertionError("a genuine internal programmer defect")

    assert captured is not None
    assert not captured.exists()


def test_pre_fix_ordering_really_did_leak() -> None:
    """Proves the RED witness is real: reproducing `#277`'s exact ordering
    (mkdtemp, then a step that can refuse, THEN the try/finally) leaks the
    directory on refusal, independent of this module's fix."""
    import shutil
    import tempfile

    workspace = pathlib.Path(tempfile.mkdtemp(prefix="g4-workspace-red-witness-"))
    try:
        (workspace / "materialised-subject-bytes.txt").write_text("x", encoding="utf-8")

        def _step_that_refuses() -> None:
            raise ValueError("subject_unknown_commit")

        try:
            _step_that_refuses()  # mkdtemp already ran; this is OUTSIDE any try/finally
        except ValueError:
            pass  # the predecessor's refusal handling -- note: no cleanup happened above
    finally:
        pass  # deliberately NOT cleaning up here, to mirror the exact ordering bug

    assert workspace.exists(), "the leak this module exists to close"
    shutil.rmtree(workspace, ignore_errors=True)  # test's own cleanup, not the product's
