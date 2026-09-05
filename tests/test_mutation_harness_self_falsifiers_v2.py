"""Each guard in the instrument, removed, must turn one of its own tests red.

WHY THIS FILE IS SEPARATE, AND WHY IT DOES NOT USE THE HARNESS

The obvious way to write this is to run the mutation harness against the mutation
harness. That would be circular: a harness broken in exactly the way under test
is the thing being asked whether it noticed. So the verification path here is
plain file edits and a plain `pytest` subprocess, and it touches none of the
harness's own functions.

The mutations are applied to a COPY. The checked-in instrument is never edited,
which a test below asserts by hash rather than by intention.

WHAT A RED HERE MEANS

`test_mutation_harness_v2.py` claims each guard is load-bearing. This file is the
evidence for that claim: remove the guard, and the specific test that names it
must fail. A guard whose removal changes nothing is not a guard, and would leave
the instrument free to report a kill it did not earn.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HARNESS = _REPO_ROOT / "scripts" / "mutation_corpus_v2.py"
_TESTS = _REPO_ROOT / "tests" / "test_mutation_harness_v2.py"

def _mutations() -> list[tuple[str, str, str, str]]:
    """Declared here rather than in a constant table so each entry can carry the
    exact source slice it replaces without fighting line length."""
    return [
        (
            "selector guard: allow an empty selection to run",
            '    if not nodeids:\n        raise MutationHarnessError("refusing to run an empty selection")\n',
            "    if False:\n        raise MutationHarnessError('unreachable')\n",
            "test_an_empty_selection_is_refused_rather_than_run",
        ),
        (
            "no-test exit: treat pytest 4/5 as a behavioural kill",
            '    if selection.exit_status in (4, 5):\n'
            '        return (\n'
            '            MutationOutcome.INVALID_SELECTOR,\n'
            '            f"pytest exit {selection.exit_status}: no intended test was executed",\n'
            '        )\n',
            '    if selection.exit_status in (4, 5):\n'
            '        return (MutationOutcome.KILLED, "scored as a kill")\n',
            "test_every_observation_maps_to_exactly_one_outcome",
        ),
        (
            "collection failure (structured branch): score it as a behavioural kill",
            '        return (\n'
            '            MutationOutcome.COLLECTION_FAILURE,\n'
            '            f"collection or import error (exit {selection.exit_status}): "\n'
            '            f"{list(selection.infrastructure_errors)}",\n'
            '        )\n',
            '        return (MutationOutcome.KILLED, "scored as a kill")\n',
            "test_every_observation_maps_to_exactly_one_outcome",
        ),
        (
            "collection failure (exit-2 fallback): score it as a behavioural kill",
            '    if selection.exit_status == 2:\n'
            '        return (MutationOutcome.COLLECTION_FAILURE, "pytest exit 2: collection or import error")\n',
            '    if selection.exit_status == 2:\n'
            '        return (MutationOutcome.KILLED, "scored as a kill")\n',
            "test_every_observation_maps_to_exactly_one_outcome",
        ),
        (
            "infrastructure error: score it as a behavioural kill",
            '    if selection.infrastructure_errors:\n        return (\n            MutationOutcome.INFRA_FAILURE,\n            f"only infrastructure errors, no behavioural failure: "\n            f"{list(selection.infrastructure_errors)}",\n        )\n',
            '    if selection.infrastructure_errors:\n        return (MutationOutcome.KILLED, "scored as a kill")\n',
            "test_an_infrastructure_error_caused_by_the_mutation_is_not_a_behavioural_kill",
        ),
        (
            "occurrence-count proof: accept an edit landing at undeclared sites",
            '    if found != mutation.occurrences:\n'
            '        raise _NotApplied(f"expected {mutation.occurrences} site(s), found {found}")\n',
            "    if False:\n        raise _NotApplied('unreachable')\n",
            "test_an_edit_matching_more_sites_than_declared_is_refused",
        ),
        (
            "no-op edit proof: accept an edit that left the bytes unchanged",
            '    if after == before:\n        raise _NotApplied("the edit did not change the file")\n',
            "    if False:\n        raise _NotApplied('unreachable')\n",
            "test_an_edit_whose_replacement_is_identical_is_mutation_not_applied",
        ),
        (
            "stale-bytecode remedy: let the child consult the tree's own cache",
            '        "PYTHONPYCACHEPREFIX": str(pycache),\n',
            "",
            "test_the_harness_run_is_immune_to_a_stale_cache_in_the_tree",
        ),
        (
            "execution-identity proof: stop comparing what the child imported",
            "    for relative_path, expected in expected_subject_digests.items():\n",
            "    for relative_path, expected in {}.items():\n",
            "test_every_observation_maps_to_exactly_one_outcome",
        ),
        (
            "baseline proof: stop running the oracle against the pristine subject",
            "    refusal = prove_oracle_green_at_base(mutation, tree=tree, tmpdir=tmpdir)\n",
            "    refusal = None\n",
            "test_an_oracle_red_before_the_mutation_cannot_score_a_kill",
        ),
        (
            "baseline green check: accept an oracle that already fails",
            "    if baseline.behavioural_failures or baseline.infrastructure_errors:\n",
            "    if False:\n",
            "test_an_oracle_red_before_the_mutation_cannot_score_a_kill",
        ),
        (
            "baseline skip check: call a skipped oracle a bad selector",
            "        if baseline.skipped_nodeids:\n",
            "        if False:\n",
            "test_a_skipped_oracle_is_not_reported_as_a_survivor",
        ),
        (
            "rootdir confinement: let the child hoist rootdir out of the tree",
            '            f"--rootdir={Path(tree).resolve()}",\n'
            '            f"--confcutdir={Path(tree).resolve()}",\n',
            "",
            "test_a_conftest_above_the_tree_cannot_manufacture_a_kill",
        ),
        (
            "subject matching: go back to an unanchored endswith",
            "                if Path(filename).resolve() != target:\n",
            "                if not filename.endswith(rel):\n",
            "test_a_file_whose_name_merely_ends_with_the_subject_is_not_the_subject",
        ),
        (
            "oracle attribution: credit the mutation with any failure in the run",
            "            if not declared_nodeids or _matches_declared(observed, declared_nodeids)\n",
            "            if True\n",
            "test_a_failure_outside_the_declared_oracle_is_not_a_kill",
        ),
        (
            "skip parsing: count a skipped testcase as executed",
            "            if case.find(\"skipped\") is not None:\n"
            "                skipped.append(nodeid)\n"
            "                continue\n",
            "",
            "test_a_skipped_oracle_is_not_reported_as_a_survivor",
        ),
    ]


def _run_one(tmp_path: Path, old: str, new: str, target_test: str) -> subprocess.CompletedProcess:
    """Copy instrument + tests, apply one edit, run only the naming test."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts").mkdir(parents=True)
    (sandbox / "tests").mkdir(parents=True)
    harness_copy = sandbox / "scripts" / "mutation_corpus_v2.py"
    shutil.copy2(_HARNESS, harness_copy)
    shutil.copy2(_TESTS, sandbox / "tests" / "test_mutation_harness_v2.py")

    source = harness_copy.read_text()
    assert source.count(old) == 1, f"self-falsifier target not found exactly once: {old[:60]!r}"
    harness_copy.write_text(source.replace(old, new))

    return subprocess.run(
        [
            sys.executable, "-m", "pytest",
            f"tests/test_mutation_harness_v2.py::{target_test}",
            "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
        ],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/root"),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


@pytest.mark.parametrize(
    ("label", "old", "new", "target_test"),
    _mutations(),
    ids=[m[0].split(":")[0].replace(" ", "_") for m in _mutations()],
)
def test_removing_a_guard_turns_its_own_test_red(
    tmp_path: Path, label: str, old: str, new: str, target_test: str
) -> None:
    """The claim `test_mutation_harness_v2.py` makes about each guard, checked."""
    completed = _run_one(tmp_path, old, new, target_test)
    assert completed.returncode != 0, (
        f"removing the guard '{label}' left {target_test} GREEN -- the guard is not "
        f"load-bearing, or the test does not discriminate it.\n{completed.stdout[-1500:]}"
    )


def test_the_unmutated_instrument_passes_every_targeted_test(tmp_path: Path) -> None:
    """The positive control for the table above.

    Without it, every row could be passing because the sandbox is broken rather
    than because the guard was removed -- the same vacuous-oracle defect the
    instrument itself exists to refuse.
    """
    sandbox = tmp_path / "clean"
    (sandbox / "scripts").mkdir(parents=True)
    (sandbox / "tests").mkdir(parents=True)
    shutil.copy2(_HARNESS, sandbox / "scripts" / "mutation_corpus_v2.py")
    shutil.copy2(_TESTS, sandbox / "tests" / "test_mutation_harness_v2.py")
    targets = sorted({m[3] for m in _mutations()})
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            *[f"tests/test_mutation_harness_v2.py::{t}" for t in targets],
            "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
        ],
        cwd=sandbox, capture_output=True, text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/root"),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert completed.returncode == 0, completed.stdout[-2000:]


def test_the_checked_in_instrument_is_never_edited_by_these_tests(tmp_path: Path) -> None:
    """Asserted by digest ACROSS a real mutation run, not alongside one.

    An earlier version hashed the file twice in a row without invoking `_run_one`,
    so it compared the file to itself and would have held even if every mutation
    edited the checked-in tree. The edit path has to actually run in between.
    """
    before = hashlib.sha256(_HARNESS.read_bytes()).hexdigest()
    tests_before = hashlib.sha256(_TESTS.read_bytes()).hexdigest()
    label, old, new_text, target = _mutations()[0]
    _run_one(tmp_path, old, new_text, target)
    assert hashlib.sha256(_HARNESS.read_bytes()).hexdigest() == before, (
        "a self-falsifier edited the checked-in instrument instead of its copy"
    )
    assert hashlib.sha256(_TESTS.read_bytes()).hexdigest() == tests_before
