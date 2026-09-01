"""`#200-G4` -- public caller input is its own pre-seal authority.

## The witness this module exists to close

`#276` round 4::

    aiops-review-run-v2 --delivery-id 'bad id here' ...

``delivery_id`` is a *required public flag*, and ``'bad id here'`` is an
ordinary wrong value, not an attack. It reached ``RunOriginV2`` deep inside the
semantic layer, where ``SafeIdentifier`` rejected it with a raw
``pydantic.ValidationError``. The traceback escaped the boundary and printed
virtualenv and subject temp-directory paths. The control that was supposed to
prevent exactly this was green, because it could only see ``reason_code``
bearing classes in ``app.agent_review`` and ``ValidationError`` is neither.

`#277` closed the nine scalar flags and, after its own round-1 correction,
``--profile``/``--grouping-policy`` file *content*. Round-2 independent review
then found the exact same class of leak still open one hop over:
``--responses`` file content was still read raw in the operational script's
offline transport, producing an unhandled ``UnicodeDecodeError`` /
``pydantic.ValidationError`` traceback that printed the subject's temp
directory to stderr. That witness, plus three siblings (temp-directory
cleanup skipped on certain refusal paths, an ``OverflowError`` on an
out-of-range inner-control-fd value, and the argparse usage-error path itself
echoing caller bytes) were independently reproduced and never fixed before
`#277` stopped. `#200-G4` exists to close them.

The lesson is not "add ValidationError to the catch list". It is that
**caller material must never reach the semantic layer at all**. Validation
belongs at ingress, before the subject is sealed, where a rejection is an
ordinary product outcome rather than a crash in the middle of a run. That
discipline applies identically whether the material arrived as an argv flag,
a file path, file content read from a caller-supplied path, one entry among
many in a caller-supplied directory, or environment/control metadata read at
the process boundary -- the trust boundary follows *provenance*, not
transport.

## Two epochs, and the deliberate asymmetry

===================  =======================  ==========================
epoch                bad material means       boundary behaviour
===================  =======================  ==========================
pre-seal (here)      the caller got it wrong  typed refusal, reason code,
                                              content-free stderr, no
                                              traceback
post-seal            *we* got it wrong        raw programmer defect,
                                              traceback, no reason code
===================  =======================  ==========================

The second row is a requirement, not an omission. This module deliberately
exports no way to convert a post-seal failure. If an internal derivation
produces malformed material after the seal, that is a defect in this codebase,
and dressing it up as an orderly refusal would make the product misreport its
own health.

## Why the reason codes are derived, not enumerated

Field-specific codes for the nine scalar flags are computed from the model's
own ``model_fields``. Adding a public input to the model adds its reason code
automatically -- there is no second list to forget to update, which is the
failure mode this whole slice exists to retire.

Document- and directory-shaped sources (``--profile``, ``--grouping-policy``,
``--diff``, one entry of ``--responses``) instead carry a reason code named
after the *flag*, never the value and never a per-entry key such as a chunk
id -- a chunk id inside ``--responses`` can itself be derived from
caller-supplied diff content, so it is exactly the kind of value that must
never reach a reason code or stderr.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    PositiveInt,
    Repository,
    RunOriginV2,
    SafeIdentifier,
    Sha256,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

__all__ = [
    "INGRESS_DOCUMENT_INVALID_REASON_V2",
    "INGRESS_DOCUMENT_UNREADABLE_REASON_V2",
    "INGRESS_INVALID_PUBLIC_INPUT_REASON_V2",
    "INGRESS_PATH_NOT_A_FILE_REASON_V2",
    "INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2",
    "INGRESS_PATH_NOT_ABSOLUTE_REASON_V2",
    "INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2",
    "INGRESS_RESPONSE_ESCAPES_DIRECTORY_REASON_V2",
    "INGRESS_CONTROL_FD_INVALID_REASON_V2",
    "INGRESS_USAGE_ERROR_REASON_V2",
    "OperationalIngressError",
    "ValidatedPublicInputsV2",
    "public_input_reason_code_v2",
    "validate_public_inputs_v2",
    "validate_existing_directory_v2",
    "validate_existing_file_v2",
    "read_caller_document_text_v2",
    "validate_caller_document_v2",
    "read_offline_response_document_v2",
    "resolve_inner_control_fd_v2",
    "NoEchoArgumentParserV2",
]


INGRESS_INVALID_PUBLIC_INPUT_REASON_V2 = "operational_ingress_invalid_public_input"
INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2 = "operational_ingress_unknown_public_input"
INGRESS_PATH_NOT_ABSOLUTE_REASON_V2 = "operational_ingress_path_not_absolute"
INGRESS_PATH_NOT_A_FILE_REASON_V2 = "operational_ingress_path_not_a_file"
INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2 = "operational_ingress_path_not_a_directory"
INGRESS_DOCUMENT_UNREADABLE_REASON_V2 = "operational_ingress_document_unreadable"
INGRESS_DOCUMENT_INVALID_REASON_V2 = "operational_ingress_document_invalid"
INGRESS_RESPONSE_ESCAPES_DIRECTORY_REASON_V2 = "operational_ingress_response_escapes_directory"
INGRESS_CONTROL_FD_INVALID_REASON_V2 = "operational_ingress_invalid_control_fd"
INGRESS_USAGE_ERROR_REASON_V2 = "operational_ingress_usage_error"


class OperationalIngressError(ExpectedOperationalRefusalV2, ValueError):
    """Raised when public caller material is unusable.

    Carries a stable, content-free ``reason_code`` naming the *field* at
    fault. It never carries the offending value: a caller who passes a token
    or a local path as the wrong flag must not have it echoed back through
    logs.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _PublicInputsModelV2(ContractV2Model):
    """The public surface of a run, typed with the canonical v2 field types.

    Only what a *caller* legitimately supplies. Derived identity components --
    ``profile_hash``, ``policy_hash``, ``manifest_hash``, ``evidence_hash`` --
    are absent on purpose: they are computed by the product from material it
    controls, and accepting them from argv is precisely the authority leak
    that `#200-F` authority B exists to close.
    """

    repo: Repository
    pr_number: PositiveInt
    base_sha: GitSha
    head_sha: GitSha
    tested_merge_sha: GitSha
    toolchain_digest: Sha256
    event_type: Literal["pull_request", "pull_request_target", "manual", "replay"]
    event_action: Literal[
        "opened", "reopened", "synchronize", "ready_for_review", "manual", "replay"
    ]
    delivery_id: SafeIdentifier


PUBLIC_INPUT_FIELD_NAMES_V2: tuple[str, ...] = tuple(_PublicInputsModelV2.model_fields)


def public_input_reason_code_v2(field_name: str) -> str:
    """Derive the reason code for one public input field.

    Derived from the field name rather than looked up in a table, so a new
    public input cannot ship without a code.
    """
    if field_name not in PUBLIC_INPUT_FIELD_NAMES_V2:
        return INGRESS_INVALID_PUBLIC_INPUT_REASON_V2
    return f"operational_ingress_invalid_{field_name}"


@dataclass(frozen=True)
class ValidatedPublicInputsV2:
    """Caller material that has passed the pre-seal gate.

    Existence of this value is the evidence that ingress ran. The semantic
    layer accepts this type and never raw strings, so "did anyone validate
    this?" is answered by the type system instead of by convention.
    """

    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    tested_merge_sha: str
    toolchain_digest: str
    event_type: str
    event_action: str
    delivery_id: str

    def as_run_origin_v2(self) -> RunOriginV2:
        """Rebuild the canonical origin contract.

        Cannot fail for a value of this type: the same model validated the
        event pair during ingress. Constructing it here rather than storing it
        keeps a single source of truth for the event-pair rule.
        """
        return RunOriginV2(
            event_type=self.event_type,  # type: ignore[arg-type]
            event_action=self.event_action,  # type: ignore[arg-type]
            delivery_id=self.delivery_id,
        )


def _first_offending_field_v2(error: ValidationError) -> str | None:
    """Name the first field pydantic rejected, if it names one at all.

    Only the leading location element is consulted, and only when it matches a
    declared field. Deeper elements can contain list indices and, for some
    error classes, caller-derived keys -- neither belongs in a reason code.
    """
    for detail in error.errors():
        location = detail.get("loc") or ()
        if location and isinstance(location[0], str):
            if location[0] in PUBLIC_INPUT_FIELD_NAMES_V2:
                return location[0]
    return None


def validate_public_inputs_v2(raw_inputs: Mapping[str, Any]) -> ValidatedPublicInputsV2:
    """Validate every public caller input, before anything is sealed.

    Raises ``OperationalIngressError`` -- a member of the operational refusal
    family -- for any invalid material. ``pydantic.ValidationError`` is caught
    *here and only here*: this is the one place in the product where foreign
    validation machinery meets caller material, so it is the one place where
    translating it is honest.
    """
    unknown = sorted(set(raw_inputs) - set(PUBLIC_INPUT_FIELD_NAMES_V2))
    if unknown:
        # Reported without naming the keys: an unknown key is caller-supplied
        # text and could itself be a secret pasted into the wrong flag.
        raise OperationalIngressError(INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2)

    try:
        model = _PublicInputsModelV2(**raw_inputs)
    except ValidationError as exc:
        field_name = _first_offending_field_v2(exc)
        if field_name is None:
            # A model-level rule failed (the event_type/event_action pair is
            # the live example) rather than a single field.
            raise OperationalIngressError(INGRESS_INVALID_PUBLIC_INPUT_REASON_V2) from None
        raise OperationalIngressError(public_input_reason_code_v2(field_name)) from None

    try:
        RunOriginV2(
            event_type=model.event_type,
            event_action=model.event_action,
            delivery_id=model.delivery_id,
        )
    except ValidationError:
        raise OperationalIngressError(INGRESS_INVALID_PUBLIC_INPUT_REASON_V2) from None

    return ValidatedPublicInputsV2(
        repo=model.repo,
        pr_number=model.pr_number,
        base_sha=model.base_sha,
        head_sha=model.head_sha,
        tested_merge_sha=model.tested_merge_sha,
        toolchain_digest=model.toolchain_digest,
        event_type=model.event_type,
        event_action=model.event_action,
        delivery_id=model.delivery_id,
    )


def _validated_absolute_path_v2(candidate: str | os.PathLike[str]) -> Path:
    path = Path(candidate)
    if not path.is_absolute():
        raise OperationalIngressError(INGRESS_PATH_NOT_ABSOLUTE_REASON_V2)
    return path


def validate_existing_file_v2(candidate: str | os.PathLike[str]) -> Path:
    """A caller-supplied path that must already name a readable file.

    Absoluteness is required rather than resolved. Silently resolving a
    relative path against the process CWD would make the run depend on where
    the caller happened to stand, which is not identity.
    """
    path = _validated_absolute_path_v2(candidate)
    if not path.is_file():
        raise OperationalIngressError(INGRESS_PATH_NOT_A_FILE_REASON_V2)
    return path


def validate_existing_directory_v2(candidate: str | os.PathLike[str]) -> Path:
    path = _validated_absolute_path_v2(candidate)
    if not path.is_dir():
        raise OperationalIngressError(INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2)
    return path


def read_caller_document_text_v2(path: str | os.PathLike[str], *, field_name: str) -> str:
    """Read a caller-supplied file as UTF-8 text, or refuse in-family.

    File *contents* are caller material exactly as much as flag values are.
    The first revision of this module validated the nine scalar inputs and
    then handed ``--profile`` and ``--grouping-policy`` straight to
    ``model_validate_json`` **after the seal**, where a malformed document
    produced a raw ``pydantic.ValidationError`` traceback that printed the
    virtualenv path, the subject temp directory, and -- worst -- pydantic's
    ``input_value=`` echo of the offending bytes. A credential sitting in a
    misconfigured profile was therefore printed to stderr.

    That is the `#276` round-4 witness in a different flag, closed for the
    scalars and left open for the documents. Reading and validating them here
    is the same authority applied to the same class of material.
    """
    resolved = validate_existing_file_v2(path)
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise OperationalIngressError(
            f"{INGRESS_DOCUMENT_UNREADABLE_REASON_V2}_{field_name}"
        ) from None


def validate_caller_document_v2(
    path: str | os.PathLike[str], *, model: type[Any], field_name: str
) -> Any:
    """Parse a caller-supplied JSON document into a contract model.

    Raises ``OperationalIngressError`` -- never ``ValidationError``. The reason
    code names the *flag*, never the document's contents: a profile is exactly
    the kind of file people accidentally put a token in, so nothing parsed
    from it may reach stderr.
    """
    raw_text = read_caller_document_text_v2(path, field_name=field_name)
    try:
        return model.model_validate_json(raw_text)
    except ValidationError:
        # Deliberately narrow. `#277`'s original revision caught bare
        # `Exception` here ("normalised; the cause must not escape"), which
        # `#200-G4`'s own bidirectional-invariant tests proved unsound: a
        # genuine defect in the `model` argument itself -- not the caller's
        # bytes -- raises here too (pydantic-core wraps both malformed JSON
        # *and* schema violations in `ValidationError` for every model this
        # codebase passes through this function, confirmed for
        # `SemanticGroupingPolicyV2` and `ChunkResponseEnvelopeV2`), and a
        # bare `except Exception` swallowed an injected `AssertionError`
        # from a broken model exactly as if it were an ordinary malformed
        # document. `ValidationError` alone covers every legitimate
        # external failure shape observed; anything else escapes raw.
        raise OperationalIngressError(
            f"{INGRESS_DOCUMENT_INVALID_REASON_V2}_{field_name}"
        ) from None


# ---------------------------------------------------------------------------
# `#200-G4` additions: coverage extended beyond the nine scalars and the two
# single-file documents. Each function below reuses the pattern above --
# validate/read pre-seal, translate every foreign failure shape into
# `OperationalIngressError`, never echo the offending value or path -- for a
# source `#277` round 2 proved was still open.
# ---------------------------------------------------------------------------


def read_offline_response_document_v2(
    responses_root: str | os.PathLike[str],
    chunk_id: str,
    *,
    model: type[Any],
    field_name: str = "responses",
) -> Any | None:
    """Read one prepared offline response file, or ``None`` if unanswered.

    This is the mandatory RED witness this slice exists to close: the
    predecessor script's ``_offline_transport_v2`` built exactly this path
    and handed its content straight to ``model_validate_json`` with no
    surrounding translation, so a non-UTF-8 byte or a malformed JSON document
    dropped in the responses directory produced an unhandled
    ``UnicodeDecodeError`` / ``pydantic.ValidationError`` traceback that
    printed the subject's temp-directory path to stderr.

    Every file inside ``--responses`` is caller-controlled exactly as much as
    the directory flag itself -- an operator (or a compromised/misconfigured
    upstream step) controls what lands in that directory -- so each entry
    gets the same pre-seal treatment as a single-file document, applied
    lazily, one entry at a time, only for chunk ids the run actually
    requests. Eagerly validating every file the directory happens to contain
    would do unbounded work for entries no run will ever read; nothing about
    the leak this function closes requires that.

    ``chunk_id`` is used only to build a path component. It is **never**
    included in the reason code, an exception message, or any other text
    that could reach stderr: unlike the nine scalar flags, a chunk id can be
    *derived* from caller-supplied diff content, so it carries the same
    provenance as the file content itself.

    Absence is not a refusal: a response file that has not been produced yet
    is an ordinary, expected state for a chunk still awaiting an answer, and
    callers of this function must be able to tell "not yet answered" (``None``)
    apart from "answered, but unusable" (``OperationalIngressError``).
    """
    root = validate_existing_directory_v2(responses_root)
    response_path = root / f"{chunk_id}.json"

    # Defence in depth against a chunk id built from caller-influenced text
    # that resolves outside `responses_root` (e.g. containing `..` or `/`).
    # `chunk_id` is expected to already be constrained to a safe identifier
    # shape upstream, but this function must not assume its caller got that
    # right -- it is, itself, a boundary.
    try:
        escapes = response_path.resolve().parent != root.resolve()
    except OSError:
        escapes = True
    if escapes:
        raise OperationalIngressError(INGRESS_RESPONSE_ESCAPES_DIRECTORY_REASON_V2)

    if not response_path.is_file():
        return None

    return validate_caller_document_v2(response_path, model=model, field_name=field_name)


#: A real fd is never 0, 1, or 2: those three are the process's inherited
#: stdio streams, always already open, and never the freshly created pipe end
#: this control channel actually uses. Rejecting them here -- before any
#: syscall ever consults the value -- is what prevents a caller-supplied `0`
#: from ever reaching a blocking read on stdin.
_MIN_CONTROL_FD_V2 = 3
#: The largest value CPython's fd-consuming C-level calls (`os.fstat`,
#: `os.read`, `os.set_inheritable`, `subprocess`'s `pass_fds`, ...) accept
#: without raising `OverflowError`. Bounding here converts what would
#: otherwise be a raw, unhandled `OverflowError` at the first such call into
#: an ordinary typed refusal.
_MAX_CONTROL_FD_V2 = 2**31 - 1


def resolve_inner_control_fd_v2(raw_value: str | None) -> int | None:
    """Validate the ``AGENT_REVIEW_INNER_CONTROL_FD_V2`` environment value.

    Environment variables read at the process boundary are caller-controlled
    material exactly like an argv flag or a file path -- a caller (or a CI
    workflow file under caller control) sets the process environment. Two
    failure shapes were open here at `#277`'s STOP:

    * a value out of the platform's representable file-descriptor range
      (e.g. ``AGENT_REVIEW_INNER_CONTROL_FD_V2=99999999999999999999``) raised
      a raw ``OverflowError`` the first time anything tried to use it as a
      real fd;
    * a value of exactly ``0`` was accepted as-is and used to read the
      control document from what is actually the process's own stdin, which
      is not the pipe the real channel uses and is not closed -- so the read
      blocked forever.

    Returns ``None`` when the variable is absent or empty (there is no
    channel; this process is an ordinary outer bootstrap). Raises
    ``OperationalIngressError`` for anything present but unusable, rather
    than silently substituting a default: a well-formed-looking but wrong fd
    value is exactly the caller mistake ingress exists to catch, not paper
    over.
    """
    if raw_value is None or raw_value == "":
        return None

    try:
        fd = int(raw_value)
    except (TypeError, ValueError):
        raise OperationalIngressError(INGRESS_CONTROL_FD_INVALID_REASON_V2) from None

    if not (_MIN_CONTROL_FD_V2 <= fd <= _MAX_CONTROL_FD_V2):
        raise OperationalIngressError(INGRESS_CONTROL_FD_INVALID_REASON_V2)

    return fd


class NoEchoArgumentParserV2:
    """Mixin overriding :meth:`argparse.ArgumentParser.error` to not echo argv.

    ``argparse``'s own ``error()`` writes its ``message`` argument to stderr,
    and for several of its own error classes -- "unrecognized arguments"
    foremost among them -- that message embeds the caller's own argv tokens
    verbatim. That is exactly the kind of caller material this module exists
    to keep off stderr: an operator who fat-fingers a flag and has a token or
    a local path in the extra argument gets it printed back at them by
    argparse itself, *before* any of this module's own validation ever runs,
    contradicting this module's own no-echo rule.

    Mix this ahead of ``argparse.ArgumentParser`` in a subclass's bases. The
    override discards ``message`` entirely and raises
    ``OperationalIngressError`` instead of calling ``self.exit()`` directly,
    so a usage error joins the same structurally-caught refusal family as
    every other ingress failure rather than being a second, differently
    shaped kind of process exit.
    """

    def error(self, message: str) -> None:  # noqa: ARG002 -- message is discarded on purpose
        raise OperationalIngressError(INGRESS_USAGE_ERROR_REASON_V2)
