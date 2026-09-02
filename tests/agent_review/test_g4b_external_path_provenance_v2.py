"""#200-G4B: mechanical provenance proof for the migrated consumer surface.

`#200-G4B`'s own issue text is explicit about the tradeoff this file makes:

    "If a mechanical (AST/callgraph) enforcement of [the provenance] rule
    cannot be made to discriminate correctly, do not ship it -- fall back
    to a decidable approximation (capability-typed parameters ...) rather
    than an undiscriminating proof."

A full cross-module callgraph proof that "no raw caller path reaches a
filesystem call outside the authority" would require real type inference
(distinguishing a `Path` variable from an `ExternalInputFileV2` variable by
static analysis alone, across function boundaries and module imports) --
that is exactly the kind of proof this repository's own precedent
(`test_target_pack_arch_v2.py`'s single-adapter check) keeps deliberately
narrow and single-file. This file makes the SAME choice, applied to the
G4B migration surface specifically, rather than attempting a broader proof
that could not be made sound quickly and would then be shipped anyway,
undiscriminating.

What IS decidable, mechanically, per file, with plain `ast`:

1. The four raw pathlib methods that carried every #283/#291 defect
   (`is_file`, `is_dir`, `iterdir`, `glob`) must not appear ANYWHERE in the
   migrated consumer files at all -- every legitimate use of "does this
   external path exist / what type is it / what does it contain" now goes
   through `external_path_ingress_v2`'s capabilities instead, which do not
   expose these methods by these names on `Path.` receivers in the
   consumer's own source.
2. `Path(...).read_text(...)`/`Path(...).read_bytes(...)` -- reading
   caller-controlled content directly off a freshly constructed `Path`,
   the exact shape every migrated call site used before this PR -- must
   not appear either. Reads that DO appear must be capability method
   calls (the receiver is not itself a `Path(...)` constructor call).

This is the capability-typed-parameter fallback made mechanical: it does
not attempt to prove no OTHER indirection could smuggle a raw path to a
read (that would be the undiscriminating proof the issue says not to
ship), but it does prove, per file, that the textual pattern responsible
for every prior recurrence of this defect class is absent, and stays
absent under future edits.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The G4B migration surface: every consumer this slice moved onto the
# central authority. Engine-derived/internal-path modules (target_pack_
# plan_v2.py's `_read_on_disk_sha256_v2`, target_pack_operation_v2.py's
# `_read_target_owned_bytes_v2`, etc.) are deliberately NOT in this list --
# those operate on paths already proven contained by `resolve_within_
# target_root_v2`, which this design explicitly keeps legal (see `external_
# path_ingress_v2.py`'s own module docstring: "Filesystem work on internal/
# derived or already-validated paths remains legal").
MIGRATED_FILES = (
    REPO_ROOT / "app" / "agent_review" / "profile_loader_v2.py",
    REPO_ROOT / "app" / "agent_review" / "payload_references_v2.py",
    REPO_ROOT / "app" / "agent_review" / "authoritative_check_policy_v2.py",
    REPO_ROOT / "app" / "agent_review" / "diff_acquisition_v2.py",
    REPO_ROOT / "scripts" / "agent-review-target-pack-v2.py",
    REPO_ROOT / "scripts" / "aiops-review-quality-gate-v2.py",
    REPO_ROOT / "scripts" / "aiops-review-build-payload-set-v2.py",
    REPO_ROOT / "scripts" / "aiops-acquire-authoritative-checks-v2.py",
)

_FORBIDDEN_EXISTENCE_OR_TYPE_METHODS = frozenset({"is_file", "is_dir", "iterdir", "glob"})
_READ_METHODS = frozenset({"read_text", "read_bytes"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", MIGRATED_FILES, ids=lambda p: p.name)
def test_no_raw_existence_or_type_probe_outside_the_authority(path: Path) -> None:
    """`is_file`/`is_dir`/`iterdir`/`glob` must not appear at all: every
    migrated consumer now asks the central authority, which owns these
    checks, instead of asking the filesystem directly. This is precisely
    the method-name shape every #283/#291 recurrence shared."""

    tree = _parse(path)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_EXISTENCE_OR_TYPE_METHODS
    ]
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)}: raw existence/type probe(s) at line(s) {offenders} -- "
        f"route through external_path_ingress_v2 instead"
    )


@pytest.mark.parametrize("path", MIGRATED_FILES, ids=lambda p: p.name)
def test_no_read_directly_off_a_fresh_path_constructor(path: Path) -> None:
    """`Path(...).read_text()`/`Path(...).read_bytes()` -- reading
    caller-controlled bytes directly off a freshly constructed `Path`, with
    no authority in between -- was the exact shape of every migrated call
    site before this PR. A `read_text`/`read_bytes` call remains legal, but
    only as a CAPABILITY method (its receiver must not itself be a `Path(
    ...)` call): `validate_external_input_file_v2(...).read_bytes()` and
    `some_capability.read_text()` both pass; `Path(path).read_text()` does
    not."""

    tree = _parse(path)
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _READ_METHODS:
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "Path"
        ):
            offenders.append(node.lineno)
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)}: read directly off a fresh Path(...) at line(s) {offenders} -- "
        f"route through external_path_ingress_v2's capability instead"
    )
