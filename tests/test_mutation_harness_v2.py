"""The mutation instrument's own failure modes, asserted rather than described.

Every test here drives `scripts/mutation_corpus_v2.py` into a way of *not having
measured the subject* and requires it to say so. The point is narrow and worth
stating plainly: a mutation harness that can report `KILLED` without the oracle
having run is worse than no harness, because it manufactures confidence instead
of merely failing to find something. Each guard below therefore has a test that
fails when the guard is removed -- the same standard the harness exists to hold
a corpus to.

The subjects are synthetic. Nothing here imports `app.agent_review`, and nothing
here is a corpus: this file tests the instrument, not any production property.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mutation_corpus_v2.py"
_spec = importlib.util.spec_from_file_location("mutation_corpus_v2", _HARNESS_PATH)
assert _spec and _spec.loader
harness = importlib.util.module_from_spec(_spec)
sys.modules["mutation_corpus_v2"] = harness
_spec.loader.exec_module(harness)

Mutation = harness.Mutation
MutationOutcome = harness.MutationOutcome
SelectionResult = harness.SelectionResult


# ---------------------------------------------------------------- fixtures --


def _synthetic_tree(root: Path, *, oracle: str = "behavioural") -> Path:
    """A subject with a guard, and an oracle that is supposed to notice its removal."""
    tree = root / "tree"
    tree.mkdir()
    (tree / "subject.py").write_text(
        "def admit(value):\n"
        "    if value < 0:\n"
        "        raise ValueError('refused')\n"
        "    return value\n"
    )
    bodies = {
        "behavioural": (
            "import pytest\nfrom subject import admit\n\n"
            "def test_negative_is_refused():\n"
            "    with pytest.raises(ValueError):\n"
            "        admit(-1)\n"
        ),
        "infra": (
            "import pytest\nfrom subject import admit\n\n"
            "@pytest.fixture\n"
            "def broken():\n"
            "    raise RuntimeError('setup failure, not a behavioural difference')\n\n"
            "def test_negative_is_refused(broken):\n"
            "    with pytest.raises(ValueError):\n"
            "        admit(-1)\n"
        ),
        "import_error": (
            "import definitely_not_a_module_xyz  # noqa: F401\n\n"
            "def test_negative_is_refused():\n"
            "    assert True\n"
        ),
    }
    (tree / "test_subject.py").write_text(bodies[oracle])
    return tree


_REAL_MUTATION = dict(
    name="remove the negative-value guard",
    relative_path="subject.py",
    old="    if value < 0:\n        raise ValueError('refused')\n",
    new="    if False:\n        raise ValueError('refused')\n",
    occurrences=1,
    nodeids=("test_subject.py::test_negative_is_refused",),
)


# ------------------------------------------------------- end-to-end classes --


def test_a_real_mutation_with_a_real_oracle_is_killed(tmp_path: Path) -> None:
    """The positive control. Without it every refusal below could be vacuous."""
    tree = _synthetic_tree(tmp_path)
    result = harness.run_mutation(Mutation(**_REAL_MUTATION), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.KILLED, result.detail
    assert result.selection is not None
    assert result.selection.selected_count == 1
    assert result.selection.behavioural_failures


def test_an_oracle_that_does_not_discriminate_is_survived_not_killed(tmp_path: Path) -> None:
    """A mutation the oracle cannot see is a hole in the corpus, and must be named one."""
    tree = _synthetic_tree(tmp_path)
    inert = dict(_REAL_MUTATION)
    inert["name"] = "cosmetic edit the oracle cannot observe"
    inert["old"] = "        raise ValueError('refused')\n"
    inert["new"] = "        raise ValueError('refused')  # comment\n"
    result = harness.run_mutation(Mutation(**inert), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.SURVIVED, result.detail


def test_a_nodeid_that_does_not_exist_is_invalid_selector_not_killed(tmp_path: Path) -> None:
    """pytest exits non-zero for a missing nodeid while running nothing.

    Reproduced on the installed pytest: a nonexistent nodeid exits 4 and a
    selector matching nothing exits 5. Reading either as a kill lets a mutation
    be declared dead by a test that does not exist.
    """
    tree = _synthetic_tree(tmp_path)
    missing = dict(_REAL_MUTATION)
    missing["nodeids"] = ("test_subject.py::test_no_such_test_xyzzy",)
    result = harness.run_mutation(Mutation(**missing), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.INVALID_SELECTOR, result.detail


def test_a_collection_error_is_not_a_behavioural_kill(tmp_path: Path) -> None:
    """An oracle that cannot even be imported has not discriminated anything."""
    tree = _synthetic_tree(tmp_path, oracle="import_error")
    result = harness.run_mutation(Mutation(**_REAL_MUTATION), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.COLLECTION_FAILURE, result.detail


def test_an_infrastructure_error_alone_is_not_a_behavioural_kill(tmp_path: Path) -> None:
    """The case pytest's exit status genuinely cannot separate.

    A failing fixture and a failing assertion BOTH exit 1 -- measured. Only the
    structured report distinguishes `<error>` from `<failure>`, which is why the
    harness reads it instead of the exit status.
    """
    tree = _synthetic_tree(tmp_path, oracle="infra")
    result = harness.run_mutation(Mutation(**_REAL_MUTATION), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.INFRA_FAILURE, result.detail
    assert result.selection is not None
    assert result.selection.exit_status == 1, "the exit status alone would have said 'kill'"
    assert result.selection.infrastructure_errors
    assert not result.selection.behavioural_failures


def test_an_edit_that_does_not_match_is_mutation_not_applied(tmp_path: Path) -> None:
    """A no-op edit measures the unmutated subject while reporting on the mutant."""
    tree = _synthetic_tree(tmp_path)
    absent = dict(_REAL_MUTATION)
    absent["old"] = "this text is not in the subject\n"
    result = harness.run_mutation(Mutation(**absent), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.MUTATION_NOT_APPLIED, result.detail


def test_an_edit_matching_more_sites_than_declared_is_refused(tmp_path: Path) -> None:
    """Declared occurrence count, not discovered: an edit that lands somewhere the
    author did not intend is measuring something other than what it claims."""
    tree = _synthetic_tree(tmp_path)
    (tree / "subject.py").write_text((tree / "subject.py").read_text() + "\n# refused\n")
    ambiguous = dict(_REAL_MUTATION)
    ambiguous["old"] = "refused"
    ambiguous["new"] = "denied"
    ambiguous["occurrences"] = 1
    result = harness.run_mutation(Mutation(**ambiguous), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.MUTATION_NOT_APPLIED, result.detail


def test_an_edit_whose_replacement_is_identical_is_mutation_not_applied(tmp_path: Path) -> None:
    """`new == old`: the declared site count is satisfied and nothing changes.

    The occurrence check cannot catch this one, so the byte-level check is the
    only thing standing between a corpus typo and a run that measures the
    UNMUTATED subject and then reports the oracle's pass as a survivor.
    """
    tree = _synthetic_tree(tmp_path)
    identical = dict(_REAL_MUTATION)
    identical["new"] = identical["old"]
    result = harness.run_mutation(Mutation(**identical), tree=tree, tmpdir=tmp_path)
    assert result.outcome is MutationOutcome.MUTATION_NOT_APPLIED, result.detail


def test_the_subject_is_restored_byte_identically(tmp_path: Path) -> None:
    tree = _synthetic_tree(tmp_path)
    before = (tree / "subject.py").read_bytes()
    harness.run_mutation(Mutation(**_REAL_MUTATION), tree=tree, tmpdir=tmp_path)
    assert (tree / "subject.py").read_bytes() == before


# ------------------------------------------------------- execution identity --


def test_the_run_reports_which_subject_bytes_it_actually_imported(tmp_path: Path) -> None:
    """Execution identity is observed, not inferred from the file on disk."""
    tree = _synthetic_tree(tmp_path)
    result = harness.run_mutation(Mutation(**_REAL_MUTATION), tree=tree, tmpdir=tmp_path)
    assert result.selection is not None
    reported = result.selection.imported_subject_digests.get("subject.py")
    assert reported, "the child did not report the subject it imported"
    assert len(reported) == 64


def _build_stale_cache(tree: Path) -> str:
    """Leave a bytecode cache that CPython's `(mtime, size)` check will accept for
    bytes the source no longer holds. Returns the NEW on-disk source text."""
    subject = tree / "subject.py"
    original = subject.read_text()
    stale_variant = original.replace("value < 0", "value < 9")   # identical length
    assert len(stale_variant) == len(original), "the fixture must keep the size equal"
    mtime = subject.stat().st_mtime
    subprocess.run(
        [sys.executable, "-c", "import subject"],
        cwd=tree, check=True, capture_output=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(tree)},
    )
    assert list(tree.glob("__pycache__/*.pyc")), "fixture failed to build a cache"
    subject.write_text(stale_variant)
    os.utime(subject, (mtime, mtime))
    return stale_variant


def test_the_stale_bytecode_defect_is_real_without_the_remedy(tmp_path: Path) -> None:
    """The defect, demonstrated on this interpreter before anything claims to fix it.

    A same-size rewrite inside one mtime tick satisfies CPython's `(mtime, size)`
    invalidation, so a plain child imports the OLD code while the file holds the
    new bytes. Measured separately: `PYTHONDONTWRITEBYTECODE=1` does NOT prevent
    it -- it suppresses *writing* a cache, not *reading* one that already exists.

    Without this control the next test would be vacuous: it would be asserting
    that a defect is absent without ever showing it could occur.
    """
    tree = _synthetic_tree(tmp_path)
    _build_stale_cache(tree)
    probe = "import subject; print(subject.admit.__code__.co_consts)"
    base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(tree)}

    plain = subprocess.run([sys.executable, "-c", probe], cwd=tree, capture_output=True,
                           text=True, env=base_env)
    nowrite = subprocess.run([sys.executable, "-c", probe], cwd=tree, capture_output=True,
                             text=True, env={**base_env, "PYTHONDONTWRITEBYTECODE": "1"})
    # The stale cache holds the ORIGINAL guard (0); the file on disk now says 9.
    assert "0" in plain.stdout and "9" not in plain.stdout, plain.stdout
    assert "0" in nowrite.stdout and "9" not in nowrite.stdout, (
        "PYTHONDONTWRITEBYTECODE alone was expected NOT to help: " + nowrite.stdout
    )


def test_the_harness_run_is_immune_to_a_stale_cache_in_the_tree(tmp_path: Path) -> None:
    """The remedy, against the same fixture the previous test showed is dangerous.

    Discrimination here is BEHAVIOURAL, and it has to be. The probe hashes the
    source file, and `_pyc_is_stale` compares `(mtime, size)` -- which this
    fixture deliberately makes agree. Both would therefore report "fine" while
    the child ran the old code, so asserting on them proves nothing. Measured:
    an earlier version of this test asserted exactly those two things and stayed
    GREEN with `PYTHONPYCACHEPREFIX` removed.

    What separates the two worlds is what `admit(5)` does. The bytes on disk say
    `value < 9` and refuse it; the cached bytes say `value < 0` and admit it.
    """
    tree = _synthetic_tree(tmp_path)
    _build_stale_cache(tree)  # disk now holds `value < 9`; the cache holds `value < 0`
    (tree / "test_subject.py").write_text(
        "import pytest\nfrom subject import admit\n\n"
        "def test_five_is_refused_by_the_bytes_on_disk():\n"
        "    with pytest.raises(ValueError):\n"
        "        admit(5)\n"
    )
    subject = tree / "subject.py"

    selection = harness.run_selection(
        ("test_subject.py::test_five_is_refused_by_the_bytes_on_disk",),
        tree=tree, subjects=["subject.py"], tmpdir=tmp_path,
    )
    assert not selection.behavioural_failures, (
        "the child executed the cached bytecode rather than the source on disk: "
        f"{selection.behavioural_failures}"
    )
    assert selection.exit_status == 0, selection
    on_disk = hashlib.sha256(subject.read_bytes()).hexdigest()
    assert selection.imported_subject_digests.get("subject.py") == on_disk


def test_a_materialisation_starts_with_no_bytecode(tmp_path: Path) -> None:
    """The structural half of the stale-cache defence: a tree that never held a
    cache cannot serve one."""
    materialised = harness._materialise(tmp_path / "mirror")
    assert not list(materialised.rglob("*.pyc"))


def test_materialising_does_not_touch_the_working_tree(tmp_path: Path) -> None:
    """An interrupted run must not leave a developer holding a modified checkout."""
    subject = harness.REPO_ROOT / "scripts" / "mutation_corpus_v2.py"
    before = subject.read_bytes()
    mirror = harness._materialise(tmp_path / "mirror")
    (mirror / "scripts" / "mutation_corpus_v2.py").write_text("# clobbered\n")
    assert subject.read_bytes() == before


# ------------------------------------------------- classification, in isolation --


def _selection(**overrides) -> SelectionResult:
    base = dict(
        exit_status=0,
        executed_nodeids=("t::a",),
        behavioural_failures=(),
        infrastructure_errors=(),
        imported_subject_digests={"s.py": "d" * 64},
        stale_bytecode_detected=(),
    )
    base.update(overrides)
    return SelectionResult(**base)


_EXPECTED = {"s.py": "d" * 64}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, MutationOutcome.SURVIVED),
        ({"exit_status": 1, "behavioural_failures": ("t::a",)}, MutationOutcome.KILLED),
        ({"exit_status": 5, "executed_nodeids": ()}, MutationOutcome.INVALID_SELECTOR),
        ({"exit_status": 4, "executed_nodeids": ()}, MutationOutcome.INVALID_SELECTOR),
        ({"exit_status": 2, "executed_nodeids": ()}, MutationOutcome.COLLECTION_FAILURE),
        ({"exit_status": 1, "infrastructure_errors": ("t::a",)}, MutationOutcome.INFRA_FAILURE),
        ({"stale_bytecode_detected": ("s.py",)}, MutationOutcome.INFRA_FAILURE),
        ({"imported_subject_digests": {}}, MutationOutcome.INFRA_FAILURE),
        ({"imported_subject_digests": {"s.py": "e" * 64}}, MutationOutcome.INFRA_FAILURE),
        ({"executed_nodeids": (), "exit_status": 0}, MutationOutcome.INVALID_SELECTOR),
        ({"exit_status": 3, "executed_nodeids": ("t::a",)}, MutationOutcome.INFRA_FAILURE),
    ],
)
def test_every_observation_maps_to_exactly_one_outcome(overrides, expected) -> None:
    """`classify` is pure, so the taxonomy can be exercised without pytest at all.

    The table is written out rather than derived from the enum: a table generated
    from the thing it checks would agree with any change to it, including a wrong
    one.
    """
    outcome, _ = harness.classify(_selection(**overrides), expected_subject_digests=_EXPECTED)
    assert outcome is expected


def test_killed_requires_a_behavioural_failure_and_nothing_else_does() -> None:
    """`KILLED` is the only outcome that asserts something about the subject."""
    killers = [
        overrides
        for overrides in (
            {"exit_status": 1, "behavioural_failures": ("t::a",)},
            {"exit_status": 1, "infrastructure_errors": ("t::a",)},
            {"exit_status": 2}, {"exit_status": 4}, {"exit_status": 5},
        )
        if harness.classify(_selection(**overrides), expected_subject_digests=_EXPECTED)[0]
        is MutationOutcome.KILLED
    ]
    assert killers == [{"exit_status": 1, "behavioural_failures": ("t::a",)}]


def test_an_empty_selection_is_refused_rather_than_run(tmp_path: Path) -> None:
    with pytest.raises(harness.MutationHarnessError):
        harness.run_selection((), tree=tmp_path, subjects=[], tmpdir=tmp_path)


def test_the_harness_imports_nothing_from_the_product() -> None:
    """The instrument must be usable against any subject, including a future one
    that replaces the module this corpus was first written for.

    Checked over the IMPORT statements, not over the source text: the first
    version of this test was a substring search and failed on the module's own
    prose explaining that it does not import the product. A test that a
    docstring cannot mention the thing it is about is testing the wrong object.
    """
    import ast

    tree = ast.parse(_HARNESS_PATH.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [name for name in imported if name.split(".")[0] == "app"]
    assert not offenders, f"the instrument imports from the product: {offenders}"
