"""`#200-F` authority B -- inner authority travels on an exclusive channel.

`#276` carried inner authority on private argv flags and guarded them with a
textual blacklist; ``argparse`` prefix abbreviation walked past it via
``--_inner-d``. The round-3 note called the hole "closed at the mechanism" and
round 4 refuted that on first exposure.

Two claims are tested separately here, because conflating them is what went
wrong last time:

*exclusivity* -- the channel is an inherited file descriptor and there is no
argv or environment spelling of the same authority, so no flag syntax can
express it; and

*unforgeability* -- arriving on the channel is not enough. The document is
checked against the code that is actually executing, so a document that
disagrees with reality is refused even on the right channel.

The argv-level attacks (abbreviation, duplicate flag, ``--flag=value``,
invented private options, direct inner invocation through the product CLI)
are exercised end-to-end against the real CLI in the black-box product tests;
what is proved here is the channel contract those tests rely on.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from app.agent_review.operational_inner_control_v2 import (
    INNER_CONTROL_CHANNEL_ABSENT_REASON_V2,
    INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2,
    INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2,
    INNER_CONTROL_FD_V2,
    INNER_CONTROL_SCHEMA_ID_V2,
    INNER_CONTROL_SUBJECT_DIGEST_MISMATCH_REASON_V2,
    INNER_CONTROL_SUBJECT_EXCLUDES_LOADED_CODE_REASON_V2,
    INNER_CONTROL_SUBJECT_ROOT_MISMATCH_REASON_V2,
    InnerControlChannelError,
    InnerControlDocumentV2,
    compute_subject_digest_v2,
    encode_inner_control_document_v2,
    read_inner_control_document_v2,
    verify_inner_control_document_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

_REPOSITORY_ROOT_V2 = pathlib.Path(__file__).resolve().parents[2]


def _materialised_subject_v2(root: pathlib.Path) -> pathlib.Path:
    subject = root / "subject"
    (subject / "app" / "agent_review").mkdir(parents=True)
    (subject / "app" / "agent_review" / "worker.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (subject / "README.md").write_text("subject\n", encoding="utf-8")
    return subject


def _document_for_v2(subject: pathlib.Path) -> InnerControlDocumentV2:
    return InnerControlDocumentV2(
        subject_root=str(subject),
        declared_toolrepo_sha="a" * 40,
        subject_digest=compute_subject_digest_v2(subject),
    )


def _read_through_a_real_pipe_v2(payload: bytes) -> InnerControlDocumentV2:
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, payload)
        os.close(write_end)
        write_end = -1
        return read_inner_control_document_v2(read_end)
    finally:
        os.close(read_end)
        if write_end != -1:
            os.close(write_end)


def test_a_document_round_trips_through_a_real_descriptor(
    tmp_path: pathlib.Path,
) -> None:
    """Non-vacuity control for every refusal test below.

    Without it, a reader that rejected everything would satisfy them all.
    """
    subject = _materialised_subject_v2(tmp_path)
    document = _document_for_v2(subject)

    received = _read_through_a_real_pipe_v2(encode_inner_control_document_v2(document))

    assert received == document


def test_an_absent_channel_is_a_typed_refusal_not_a_crash() -> None:
    """Direct inner invocation, at the channel level.

    A process nobody handed the channel to is not an inner epoch. It says so
    with a reason code instead of an ``OSError`` traceback.
    """
    with pytest.raises(InnerControlChannelError) as caught:
        read_inner_control_document_v2(9999)

    assert caught.value.reason_code == INNER_CONTROL_CHANNEL_ABSENT_REASON_V2
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_an_empty_channel_is_refused() -> None:
    """An inherited but empty descriptor must not read as "no constraints"."""
    with pytest.raises(InnerControlChannelError) as caught:
        _read_through_a_real_pipe_v2(b"")

    assert caught.value.reason_code == INNER_CONTROL_CHANNEL_ABSENT_REASON_V2


@pytest.mark.parametrize(
    "payload, expected_reason",
    [
        (b"not json at all", INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2),
        (b"\xff\xfe\x00", INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2),
        (b'"a bare string"', INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2),
        (b"[1, 2, 3]", INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2),
        (b"{}", INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2),
    ],
)
def test_unreadable_or_malformed_documents_are_refused_by_shape(
    payload: bytes, expected_reason: str
) -> None:
    with pytest.raises(InnerControlChannelError) as caught:
        _read_through_a_real_pipe_v2(payload)

    assert caught.value.reason_code == expected_reason


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_id": "agent-review.inner-control.v1"},
        {"declared_toolrepo_sha": "short"},
        {"declared_toolrepo_sha": "Z" * 40},
        {"subject_digest": "f" * 63},
        {"subject_root": "relative/path"},
        {"extra_field": "smuggled"},
    ],
)
def test_a_document_that_does_not_match_the_contract_is_refused(
    tmp_path: pathlib.Path, mutation: dict[str, str]
) -> None:
    """Shape is checked before anything is believed.

    The extra-field case matters most: a document with a smuggled key is
    refused outright rather than silently ignored, so the channel cannot
    become a place to pass undeclared authority later.
    """
    subject = _materialised_subject_v2(tmp_path)
    payload = json.loads(encode_inner_control_document_v2(_document_for_v2(subject)))
    payload.update(mutation)

    with pytest.raises(InnerControlChannelError) as caught:
        _read_through_a_real_pipe_v2(json.dumps(payload).encode("utf-8"))

    assert caught.value.reason_code == INNER_CONTROL_DOCUMENT_MALFORMED_REASON_V2


def test_an_oversized_document_is_refused_rather_than_read(
    tmp_path: pathlib.Path,
) -> None:
    """An unbounded read off the channel is a denial-of-service.

    Delivered through a file descriptor rather than a pipe on purpose: a
    payload larger than the 64 KiB pipe buffer would deadlock the test writer
    itself, since nothing is draining the other end. The reader cannot tell
    the two kinds of descriptor apart, which is the point.
    """
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'{"schema_id":"x","pad":"' + b"p" * 70000 + b'"}')

    descriptor = os.open(oversized, os.O_RDONLY)
    try:
        with pytest.raises(InnerControlChannelError) as caught:
            read_inner_control_document_v2(descriptor)
    finally:
        os.close(descriptor)

    assert caught.value.reason_code == INNER_CONTROL_DOCUMENT_UNREADABLE_REASON_V2


def test_verification_rejects_a_document_pointing_away_from_the_running_code(
    tmp_path: pathlib.Path,
) -> None:
    """Unforgeability, part one.

    A document naming a subject root that does not contain the executing
    module would let a run's recorded toolrepo identity describe a tree that
    contributed none of the bytes that ran. Arriving on the exclusive channel
    does not make it true.
    """
    subject = _materialised_subject_v2(tmp_path)
    document = _document_for_v2(subject)

    elsewhere = tmp_path / "elsewhere" / "module.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("", encoding="utf-8")

    with pytest.raises(InnerControlChannelError) as caught:
        verify_inner_control_document_v2(document, executing_module_path=elsewhere)

    assert caught.value.reason_code == INNER_CONTROL_SUBJECT_ROOT_MISMATCH_REASON_V2


def test_verification_rejects_a_subject_swapped_after_materialisation(
    tmp_path: pathlib.Path,
) -> None:
    """Unforgeability, part two -- the TOCTOU window.

    The digest is recomputed from the bytes on disk at verification time, so
    content changed between materialisation and use is caught even though the
    document itself is untouched and well-formed.
    """
    subject = _materialised_subject_v2(tmp_path)
    document = _document_for_v2(subject)
    executing_module = subject / "app" / "agent_review" / "worker.py"

    assert verify_inner_control_document_v2(
        document, executing_module_path=executing_module, loaded_semantic_files=()
    ) == document

    executing_module.write_text("VALUE = 2  # swapped\n", encoding="utf-8")

    with pytest.raises(InnerControlChannelError) as caught:
        verify_inner_control_document_v2(
            document, executing_module_path=executing_module, loaded_semantic_files=()
        )

    assert caught.value.reason_code == INNER_CONTROL_SUBJECT_DIGEST_MISMATCH_REASON_V2


def test_the_digest_notices_every_kind_of_subject_change(
    tmp_path: pathlib.Path,
) -> None:
    """Content, addition, deletion and mode are all identity.

    A digest blind to any of these would let a subject be altered while still
    verifying, which would make ``declared_toolrepo_sha`` meaningless.
    """
    subject = _materialised_subject_v2(tmp_path)
    baseline = compute_subject_digest_v2(subject)

    target = subject / "app" / "agent_review" / "worker.py"

    target.write_text("VALUE = 2\n", encoding="utf-8")
    assert compute_subject_digest_v2(subject) != baseline
    target.write_text("VALUE = 1\n", encoding="utf-8")
    assert compute_subject_digest_v2(subject) == baseline

    os.chmod(target, 0o755)
    assert compute_subject_digest_v2(subject) != baseline, "mode is identity"
    os.chmod(target, 0o644)
    assert compute_subject_digest_v2(subject) == baseline

    added = subject / "app" / "agent_review" / "extra.py"
    added.write_text("", encoding="utf-8")
    assert compute_subject_digest_v2(subject) != baseline
    added.unlink()
    assert compute_subject_digest_v2(subject) == baseline

    (subject / "README.md").unlink()
    assert compute_subject_digest_v2(subject) != baseline


def test_a_symlink_is_hashed_as_its_target_text_not_followed(
    tmp_path: pathlib.Path,
) -> None:
    """A link planted in the subject must not import outside bytes silently.

    Following it would let the digest stay stable while the code actually
    executed came from somewhere the subject does not describe.
    """
    subject = _materialised_subject_v2(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("ORIGINAL = 1\n", encoding="utf-8")

    link = subject / "app" / "agent_review" / "linked.py"
    link.symlink_to(outside)
    with_link = compute_subject_digest_v2(subject)

    outside.write_text("REPLACED = 2\n", encoding="utf-8")
    assert compute_subject_digest_v2(subject) == with_link, (
        "the link target's contents are outside the subject and must not be "
        "silently absorbed into its identity"
    )

    link.unlink()
    link.symlink_to(tmp_path / "different.py")
    assert compute_subject_digest_v2(subject) != with_link, (
        "the link's target path is part of the subject and must be identity"
    )


def test_authority_arrives_only_by_inheritance_never_by_argv_or_environment(
    tmp_path: pathlib.Path,
) -> None:
    """Exclusivity, proved in a real child process.

    The child is handed a poisoned environment and a poisoned argv using every
    name the predecessor used, and no channel. It must still refuse. This is
    the property that makes abbreviation, duplicate-flag and ``--flag=value``
    attacks structurally impossible rather than merely blocked: there is
    nothing for them to spell.
    """
    subject = _materialised_subject_v2(tmp_path)
    script = tmp_path / "inner_probe.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[1])
            from app.agent_review.operational_inner_control_v2 import (
                InnerControlChannelError,
                read_inner_control_document_v2,
            )
            try:
                read_inner_control_document_v2()
            except InnerControlChannelError as exc:
                print(exc.reason_code)
            else:
                print("AUTHORITY-OBTAINED-WITHOUT-THE-CHANNEL")
            """
        ),
        encoding="utf-8",
    )

    poisoned_environment = dict(os.environ)
    poisoned_environment.update(
        {
            "AGENT_REVIEW_INNER_SUBJECT_ROOT": str(subject),
            "AGENT_REVIEW_INNER_DECLARED_TOOLREPO_SHA": "b" * 40,
            "AGENT_REVIEW_CONTROLLED_INNER": "1",
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            str(_REPOSITORY_ROOT_V2),
            "--_controlled-inner",
            "--_inner-subject-root",
            str(subject),
            "--_inner-declared-toolrepo-sha",
            "b" * 40,
            "--_inner-d",
            "b" * 40,
        ],
        capture_output=True,
        text=True,
        env=poisoned_environment,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == INNER_CONTROL_CHANNEL_ABSENT_REASON_V2


def test_the_channel_contract_names_a_descriptor_not_a_flag() -> None:
    """Guards the shape of the mechanism itself.

    If someone later reintroduces an argv spelling, the constant it would need
    to reference is a descriptor number, not an option string -- and the
    module exports nothing flag-shaped.
    """
    import app.agent_review.operational_inner_control_v2 as channel_module

    assert isinstance(INNER_CONTROL_FD_V2, int)
    assert INNER_CONTROL_FD_V2 >= 3, "0/1/2 are stdio and cannot carry authority"
    assert INNER_CONTROL_SCHEMA_ID_V2 == "agent-review.inner-control.v2"

    for exported_name in channel_module.__all__:
        value = getattr(channel_module, exported_name)
        if isinstance(value, str):
            assert not value.startswith("-"), (
                f"{exported_name} looks like a command-line flag; inner "
                "authority must have no argv spelling"
            )


def test_the_narrowing_forgery_is_refused_with_real_interpreter_state() -> None:
    """Lane A P0. Narrowing, not substitution, was the hole.

    The forgery: declare ``subject_root = <repo>/scripts``. That directory
    genuinely contains the entry script, so the executing-module check passes,
    and the caller can compute its digest with this module's own public helper
    so the digest check passes too. But ``scripts/`` contains none of
    ``app/agent_review/``, so the digest covered no part of the code that
    actually performs the review, and ``declared_toolrepo_sha`` described a
    tree that had contributed almost nothing to the run.

    Deliberately run against the **default** ``loaded_semantic_files``, i.e.
    real ``sys.modules`` state: the injectable parameter exists for synthetic
    fixtures, and a test of the forgery that used it would be testing nothing.

    The prior test only tried ``subject_root=/tmp/attacker``, an *unrelated*
    directory. That is why it stayed green: checking a containing directory is
    not the same as checking the code.
    """
    narrowed = _REPOSITORY_ROOT_V2 / "scripts"
    document = InnerControlDocumentV2(
        subject_root=str(narrowed),
        declared_toolrepo_sha="b" * 40,
        subject_digest=compute_subject_digest_v2(narrowed),
    )

    assert (narrowed / "aiops-review-run-v2.py").is_file(), (
        "non-vacuity: the narrowed root really does contain the entry script, "
        "so the executing-module check alone would pass"
    )
    assert not (narrowed / "app").exists(), (
        "non-vacuity: and it really does exclude the semantic package"
    )

    with pytest.raises(InnerControlChannelError) as caught:
        verify_inner_control_document_v2(
            document,
            executing_module_path=narrowed / "aiops-review-run-v2.py",
        )

    assert caught.value.reason_code == (
        INNER_CONTROL_SUBJECT_EXCLUDES_LOADED_CODE_REASON_V2
    )


def test_the_honest_repository_root_still_verifies() -> None:
    """Non-vacuity control for the test above.

    A check that rejected every root would satisfy it and break the product.
    The real repository root contains both the entry script and every loaded
    semantic module, so it must verify.
    """
    document = InnerControlDocumentV2(
        subject_root=str(_REPOSITORY_ROOT_V2),
        declared_toolrepo_sha="c" * 40,
        subject_digest=compute_subject_digest_v2(_REPOSITORY_ROOT_V2),
    )

    verified = verify_inner_control_document_v2(
        document,
        executing_module_path=_REPOSITORY_ROOT_V2 / "scripts" / "aiops-review-run-v2.py",
    )

    assert verified.declared_toolrepo_sha == "c" * 40
