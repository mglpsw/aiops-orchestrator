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

    A mutation is KILLED **iff** the intended mutated subject and the intended,
    non-empty oracle both actually executed, and the oracle discriminated the
    mutation behaviourally.

Everything that is not that is a different outcome with a different name. In
particular a generic non-zero exit status is NOT a kill, and this is measured
rather than assumed: on the installed pytest, a behavioural assertion failure
and a fixture/setup error BOTH exit 1, so the exit status alone cannot separate
"the oracle discriminated the mutation" from "the run fell over". The JUnit
report can -- `<failure>` versus `<error>` -- so that is what is consulted.

WHAT AN INSTRUMENT LIKE THIS GETS WRONG

Each guard below exists because the failure mode was reproduced first, in this
repository, on this interpreter:

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

WHAT THIS INSTRUMENT STILL DOES NOT PROVE

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
"""Injected into the child so the run reports what it actually imported.

Written by the harness, never checked in as a fixture: it has to describe the
subjects of *this* run.
"""
import hashlib, json, os, struct, sys
from pathlib import Path

_SUBJECTS = json.loads(os.environ["AR_MUTATION_SUBJECTS"])
_OUT = os.environ["AR_MUTATION_PROBE_OUT"]


def _pyc_is_stale(source: Path, cached: str | None) -> bool:
    """Does a cached bytecode file claim to describe bytes the source no longer has?

    CPython validates `(mtime, size)`. Both can agree while the content differs,
    which is exactly the reproduced defect; comparing them here reports the
    condition the child is actually subject to rather than assuming it away.
    """
    if not cached or not os.path.exists(cached):
        return False
    try:
        with open(cached, "rb") as fh:
            header = fh.read(16)
        if len(header) < 16:
            return True
        flags, mtime, size = struct.unpack("<III", header[4:16])
        if flags & 0b1:  # hash-based pyc: (mtime, size) carry no meaning
            return False
        st = source.stat()
        return not (mtime == int(st.st_mtime) & 0xFFFFFFFF and size == st.st_size & 0xFFFFFFFF)
    except OSError:
        return True


def pytest_sessionfinish(session, exitstatus):
    digests, stale = {}, []
    for rel in _SUBJECTS:
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename or not filename.endswith(rel.replace("/", os.sep)):
                continue
            path = Path(filename)
            try:
                digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            if _pyc_is_stale(path, getattr(module, "__cached__", None)):
                stale.append(rel)
            break
    Path(_OUT).write_text(json.dumps({"digests": digests, "stale": sorted(set(stale))}))
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
        "AR_MUTATION_SUBJECTS": json.dumps(list(subjects)),
        "AR_MUTATION_PROBE_OUT": str(probe_out),
    }
    completed = subprocess.run(  # noqa: S603 -- fixed interpreter, no shell
        [
            sys.executable, "-m", "pytest",
            *nodeids,
            "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
            "-p", "ar_mutation_probe",
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
    if junit.exists():
        root = ET.parse(junit).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        for case in [] if suite is None else suite.iter("testcase"):
            nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
            executed.append(nodeid)
            if case.find("failure") is not None:
                failures.append(nodeid)
            if case.find("error") is not None:
                errors.append(nodeid)

    probe = json.loads(probe_out.read_text()) if probe_out.exists() else {}
    return SelectionResult(
        exit_status=completed.returncode,
        executed_nodeids=tuple(executed),
        behavioural_failures=tuple(failures),
        infrastructure_errors=tuple(errors),
        imported_subject_digests=dict(probe.get("digests", {})),
        stale_bytecode_detected=tuple(probe.get("stale", ())),
    )


def classify(
    selection: SelectionResult,
    *,
    expected_subject_digests: dict[str, str],
) -> tuple[MutationOutcome, str]:
    """Turn one observed run into exactly one outcome.

    Pure, so it can be exercised without running pytest at all, and ordered so
    that every way of *not* having measured the subject is ruled out before a
    kill can be returned.
    """
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
        return (MutationOutcome.INVALID_SELECTOR, "no test executed")

    for relative_path, expected in expected_subject_digests.items():
        actual = selection.imported_subject_digests.get(relative_path)
        if actual is None:
            return (
                MutationOutcome.INFRA_FAILURE,
                f"the run never imported the mutated subject {relative_path}",
            )
        if actual != expected:
            return (
                MutationOutcome.INFRA_FAILURE,
                f"{relative_path} executed as {actual[:12]}, expected {expected[:12]}",
            )

    if selection.behavioural_failures:
        return (
            MutationOutcome.KILLED,
            f"behavioural failure in {list(selection.behavioural_failures)}",
        )
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


def run_mutation(mutation: Mutation, *, tree: Path, tmpdir: Path) -> MutationResult:
    """Apply one mutation to `tree`, measure, restore, and verify the restore."""
    path = tree / mutation.relative_path
    pristine = path.read_bytes()
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
            selection, expected_subject_digests={mutation.relative_path: mutated_digest}
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
