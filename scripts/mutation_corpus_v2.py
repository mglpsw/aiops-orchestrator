#!/usr/bin/env python3
"""A generic mutation-discrimination instrument, and the reasons it distrusts itself.

WHAT THIS IS

A mutation corpus answers one question per entry: *does the test that claims to
prove property P actually fail when P is removed?* A test that stays green under
that removal proves nothing about P, however green it is.

This module is the instrument, not a corpus. It carries no mutations of its own
and imports nothing from `app.agent_review` -- the subject files, the mutated
bytes and the oracle are all supplied by the caller.

WHY IT IS SHAPED THIS WAY

The concept is not new here: `tests/agent_review/test_profile_loader_v2_mutation
_discrimination.py` already establishes, for one subject, that a mutation test
must prove *the patch actually took effect* before believing its result. This
module generalises that obligation to a subprocess/source-rewriting instrument
and adds the classification an in-process monkeypatch does not need.

THE PROPERTY

    A mutation is KILLED **iff** the declared oracle passed against the pristine
    subject, then failed behaviourally against the mutated subject, in a test
    the mutation itself nominated, having demonstrably loaded the mutated bytes.

The first clause is the one that took two independent review lanes to find. An
earlier version required only that *a* behavioural failure occurred in a run
that had imported the subject -- which cannot tell "the oracle failed BECAUSE of
the mutation" from "the oracle was already failing". Every pre-existing or
environment-class red in a declared nodeid was silently converted into a kill.
Nothing ran the oracle against the unmutated subject at all.

Everything that is not that is a different outcome with a different name. In
particular a generic non-zero exit status is NOT a kill, and this is measured
rather than assumed: on the installed pytest, a behavioural assertion failure
and a fixture/setup error BOTH exit 1, so the exit status alone cannot separate
"the oracle discriminated the mutation" from "the run fell over". The JUnit
report can -- `<failure>` versus `<error>` -- so that is what is consulted.

WHAT AN INSTRUMENT LIKE THIS GETS WRONG

Each guard below exists because the failure mode was reproduced first, in this
repository, on this interpreter:

* **An oracle that was already red.** Nothing in a mutated run distinguishes a
  test failing because the guard is gone from one that was failing anyway. The
  oracle is therefore run against the pristine subject first, and an entry whose
  oracle is not green there is refused rather than scored.

* **Stale bytecode.** Rewriting a source file with the *same size* inside one
  mtime tick leaves CPython's `(mtime, size)` invalidation satisfied, so a child
  process imports the OLD code while the file on disk holds the new bytes -- a
  mutation reported as surviving that in fact never ran. Measured directly.
  `PYTHONDONTWRITEBYTECODE=1` **does not fix this**: it suppresses *writing* a
  cache, not *reading* one that already exists. Also measured. The remedy that
  works is to give the run a cache location that is provably empty, and to have
  the child verify what it actually imported.

* **A selector that matches nothing.** pytest exits 5. Scoring any non-zero exit
  as a kill therefore lets a mutation be declared dead by a test that does not
  exist.

* **A selector pytest cannot parse, or a nodeid that is not there.** Exit 4, with
  zero tests executed.

* **A collection or import error.** Exit 2, again with the oracle never run.

* **A mutation that did not apply.** If the expected bytes are absent the edit is
  a no-op, and the run measures the unmutated subject while reporting on the
  mutant.

* **A restore that is not byte-identical.** Every later entry in the run is then
  measuring an unknown subject.

WHAT THE ENVIRONMENT HAS TO BE CONFINED AGAINST

pytest searches *upward* from the tree for `pytest.ini`/`pyproject.toml` and
then loads every `conftest.py` between that rootdir and the tree. Reproduced: a
conftest outside the tree turned an inert, comment-only mutation into a reported
kill, and re-prefixed the recorded nodeids without saying so. `--rootdir` and
`--confcutdir` pin both to the tree. The production path happened to be safe
only because a materialised repository carries its own `pytest.ini`; every
direct caller of `run_selection` was not.

WHAT THIS INSTRUMENT STILL DOES NOT PROVE

The digest check proves the declared subject file was imported by *someone* in
the child process, not that the declared oracle exercised it -- a `conftest.py`
importing the subject satisfies it. What closes the false-kill route is the
baseline requirement, not the digest: if the oracle never touches the mutated
code, mutating it cannot make a green oracle fail. The digest remains useful
evidence of identity and nothing more, and is documented as that rather than as
proof of coverage.

Bytecode immunity is not universal either. `zipimport` ignores
`sys.pycache_prefix`, so a stale `.pyc` inside a zip can execute under a fresh
prefix. Such a module has a `__file__` ending in `.pyc`, so the probe does not
match it and the run degrades to a refusal rather than a false kill.

It does not prove the oracle is *sufficient* -- only that it discriminated this
mutation. It does not prove the mutation is *faithful* to a defect anyone would
actually write; an unfaithful mutation can be killed by a test that would not
catch the real thing. And a corpus of mutations chosen by the same author as the
code carries that author's blind spots. Those are properties of the corpus and
of review, not of this file, and no green run here establishes any of them.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directory names never copied into a materialisation. `.git` and `.venv` are
#: excluded for size; the cache directories are excluded because a materialised
#: tree must start with no bytecode at all -- that is the whole point of it.
_MATERIALISATION_EXCLUDES = ("__pycache__", "*.pyc", ".pytest_cache")
_EXCLUDED_TOP_LEVEL = frozenset({".git", ".venv", ".pytest_cache", "__pycache__"})


class MutationOutcome(enum.Enum):
    """The complete set of things a mutation run can conclude.

    `KILLED` is deliberately the narrowest member: it is the only one that
    asserts anything about the subject's behaviour, and it is reachable only
    when every other class has been ruled out by observation.
    """

    SURVIVED = "survived"
    KILLED = "killed"
    INVALID_SELECTOR = "invalid_selector"
    MUTATION_NOT_APPLIED = "mutation_not_applied"
    COLLECTION_FAILURE = "collection_failure"
    INFRA_FAILURE = "infra_failure"
    RESTORE_FAILURE = "restore_failure"
    # Added after independent review reproduced a KILLED for each of these.
    ORACLE_NOT_GREEN_AT_BASE = "oracle_not_green_at_base"
    ORACLE_SKIPPED = "oracle_skipped"
    SUBJECT_NOT_EXERCISED = "subject_not_exercised"
    AMBIGUOUS_SUBJECT = "ambiguous_subject"


class MutationHarnessError(AssertionError):
    """The instrument could not stand behind its own result.

    Raised instead of returning a verdict, because a harness that reports a
    number it cannot justify is worse than one that reports nothing.
    """


@dataclasses.dataclass(frozen=True)
class SelectionResult:
    """What one pytest invocation actually did, read from structured output.

    `executed_nodeids` comes from the JUnit report rather than from stdout: the
    question "did the intended test run" must be answered by the run itself, and
    prose is not a record.
    """

    exit_status: int
    executed_nodeids: tuple[str, ...]
    behavioural_failures: tuple[str, ...]
    infrastructure_errors: tuple[str, ...]
    imported_subject_digests: dict[str, str]
    stale_bytecode_detected: tuple[str, ...]
    #: Tests pytest reported as `<skipped>`. They are NOT in `executed_nodeids`:
    #: a skipped oracle did not run, and reporting it as a survivor states
    #: something about the subject that was never observed.
    skipped_nodeids: tuple[str, ...] = ()
    #: Subjects whose resident code objects disagree with a fresh compile of the
    #: bytes on disk. This replaces an `(mtime, size)` header comparison that was
    #: measured to be unreachable: the per-run cache prefix means `__cached__`
    #: never exists, and the fixture that does reproduce the defect makes
    #: `(mtime, size)` agree on purpose.
    executed_code_mismatch: tuple[str, ...] = ()
    #: Subjects matched by more than one resident module. Ambiguity is refused,
    #: not resolved by preference.
    ambiguous_subjects: tuple[str, ...] = ()
    #: What the child actually resolved as its configuration, so rootdir
    #: hoisting is visible in the record instead of silently changing nodeids.
    rootdir: str = ""
    configfile: str = ""

    @property
    def selected_count(self) -> int:
        return len(self.executed_nodeids)


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One edit, and the oracle that is supposed to notice it.

    `occurrences` is declared rather than discovered: an edit that matches a
    different number of sites than the author believed is measuring something
    other than what it claims, and that is `MUTATION_NOT_APPLIED`, not a kill.
    """

    name: str
    relative_path: str
    old: str
    new: str
    occurrences: int
    nodeids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class MutationResult:
    name: str
    outcome: MutationOutcome
    detail: str
    selection: SelectionResult | None = None


# --------------------------------------------------------------------------
# execution identity
# --------------------------------------------------------------------------

_PROBE_PLUGIN = '''
"""Injected into the child so the run reports what it actually EXECUTED.

Written by the harness, never checked in as a fixture: it has to describe the
subjects of *this* run.

Two things here were rebuilt after independent review reproduced a false kill
against the previous version:

* Matching was `filename.endswith(rel)`, which has no path-separator boundary.
  `test_subject.py` ends with `subject.py`, so the harness's own naming
  convention was a collision, and whichever module `sys.modules` happened to
  hold first won. Matching is now by resolved path against the tree, and every
  match is collected rather than the first one winning.

* Staleness was an `(mtime, size)` header comparison, which is unreachable here
  (the per-run cache prefix means `__cached__` never exists) and blind anyway to
  the one fixture that reproduces the defect, which makes `(mtime, size)` agree
  on purpose. It is replaced by comparing the code objects actually resident in
  the module against a fresh compile of the bytes on disk.
"""
import hashlib, json, os, sys, types
from pathlib import Path

_SUBJECTS = json.loads(os.environ["AR_MUTATION_SUBJECTS"])
_OUT = os.environ["AR_MUTATION_PROBE_OUT"]
_TREE = Path(os.environ["AR_MUTATION_TREE"])


def _resident_code(module, path):
    """`co_code` of every function/class body defined in this module, by name."""
    out = {}
    for name, value in vars(module).items():
        code = getattr(value, "__code__", None)
        if code is None and isinstance(value, type):
            continue
        if code is None:
            continue
        if code.co_filename != str(path):
            continue
        out[name] = hashlib.sha256(code.co_code).hexdigest()
    return out


def _fresh_code(path):
    """The same mapping, compiled from the bytes on disk right now."""
    try:
        source = path.read_bytes()
    except OSError:
        return None
    try:
        module = types.ModuleType("_ar_fresh")
        module.__dict__["__file__"] = str(path)
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    except Exception:
        return None
    return _resident_code(module, path)


def pytest_sessionfinish(session, exitstatus):
    digests, mismatched, ambiguous = {}, [], []
    for rel in _SUBJECTS:
        target = (_TREE / rel).resolve()
        matches = []
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename:
                continue
            try:
                if Path(filename).resolve() != target:
                    continue
            except OSError:
                continue
            matches.append(module)
        if not matches:
            continue
        if len(matches) > 1:
            ambiguous.append(rel)
        module = matches[0]
        try:
            digests[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            continue
        resident = _resident_code(module, target)
        fresh = _fresh_code(target)
        # Only a positive disagreement counts. If the source cannot be compiled
        # in isolation (imports, side effects) `fresh` is None and this claims
        # nothing, rather than inventing a mismatch it did not observe.
        if resident and fresh is not None and fresh and resident != fresh:
            mismatched.append(rel)

    config = getattr(session, "config", None)
    Path(_OUT).write_text(json.dumps({
        "digests": digests,
        "stale": [],
        "code_mismatch": sorted(set(mismatched)),
        "ambiguous": sorted(set(ambiguous)),
        "rootdir": str(getattr(config, "rootpath", "")) if config else "",
        "configfile": str(getattr(config, "inipath", "") or "") if config else "",
    }))
'''


def _materialise(destination: Path) -> Path:
    """Copy the tree the corpus needs into a directory that has never held bytecode.

    A materialisation rather than an in-place rewrite, for two independent
    reasons. It removes the stale-cache condition by construction -- there is no
    prior `__pycache__` to serve -- and it means an interrupted run cannot leave
    a developer holding a silently modified checkout.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for entry in REPO_ROOT.iterdir():
        if entry.name in _EXCLUDED_TOP_LEVEL:
            continue
        target = destination / entry.name
        if entry.is_dir():
            shutil.copytree(
                entry,
                target,
                symlinks=True,
                ignore=shutil.ignore_patterns(*_MATERIALISATION_EXCLUDES),
            )
        else:
            shutil.copy2(entry, target)
    leftover = [str(p) for p in destination.rglob("*.pyc")]
    if leftover:
        raise MutationHarnessError(f"materialisation is not bytecode-free: {leftover[:3]}")
    return destination


def run_selection(
    nodeids: Sequence[str],
    *,
    tree: Path,
    subjects: Sequence[str],
    tmpdir: Path,
) -> SelectionResult:
    """Run exactly `nodeids` against `tree` and report what happened, structurally.

    `-p no:cacheprovider` and a per-run `PYTHONPYCACHEPREFIX` are what keep one
    entry's state out of the next one's result.
    """
    if not nodeids:
        raise MutationHarnessError("refusing to run an empty selection")
    run_dir = Path(tempfile.mkdtemp(prefix="sel", dir=tmpdir))
    junit = run_dir / "j.xml"
    probe_out = run_dir / "probe.json"
    plugin = run_dir / "ar_mutation_probe.py"
    plugin.write_text(_PROBE_PLUGIN)
    pycache = run_dir / "pyc"
    pycache.mkdir()

    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "TMPDIR": str(tmpdir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(pycache),
        "PYTHONPATH": str(run_dir),
        "AR_MUTATION_TREE": str(Path(tree).resolve()),
        "AR_MUTATION_SUBJECTS": json.dumps(list(subjects)),
        "AR_MUTATION_PROBE_OUT": str(probe_out),
    }
    completed = subprocess.run(  # noqa: S603 -- fixed interpreter, no shell
        [
            sys.executable, "-m", "pytest",
            *nodeids,
            "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
            "-p", "ar_mutation_probe",
            # Without these the child walks UP from `tree` looking for
            # pytest.ini/pyproject.toml and loads every conftest.py between the
            # hoisted rootdir and the tree. Reproduced: a conftest ABOVE the
            # tree injecting a failure turned an inert, comment-only mutation
            # into a reported kill, and silently re-prefixed the nodeids.
            f"--rootdir={Path(tree).resolve()}",
            f"--confcutdir={Path(tree).resolve()}",
            f"--junit-xml={junit}",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        env=environment,
    )

    executed: list[str] = []
    failures: list[str] = []
    errors: list[str] = []
    skipped: list[str] = []
    if junit.exists():
        root = ET.parse(junit).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        for case in [] if suite is None else suite.iter("testcase"):
            nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
            if case.find("failure") is not None:
                failures.append(nodeid)
            if case.find("error") is not None:
                errors.append(nodeid)
            if case.find("skipped") is not None:
                skipped.append(nodeid)
                continue
            # One test that fails and then errors in teardown emits TWO
            # <testcase> records, so counting records counts that test twice.
            if nodeid not in executed:
                executed.append(nodeid)

    probe = json.loads(probe_out.read_text()) if probe_out.exists() else {}
    return SelectionResult(
        exit_status=completed.returncode,
        executed_nodeids=tuple(executed),
        behavioural_failures=tuple(dict.fromkeys(failures)),
        infrastructure_errors=tuple(dict.fromkeys(errors)),
        imported_subject_digests=dict(probe.get("digests", {})),
        stale_bytecode_detected=tuple(probe.get("stale", ())),
        skipped_nodeids=tuple(dict.fromkeys(skipped)),
        executed_code_mismatch=tuple(probe.get("code_mismatch", ())),
        ambiguous_subjects=tuple(probe.get("ambiguous", ())),
        rootdir=str(probe.get("rootdir", "")),
        configfile=str(probe.get("configfile", "")),
    )


def junit_key(nodeid: str) -> str:
    """A declared pytest nodeid in the form the JUnit report uses.

    pytest writes `classname="pkg.module" name="test_x"`, not the nodeid it was
    given. Without this the harness can never compare what it ASKED to run
    against what ran -- which is how a file-level selector let an unrelated,
    already-broken test in the same file score the kill.
    """
    path, _, name = nodeid.partition("::")
    module = path[:-3] if path.endswith(".py") else path
    module = module.replace("/", ".").replace(os.sep, ".")
    return f"{module}::{name}" if name else module


def _matches_declared(observed: str, declared: Sequence[str]) -> bool:
    """Is `observed` (a JUnit `classname::name`) covered by a declared selector?"""
    for nodeid in declared:
        key = junit_key(nodeid)
        if "::" in key:
            if observed == key:
                return True
        elif observed.split("::", 1)[0] == key:
            return True
    return False


def classify(
    selection: SelectionResult,
    *,
    expected_subject_digests: dict[str, str],
    declared_nodeids: Sequence[str] = (),
) -> tuple[MutationOutcome, str]:
    """Turn one observed run into exactly one outcome.

    Pure, so it can be exercised without running pytest at all, and ordered so
    that every way of *not* having measured the subject is ruled out before a
    kill can be returned. The ordering is load-bearing, not stylistic: moving
    the identity checks below the KILLED return was measured to produce a kill
    for a run that executed the wrong bytes, with the whole suite still green.
    """
    if selection.ambiguous_subjects:
        return (
            MutationOutcome.AMBIGUOUS_SUBJECT,
            f"more than one resident module claims {list(selection.ambiguous_subjects)}",
        )
    if selection.executed_code_mismatch:
        return (
            MutationOutcome.INFRA_FAILURE,
            f"executed code disagrees with the source on disk for "
            f"{list(selection.executed_code_mismatch)}",
        )
    if selection.stale_bytecode_detected:
        return (
            MutationOutcome.INFRA_FAILURE,
            f"child imported stale bytecode for {list(selection.stale_bytecode_detected)}",
        )
    # Structured evidence before exit status, and this ordering was earned: a
    # named nodeid inside a module that fails to import exits **4**, not 2, so
    # classifying on the status alone called an import error a bad selector.
    # The report had recorded the error all along.
    if selection.infrastructure_errors and selection.selected_count and not any(
        node in selection.behavioural_failures for node in selection.executed_nodeids
    ) and selection.exit_status in (2, 4):
        return (
            MutationOutcome.COLLECTION_FAILURE,
            f"collection or import error (exit {selection.exit_status}): "
            f"{list(selection.infrastructure_errors)}",
        )
    if selection.exit_status == 2:
        return (MutationOutcome.COLLECTION_FAILURE, "pytest exit 2: collection or import error")
    if selection.exit_status in (4, 5):
        return (
            MutationOutcome.INVALID_SELECTOR,
            f"pytest exit {selection.exit_status}: no intended test was executed",
        )
    if selection.selected_count == 0:
        if selection.skipped_nodeids:
            return (
                MutationOutcome.ORACLE_SKIPPED,
                f"every selected test was skipped: {list(selection.skipped_nodeids)}",
            )
        return (MutationOutcome.INVALID_SELECTOR, "no test executed")

    # The declared oracle must be the thing that ran. A skipped oracle has not
    # observed the subject at all, so reporting it as a survivor would state
    # something about the subject that was never measured.
    if declared_nodeids:
        for observed in selection.skipped_nodeids:
            if _matches_declared(observed, declared_nodeids):
                return (
                    MutationOutcome.ORACLE_SKIPPED,
                    f"the declared oracle was skipped: {observed}",
                )
        if not any(
            _matches_declared(observed, declared_nodeids)
            for observed in selection.executed_nodeids
        ):
            return (
                MutationOutcome.INVALID_SELECTOR,
                f"none of the declared nodeids ran; executed "
                f"{list(selection.executed_nodeids)}",
            )

    for relative_path, expected in expected_subject_digests.items():
        actual = selection.imported_subject_digests.get(relative_path)
        if actual is None:
            return (
                MutationOutcome.SUBJECT_NOT_EXERCISED,
                f"the run never imported the mutated subject {relative_path}",
            )
        if actual != expected:
            return (
                MutationOutcome.INFRA_FAILURE,
                f"{relative_path} executed as {actual[:12]}, expected {expected[:12]}",
            )

    if selection.behavioural_failures:
        # A failure somewhere in the run is not a kill. It has to be a failure of
        # a test this mutation nominated as its oracle, or the mutation is being
        # credited with damage it may not have caused.
        attributable = [
            observed for observed in selection.behavioural_failures
            if not declared_nodeids or _matches_declared(observed, declared_nodeids)
        ]
        if not attributable:
            return (
                MutationOutcome.INFRA_FAILURE,
                f"behavioural failures, but none in the declared oracle: "
                f"{list(selection.behavioural_failures)}",
            )
        return (MutationOutcome.KILLED, f"behavioural failure in {attributable}")
    if selection.infrastructure_errors:
        return (
            MutationOutcome.INFRA_FAILURE,
            f"only infrastructure errors, no behavioural failure: "
            f"{list(selection.infrastructure_errors)}",
        )
    if selection.exit_status == 0:
        return (MutationOutcome.SURVIVED, "the oracle passed against the mutated subject")
    return (
        MutationOutcome.INFRA_FAILURE,
        f"pytest exit {selection.exit_status} with no behavioural failure recorded",
    )


def _apply(tree: Path, mutation: Mutation) -> str:
    path = tree / mutation.relative_path
    text = path.read_text()
    found = text.count(mutation.old)
    if found != mutation.occurrences:
        raise _NotApplied(f"expected {mutation.occurrences} site(s), found {found}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(text.replace(mutation.old, mutation.new))
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if after == before:
        raise _NotApplied("the edit did not change the file")
    return after


class _NotApplied(Exception):
    """Internal: the edit could not be made as declared."""


def prove_oracle_green_at_base(
    mutation: Mutation, *, tree: Path, tmpdir: Path
) -> tuple[MutationOutcome, str] | None:
    """Run the declared oracle against the PRISTINE subject. Returns a refusal, or None.

    This is the check whose absence made every other guard beside the point.
    Without it `classify` cannot tell "the oracle failed BECAUSE of the mutation"
    from "the oracle was already failing", so any pre-existing or
    environment-class red in a declared nodeid was silently converted into a
    kill -- reproduced, with an oracle that never touched the mutated guard.

    It also discharges the obligation to prove, before executing anything, that
    the selector collects and selects a non-zero number of tests.
    """
    baseline = run_selection(
        mutation.nodeids, tree=tree, subjects=[mutation.relative_path], tmpdir=tmpdir
    )
    # Structured evidence before exit status, for the same reason `classify`
    # does it: a named nodeid inside a module that fails to import exits 4, not
    # 2, while recording the error all along. Reading the status first called
    # that a bad selector and sent the corpus author hunting for a typo.
    if baseline.exit_status == 2 or (
        baseline.infrastructure_errors and baseline.exit_status in (2, 4)
    ):
        return (
            MutationOutcome.COLLECTION_FAILURE,
            f"the oracle does not collect against the pristine subject: "
            f"{list(baseline.infrastructure_errors)}",
        )
    if baseline.exit_status in (4, 5) or baseline.selected_count == 0:
        if baseline.skipped_nodeids:
            return (
                MutationOutcome.ORACLE_SKIPPED,
                f"the oracle is skipped against the pristine subject: "
                f"{list(baseline.skipped_nodeids)}",
            )
        return (
            MutationOutcome.INVALID_SELECTOR,
            f"the selector runs no test against the pristine subject "
            f"(pytest exit {baseline.exit_status})",
        )
    for observed in baseline.skipped_nodeids:
        if _matches_declared(observed, mutation.nodeids):
            return (
                MutationOutcome.ORACLE_SKIPPED,
                f"the declared oracle is skipped against the pristine subject: {observed}",
            )
    if baseline.behavioural_failures or baseline.infrastructure_errors:
        return (
            MutationOutcome.ORACLE_NOT_GREEN_AT_BASE,
            f"the oracle already fails without the mutation: "
            f"failures={list(baseline.behavioural_failures)} "
            f"errors={list(baseline.infrastructure_errors)}",
        )
    if baseline.exit_status != 0:
        return (
            MutationOutcome.INFRA_FAILURE,
            f"the pristine run exited {baseline.exit_status} with nothing recorded",
        )
    return None


def run_mutation(mutation: Mutation, *, tree: Path, tmpdir: Path) -> MutationResult:
    """Prove the oracle green at base, apply one mutation, measure, restore, verify."""
    path = tree / mutation.relative_path
    pristine = path.read_bytes()

    refusal = prove_oracle_green_at_base(mutation, tree=tree, tmpdir=tmpdir)
    if refusal is not None:
        return MutationResult(mutation.name, refusal[0], refusal[1])

    if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(pristine).hexdigest():
        return MutationResult(
            mutation.name, MutationOutcome.INFRA_FAILURE,
            "the baseline run modified the subject",
        )

    try:
        mutated_digest = _apply(tree, mutation)
    except _NotApplied as exc:
        return MutationResult(mutation.name, MutationOutcome.MUTATION_NOT_APPLIED, str(exc))

    try:
        selection = run_selection(
            mutation.nodeids,
            tree=tree,
            subjects=[mutation.relative_path],
            tmpdir=tmpdir,
        )
        outcome, detail = classify(
            selection,
            expected_subject_digests={mutation.relative_path: mutated_digest},
            declared_nodeids=mutation.nodeids,
        )
    finally:
        path.write_bytes(pristine)

    if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(pristine).hexdigest():
        return MutationResult(mutation.name, MutationOutcome.RESTORE_FAILURE, "restore not byte-identical")
    return MutationResult(mutation.name, outcome, detail, selection)


def run_corpus(mutations: Iterable[Mutation], *, tmpdir: Path | None = None) -> list[MutationResult]:
    """Run every mutation against one fresh materialisation."""
    with tempfile.TemporaryDirectory(prefix="armut") as workspace:
        root = Path(tmpdir) if tmpdir else Path(workspace)
        tree = _materialise(root / "tree")
        return [run_mutation(m, tree=tree, tmpdir=root) for m in mutations]


def main(argv: Sequence[str] | None = None) -> int:
    """Run a corpus declared in a JSON file. Non-zero unless every entry was KILLED."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(f"usage: {Path(__file__).name} <corpus.json>", file=sys.stderr)
        return 2
    declared = json.loads(Path(argv[0]).read_text())
    mutations = [
        Mutation(
            name=item["name"],
            relative_path=item["path"],
            old=item["old"],
            new=item["new"],
            occurrences=int(item.get("occurrences", 1)),
            nodeids=tuple(item["nodeids"]),
        )
        for item in declared
    ]
    results = run_corpus(mutations)
    unkilled = [r for r in results if r.outcome is not MutationOutcome.KILLED]
    for result in results:
        print(f"{result.name}\n    {result.outcome.value}: {result.detail}")
    print(f"\n{len(results) - len(unkilled)}/{len(results)} killed")
    for result in unkilled:
        print(f"  NOT KILLED: {result.name} ({result.outcome.value})")
    return 1 if unkilled else 0


if __name__ == "__main__":
    raise SystemExit(main())
