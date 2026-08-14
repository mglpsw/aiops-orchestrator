"""`agent-review target conformance` -- synthetic, offline conformance for
the AgentReview v2 target pack (#203-S2).

## What this proves, precisely

That the ONE generic pack exercises its own contract identically across
several independently-authored targets, and detects violations in each.
Two properties, and only these two:

1. **Uniformity.** Two targets differing only in their authored identity
   produce the same checks, in the same order, with the same statuses. A
   target-name branch anywhere in the engine would break this.
2. **Detection.** A target the matrix declares ineligible must ACTUALLY
   fail validation. Without this direction, "conformance" degenerates into
   "every case passed", which a pack that validates nothing would also
   satisfy.

## What this is NOT

`#203`'s conformance is synthetic and offline: ordinary directories, no
real consumer repository, no network, no Agent Router, no provider, no
GitHub, no secrets, no runner. Real dual-target adoption -- migrating an
existing consumer, installing into a new one, CT104 canaries, and
operational DLP/PHI proof -- is `#204`'s charter (spec §10), and a
passing run here may only ever be reported as
`synthetic_pack_conformance`, never as `dual_target_conformance`.

(Deliberately no consumer repository is named anywhere in this module:
the generic engine must not contain a target name even in prose, which
`test_target_pack_arch_v2.py` enforces mechanically over every string
constant here -- docstrings included.)

Offline-ness is structural, not merely intended: this module imports no
network, subprocess or GitHub client, and its only I/O is
`target_pack_validate_v2`'s own contained reads.

## Why it wraps `validate` rather than re-deriving anything

`run_validate_v2` already owns the per-target decision. Conformance adds
exactly one thing on top: comparing each target's outcome against a
declared expectation, and comparing targets against each other. It
re-implements no check -- the same "the CLI never re-implements a
decision" discipline the spec sets for every subcommand, applied one
layer up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.agent_review.target_pack_validate_v2 import (
    STATUS_FAIL_V2,
    ValidateCheckV2,
    ValidateReportV2,
    run_validate_v2,
)

CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2 = "target_pack_conformance_case_target_unreadable"
CONFORMANCE_TOO_FEW_CASES_REASON_V2 = "target_pack_conformance_too_few_cases"

# A conformance claim proven against a single target is not a conformance
# claim -- "the same pack works everywhere" needs at least two somewheres.
MINIMUM_CONFORMANCE_CASES_V2 = 2


class ConformanceExpectationV2(str, Enum):
    """What the matrix asserts about one synthetic target."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class ConformanceCaseV2:
    case_id: str
    target_root: Path
    expectation: ConformanceExpectationV2


@dataclass(frozen=True)
class ConformanceCaseResultV2:
    case_id: str
    expectation: ConformanceExpectationV2
    validate_report: ValidateReportV2
    matched_expectation: bool

    @property
    def observed_reason_codes(self) -> tuple[str, ...]:
        return tuple(
            check.reason_code
            for check in self.validate_report.checks
            if check.status == STATUS_FAIL_V2 and check.reason_code is not None
        )


@dataclass(frozen=True)
class ConformanceReportV2:
    cases: tuple[ConformanceCaseResultV2, ...]
    reason_codes: tuple[str, ...] = ()

    @property
    def is_conformant(self) -> bool:
        return not self.reason_codes and bool(self.cases) and all(c.matched_expectation for c in self.cases)

    def summary_tuple(self) -> tuple[object, ...]:
        """A fully-ordered, comparable projection -- two runs over the same
        inputs must produce an identical value. Used to assert determinism
        without depending on object identity."""

        return (
            self.reason_codes,
            tuple(
                (
                    case.case_id,
                    case.expectation.value,
                    case.matched_expectation,
                    tuple((c.name, c.status, c.reason_code) for c in case.validate_report.checks),
                )
                for case in self.cases
            ),
        )


def _unreadable_report_v2(target_root: Path) -> ValidateReportV2:
    return ValidateReportV2(
        target_root=str(target_root),
        checks=(
            ValidateCheckV2(
                "target_root", STATUS_FAIL_V2, CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2
            ),
        ),
    )


def run_conformance_v2(*, cases: tuple[ConformanceCaseV2, ...]) -> ConformanceReportV2:
    """Run every case and compare each outcome to its declared expectation.

    Total: a case whose target cannot be read at all becomes a failing
    case result, never an exception, so one unreadable fixture cannot hide
    the outcome of every other case in the matrix.

    Cases are evaluated in the order given and the report preserves that
    order, so the result is deterministic for a deterministic matrix.
    """

    if len(cases) < MINIMUM_CONFORMANCE_CASES_V2:
        return ConformanceReportV2(cases=(), reason_codes=(CONFORMANCE_TOO_FEW_CASES_REASON_V2,))

    results: list[ConformanceCaseResultV2] = []
    for case in cases:
        if not case.target_root.is_dir():
            report = _unreadable_report_v2(case.target_root)
        else:
            report = run_validate_v2(target_root=case.target_root)

        expected_valid = case.expectation is ConformanceExpectationV2.ELIGIBLE
        results.append(
            ConformanceCaseResultV2(
                case_id=case.case_id,
                expectation=case.expectation,
                validate_report=report,
                matched_expectation=report.is_valid == expected_valid,
            )
        )

    return ConformanceReportV2(cases=tuple(results))
