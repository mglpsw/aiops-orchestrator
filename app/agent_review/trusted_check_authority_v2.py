"""Authority boundary for AgentReview v2 trusted checks (#201-B3, rev. 4).

Pure classification and eligibility logic. Nothing here spawns a process,
reads a checkout, or touches the filesystem -- that is
``isolated_executor_v2``'s job. Keeping this module free of the executor's
imports is deliberate: the executor imports THIS, never the reverse, so the
authority decision can be reasoned about (and tested) without a subprocess
anywhere in sight.

## The rule this module exists to enforce

```text
controls(subject, success_signal)  =>  not authoritative(success_signal)
```

Isolating PR-controlled code does not turn the result produced by that code
into authoritative evidence. ``#201-B2`` proved the isolation is real; an
earlier revision of the ``#201-B3`` plan then tried to rescue pytest's
authority by running each test item in its own process with the checkout's
``conftest.py``/plugins disabled. That reduces blast radius -- it does NOT
close semantic forgery: application or test code can call ``os._exit(0)`` in
EVERY per-item process, and an item inventory derived from the PR's own tree
cannot prove the PR did not simply delete the tests it was supposed to run.

So the defence is not a detector that runs after the fact. It is a CLASS
boundary, refused BEFORE the process is ever created -- the system must
never call trusted something it merely managed to contain.

## The conjunctive trust theorem

A promotable success requires ALL of these simultaneously:

```text
PromotableSuccess = I and P and H and W and C and N and R and V and O
```

``I`` subject identity verified; ``P`` plan/inventory bound to the trusted
base; ``H`` host-owned harness integrity; ``W`` **the workload does not
control the semantic assertion**; ``C`` private, complete, validated result
channel; ``N`` containment proven; ``R`` network/privilege/resource limits
held; ``V`` coverage established by authoritative manifestation; ``O``
product outcome successful. Any premise absent, false, or unknown makes the
whole conjunction false -- ``unknown`` is never optimistically resolved.

``W`` is the premise that is unsatisfiable for pytest no matter how tightly
the process is isolated, and it is why ``ExecutionClassV2.SUBJECT_CODE`` can
never back ``TrustedCheckAuthorityV2.TRUSTED``.

## Who owns what, and why `subject_code` is still allowed to run

Refusing ``TRUSTED`` is not refusing to execute. ``UNTRUSTED_ADVISORY``
execution of subject code remains permitted (under the strong containment
``#201-B3`` builds) -- it simply can never promote, exactly as ``#201-A``'s
``promote_trusted_check_to_required_v2`` already enforces structurally.

Authoritative evidence for subject-code checks (pytest and friends) comes
from deterministic CI bound to the run identity, NOT from this executor --
the project overlay already assigns CI that authority ("CI determinístico
permanece autoridade sobre testes"). Wiring that source is ``#201-C0``, a
later slice; nothing in this module produces or consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated

from pydantic import Field

from app.agent_review.trusted_checks_v2 import TrustedCheckAuthorityV2

# -- authority reason codes ---------------------------------------------------

EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2 = "isolated_executor_subject_code_cannot_be_trusted"
EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2 = "isolated_executor_execution_class_unknown"
EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2 = "isolated_executor_host_owned_config_required"
EXECUTOR_REASON_COVERAGE_AUTHORITY_UNAVAILABLE_V2 = "isolated_executor_coverage_authority_unavailable"

# -- vector states ------------------------------------------------------------

STATE_VERIFIED_V2 = "verified"
STATE_FAILED_V2 = "failed"
STATE_MISMATCH_V2 = "mismatch"
STATE_VIOLATED_V2 = "violated"
STATE_UNAVAILABLE_V2 = "unavailable"
STATE_UNKNOWN_V2 = "unknown"
STATE_COMPLETE_V2 = "complete"
STATE_PARTIAL_V2 = "partial"

WORKLOAD_HOST_OWNED_V2 = "host_owned"
WORKLOAD_SUBJECT_OWNED_V2 = "subject_owned"
WORKLOAD_MIXED_V2 = "mixed"

OUTCOME_SUCCESS_V2 = "success"

ELIGIBILITY_ELIGIBLE_V2 = "eligible"
ELIGIBILITY_INELIGIBLE_V2 = "ineligible"


class ExecutionClassV2(str, Enum):
    """What a command actually IS, semantically -- not what it is called.

    ``DATA_ONLY_HOST_TOOL``: a host-owned binary with host-owned config that
    consumes the checkout as DATA (a linter, a formatter check, a schema
    validator). The checkout cannot steer the tool's own verdict logic.

    ``SUBJECT_CODE``: the checkout's own code executes (pytest, repository
    scripts, PR Python modules, checkout shell). The subject controls its own
    success signal, so that signal is not authoritative -- ever.

    ``UNKNOWN``: the base-owned inventory did not classify this token. This
    is the DEFAULT, and it is refused rather than inferred.
    """

    DATA_ONLY_HOST_TOOL = "data_only_host_tool"
    SUBJECT_CODE = "subject_code"
    UNKNOWN = "unknown"


ExecutionClassValueV2 = Annotated[ExecutionClassV2, Field(strict=False)]


@dataclass(frozen=True)
class ExecutionClassificationV2:
    """The classification decision, with both the declared and the EFFECTIVE
    class preserved. Keeping the declaration visible next to the effective
    result is what makes a downgrade auditable rather than silent."""

    declared_class: ExecutionClassV2
    effective_class: ExecutionClassV2
    # ``None`` iff TRUSTED authority is permissible for this command.
    trusted_refusal_reason: str | None


def classify_command_spec_v2(
    *,
    execution_class: ExecutionClassV2,
    host_owned_config: bool,
    loads_checkout_plugins: bool,
) -> ExecutionClassificationV2:
    """Classify one inventory entry, fail-closed.

    Takes primitives rather than the spec object so this module never has to
    import ``isolated_executor_v2`` (which imports this one).

    Two ways a ``DATA_ONLY_HOST_TOOL`` declaration is overridden, both
    deliberate -- the declaration lives in a host-owned inventory, but a
    declaration is not a proof:

    - ``loads_checkout_plugins``: a tool that loads plugins from the checkout
      executes checkout code by definition, whatever it calls itself;
    - ``host_owned_config`` false: a tool steered by checkout-owned config
      lets the subject choose what "pass" means. ``host_owned_config: true``
      is therefore NECESSARY -- and, on its own, still not sufficient: the
      actually-consumed config bytes and toolchain must additionally
      reproduce the digests the inventory is bound to (that binding lands
      with ``#201-C0``'s provenance sidecar, which is why this module records
      the necessary condition and never treats it as the whole proof).
    """

    if execution_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL:
        if loads_checkout_plugins:
            return ExecutionClassificationV2(
                declared_class=execution_class,
                effective_class=ExecutionClassV2.SUBJECT_CODE,
                trusted_refusal_reason=EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2,
            )
        if not host_owned_config:
            return ExecutionClassificationV2(
                declared_class=execution_class,
                effective_class=ExecutionClassV2.SUBJECT_CODE,
                trusted_refusal_reason=EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2,
            )
        return ExecutionClassificationV2(
            declared_class=execution_class,
            effective_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL,
            trusted_refusal_reason=None,
        )

    if execution_class is ExecutionClassV2.SUBJECT_CODE:
        return ExecutionClassificationV2(
            declared_class=execution_class,
            effective_class=ExecutionClassV2.SUBJECT_CODE,
            trusted_refusal_reason=EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2,
        )

    return ExecutionClassificationV2(
        declared_class=ExecutionClassV2.UNKNOWN,
        effective_class=ExecutionClassV2.UNKNOWN,
        trusted_refusal_reason=EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2,
    )


def trusted_refusal_reason_v2(
    *, classification: ExecutionClassificationV2, authority: TrustedCheckAuthorityV2
) -> str | None:
    """The pre-spawn gate: returns a stable reason code when this command may
    NOT run under ``authority``, or ``None`` when it may.

    ``UNTRUSTED_ADVISORY`` is permitted for every class -- refusing TRUSTED
    is not refusing to execute. The advisory result simply can never promote
    (``promote_trusted_check_to_required_v2`` refuses it structurally)."""

    if authority is not TrustedCheckAuthorityV2.TRUSTED:
        return None
    return classification.trusted_refusal_reason


def coverage_authority_for_class_v2(execution_class: ExecutionClassV2) -> str:
    """Coverage authority answers "do we know what SHOULD have run?".

    For subject code the honest answer is ``unavailable``: any enumeration of
    the expected work would be derived from the PR's own tree, and the PR can
    delete or hide whatever it likes. That is not a gap to be closed with a
    cleverer enumerator -- it is the same ``W`` premise failing again, and
    pretending otherwise is exactly the failure mode this slice exists to
    remove."""

    if execution_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL:
        return STATE_COMPLETE_V2
    return STATE_UNAVAILABLE_V2


def workload_authority_for_class_v2(execution_class: ExecutionClassV2) -> str:
    if execution_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL:
        return WORKLOAD_HOST_OWNED_V2
    if execution_class is ExecutionClassV2.SUBJECT_CODE:
        return WORKLOAD_SUBJECT_OWNED_V2
    return STATE_UNKNOWN_V2


@dataclass(frozen=True)
class ExecutionAssessmentV2:
    """The vectorial internal assessment, computed BEFORE the public
    ``TrustedCheckResultV2`` is constructed.

    Every axis is independent on purpose. A scalar outcome forces a choice
    that should never be made: if a check FAILED and a descendant also
    survived, collapsing both into one value means either the security
    failure hides the regression or the regression hides the security
    failure. Both are recorded here, and ``promotion_eligibility`` is derived
    from the conjunction rather than from any single axis.

    This is a plain frozen dataclass, never part of the hashed contract
    material -- exactly like ``ExecutedCheckV2.diagnostic_reason``."""

    subject_identity: str = STATE_UNKNOWN_V2
    inventory_binding: str = STATE_UNKNOWN_V2
    execution_class: str = ExecutionClassV2.UNKNOWN.value
    workload_authority: str = STATE_UNKNOWN_V2
    harness_integrity: str = STATE_UNKNOWN_V2
    protocol_integrity: str = STATE_UNKNOWN_V2
    containment: str = STATE_UNKNOWN_V2
    resource_boundary: str = STATE_UNKNOWN_V2
    coverage_authority: str = STATE_UNKNOWN_V2
    product_outcome: str = STATE_UNKNOWN_V2
    promotion_eligibility: str = STATE_UNKNOWN_V2
    diagnostic_reasons: tuple[str, ...] = field(default_factory=tuple)


# The premises of the conjunctive theorem that must each read `verified`.
_VERIFIED_PREMISES_V2 = (
    "subject_identity",
    "inventory_binding",
    "harness_integrity",
    "protocol_integrity",
    "containment",
    "resource_boundary",
)


def compute_promotion_eligibility_v2(assessment: ExecutionAssessmentV2) -> str:
    """Derive ``promotion_eligibility`` from the whole vector.

    Returns ``eligible`` only when every premise of the conjunctive theorem
    holds simultaneously; ``ineligible`` otherwise. It never returns
    ``unknown``: an unknown premise is a false conjunct, not a maybe."""

    if assessment.execution_class != ExecutionClassV2.DATA_ONLY_HOST_TOOL.value:
        return ELIGIBILITY_INELIGIBLE_V2
    if assessment.workload_authority != WORKLOAD_HOST_OWNED_V2:
        return ELIGIBILITY_INELIGIBLE_V2
    if any(getattr(assessment, premise) != STATE_VERIFIED_V2 for premise in _VERIFIED_PREMISES_V2):
        return ELIGIBILITY_INELIGIBLE_V2
    if assessment.coverage_authority != STATE_COMPLETE_V2:
        return ELIGIBILITY_INELIGIBLE_V2
    if assessment.product_outcome != OUTCOME_SUCCESS_V2:
        return ELIGIBILITY_INELIGIBLE_V2
    return ELIGIBILITY_ELIGIBLE_V2
