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
CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2 = "target_pack_conformance_duplicate_target_root"
CONFORMANCE_NON_UNIFORM_SHAPE_REASON_V2 = "target_pack_conformance_non_uniform_check_shape"
CONFORMANCE_NO_COMPARABLE_COHORT_REASON_V2 = "target_pack_conformance_no_comparable_cohort"
CONFORMANCE_DUPLICATE_CASE_ID_REASON_V2 = "target_pack_conformance_duplicate_case_id"
CONFORMANCE_INVALID_CASE_ID_REASON_V2 = "target_pack_conformance_invalid_case_id"
CONFORMANCE_TARGET_ROOT_UNRESOLVABLE_REASON_V2 = "target_pack_conformance_target_root_unresolvable"
CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2 = "target_pack_conformance_single_authored_identity"

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
    # `receipt.target_repo` for this case, or None when no receipt parsed.
    # Used only for the distinct-identity property (see
    # `_distinct_authored_identities_v2`); never for a per-case decision,
    # so the pack still branches on no target's name anywhere.
    authored_identity: str | None = None

    @property
    def observed_reason_codes(self) -> tuple[str, ...]:
        return tuple(
            check.reason_code
            for check in self.validate_report.checks
            if check.status == STATUS_FAIL_V2 and check.reason_code is not None
        )

    @property
    def observable_shape(self) -> tuple[tuple[str, str, str | None], ...]:
        """The full observable behaviour of one case: check names, order,
        statuses and reason codes. This -- not a single boolean -- is what
        must agree across targets for a uniformity claim to mean anything."""

        return tuple(
            (check.name, check.status, check.reason_code) for check in self.validate_report.checks
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


def _authored_identity_v2(resolved_root: Path) -> str | None:
    """The `receipt.target_repo` this installation is bound to, or None if
    no receipt parses.

    Read through `validate`'s own contained loaders, so it inherits the
    same containment and total-parse-boundary guarantees rather than
    opening a second, weaker read path into the target.
    """

    from app.agent_review.target_pack_validate_v2 import authored_target_identity_v2

    return authored_target_identity_v2(target_root=resolved_root)


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

    # Distinct target ROOTS, not merely distinct case ids (PR #235 review
    # round 1, confirmed): the same directory listed twice exercised one
    # target while satisfying a claim that requires two. Compared by
    # resolved path so two spellings of one directory cannot masquerade as
    # two targets either.
    # Resolution itself can fail: `Path.resolve(strict=False)` raises
    # RuntimeError on a symlink cycle (PR #235 review round 3, confirmed),
    # and this ran eagerly before any per-case handling -- so a
    # target-authored matrix produced a traceback instead of the promised
    # reason-coded result. Normalised into a matrix-level refusal.
    resolved_roots: list[Path] = []
    for case in cases:
        try:
            resolved_roots.append(case.target_root.resolve(strict=False))
        except (RuntimeError, OSError):
            return ConformanceReportV2(
                cases=(), reason_codes=(CONFORMANCE_TARGET_ROOT_UNRESOLVABLE_REASON_V2,)
            )

    # EVERY case root must be distinct, not merely "at least two distinct
    # roots overall" (PR #235 review round 3, confirmed -- the third
    # consecutive round to find a gap in this same guarantee). A matrix of
    # {A eligible, A eligible, B ineligible} passed a set-size floor AND
    # produced a two-member cohort, but that cohort compared A with itself,
    # so uniformity across distinct targets was still never tested.
    if len(set(resolved_roots)) != len(resolved_roots):
        return ConformanceReportV2(cases=(), reason_codes=(CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2,))

    # Case ids must be unique, non-empty strings (PR #235 review round 2,
    # confirmed): they are how a consumer attributes a failure or an
    # evidence record back to its matrix entry, so two indistinguishable
    # ids make the report unattributable even when the run itself is fine.
    case_ids = [case.case_id for case in cases]
    if any(not isinstance(cid, str) or not cid.strip() for cid in case_ids):
        return ConformanceReportV2(cases=(), reason_codes=(CONFORMANCE_INVALID_CASE_ID_REASON_V2,))
    if len(set(case_ids)) != len(case_ids):
        return ConformanceReportV2(cases=(), reason_codes=(CONFORMANCE_DUPLICATE_CASE_ID_REASON_V2,))

    results: list[ConformanceCaseResultV2] = []
    unreadable_case_ids: list[str] = []
    # Validate through the SAME resolved snapshot uniqueness was checked
    # against (PR #235 review round 5). Re-deriving from the mutable
    # `case.target_root` let two roots that resolved distinctly at gate time
    # both land on one directory if a symlink was retargeted in between --
    # recreating precisely the self-comparison the uniqueness gate exists to
    # prevent. Same "one snapshot, one decision" invariant as the profile
    # byte-snapshot fix in round 4.
    for case, resolved_root in zip(cases, resolved_roots):
        if not resolved_root.is_dir():
            # An unreadable fixture is a MATRIX failure regardless of the
            # declared expectation (PR #235 review round 1, confirmed):
            # `is_valid=False` happened to satisfy an `ineligible`
            # expectation, so a matrix of nonexistent directories reported
            # success having validated nothing and having exercised no
            # intentional contract violation.
            report = _unreadable_report_v2(resolved_root)
            unreadable_case_ids.append(case.case_id)
            matched = False
            authored_identity = None
        else:
            report = run_validate_v2(target_root=resolved_root)
            expected_valid = case.expectation is ConformanceExpectationV2.ELIGIBLE
            matched = report.is_valid == expected_valid
            authored_identity = _authored_identity_v2(resolved_root)

        results.append(
            ConformanceCaseResultV2(
                case_id=case.case_id,
                expectation=case.expectation,
                validate_report=report,
                matched_expectation=matched,
                authored_identity=authored_identity,
            )
        )

    reason_codes: list[str] = []
    if unreadable_case_ids:
        reason_codes.append(CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2)
    reason_codes.extend(_uniformity_reason_codes_v2(tuple(results)))
    return ConformanceReportV2(cases=tuple(results), reason_codes=tuple(reason_codes))


def _uniformity_reason_codes_v2(results: tuple[ConformanceCaseResultV2, ...]) -> tuple[str, ...]:
    """Enforce the uniformity property this module CLAIMS to prove.

    PR #235 review round 1, confirmed and the most consequential of the
    round: the decision compared only each case's validity boolean and
    never the promised check names, ordering or statuses. Two cases could
    therefore fail for entirely different reasons -- or diverge in shape
    while both matching their expectation -- and still be reported
    conformant. That is precisely the repository-name branch this command
    exists to detect: such a branch changes observable output without
    necessarily changing any final boolean.

    Cases sharing an expectation must share an observable shape. Cases
    with DIFFERENT expectations are legitimately allowed to differ -- one
    is supposed to pass and the other to fail.
    """

    by_expectation: dict[ConformanceExpectationV2, list[ConformanceCaseResultV2]] = {}
    for result in results:
        by_expectation.setdefault(result.expectation, []).append(result)

    for group in by_expectation.values():
        shapes = {case.observable_shape for case in group}
        if len(shapes) > 1:
            return (CONFORMANCE_NON_UNIFORM_SHAPE_REASON_V2,)

    # A cohort of ONE compares nothing (PR #235 review round 2, confirmed
    # -- a gap in round 1's own fix). Grouping by expectation meant the
    # minimal legal matrix, one eligible plus one ineligible target,
    # produced two singleton cohorts and therefore zero comparisons, while
    # still reporting conformance. At least one cohort must contain two
    # distinct targets or the uniformity claim is withheld rather than
    # asserted vacuously.
    comparable = [g for g in by_expectation.values() if len(g) >= MINIMUM_CONFORMANCE_CASES_V2]
    if not comparable:
        return (CONFORMANCE_NO_COMPARABLE_COHORT_REASON_V2,)

    # Property 5: the comparable cohort must contain >= 2 SEMANTICALLY
    # distinct targets, not merely two directories. Two copies of one
    # installation have distinct roots and identical authored identity, so
    # comparing them proves only that the pack is deterministic -- a
    # repository-name branch keyed on any OTHER identity stays invisible.
    #
    # Authored identity is `receipt.target_repo`, the identity the pack
    # actually binds an install to. Deliberately NOT the profile's
    # `identity.repo`: that may legitimately still hold the generic seed
    # placeholder, which is the same value in every freshly initialised
    # target and would therefore make every cohort look identical.
    if not any(_distinct_authored_identities_v2(group) >= MINIMUM_CONFORMANCE_CASES_V2 for group in comparable):
        return (CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2,)
    return ()


def _distinct_authored_identities_v2(group: list[ConformanceCaseResultV2]) -> int:
    """How many distinct `receipt.target_repo` values a cohort contains.

    A case whose receipt could not be parsed contributes no identity --
    it cannot be counted toward a distinctness claim it never established.
    """

    identities = {case.authored_identity for case in group if case.authored_identity is not None}
    return len(identities)
