"""`#200-F` authority A -- the operational-refusal family is derivable.

## Why this file exists at all

`#276` shipped `test_cli_except_tuple_is_complete_by_construction`, which was
**green** while the CLI leaked a raw `pydantic.ValidationError` traceback for
an ordinary `--delivery-id` value. It failed for two compounding reasons:

1. it enumerated by *instantiation probe* (`Cls("probe").reason_code ==
   "probe"`), so it saw 74 of 95 owner classes and silently skipped the rest;
2. it accepted **written prose** as the disposition for anything it could not
   place, and one of those written justifications was factually false.

So this file states invariants that are *computed*, never asserted in prose.
There is no allowlist of blessed exceptions here, and no free-text excuse
field. Each of the three invariants below fails loudly on a residue it cannot
classify structurally.

## Deliberately stated limit

These invariants range over exception classes **defined in
`app.agent_review`**. They say nothing about `pydantic.ValidationError`,
`OSError`, or any other foreign class -- by design. Keeping foreign exceptions
away from the boundary is the job of the ingress validation authority
(`operational_ingress_v2`), and the job of the behavioural product tests that
assert the boundary emits no traceback for the adversarial corpus. Two
independent controls of *different kinds*; neither is asked to cover for the
other. Sole reliance on a single enumeration control is exactly what `#276`
falsified.
"""

from __future__ import annotations

import ast
import collections
import functools
import importlib
import pathlib
import pkgutil

import pytest

import app.agent_review as agent_review_package
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

_PACKAGE_ROOT_V2 = pathlib.Path(agent_review_package.__file__).parent

#: The product's real entry point. The closure is seeded from what THIS file
#: imports -- transitively, including function-local imports -- so it is
#: derived from the code rather than maintained beside it.
_PRODUCT_ENTRY_POINT_V2 = (
    pathlib.Path(agent_review_package.__file__).parents[2]
    / "scripts"
    / "aiops-review-run-v2.py"
)


def _import_every_package_module_v2() -> None:
    """Import the whole package explicitly.

    `#276` walked `sys.modules` instead, which made its structural test
    order-dependent: it passed alone and failed in a combined run, because a
    module nobody had imported yet was invisible. Enumerating with
    `pkgutil` removes the dependence on what some earlier test happened to
    import.
    """
    for module_info in pkgutil.iter_modules(agent_review_package.__path__):
        importlib.import_module(f"app.agent_review.{module_info.name}")


@functools.cache
def _package_exception_classes_v2() -> frozenset[type[BaseException]]:
    _import_every_package_module_v2()
    found: set[type[BaseException]] = set()
    for module_info in pkgutil.iter_modules(agent_review_package.__path__):
        module = importlib.import_module(f"app.agent_review.{module_info.name}")
        for attribute_name in dir(module):
            candidate = getattr(module, attribute_name)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, BaseException)
                and candidate.__module__.startswith("app.agent_review.")
            ):
                found.add(candidate)
    return frozenset(found)


def _local_imports_in_source_v2(source: str) -> list[str]:
    """Package modules imported anywhere in this source, including inside
    functions -- the CLI defers most of its imports into the outer and inner
    entry functions, and a top-level-only scan would miss nearly all of them.
    """
    discovered: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "app.agent_review."
        ):
            discovered.append(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.agent_review."):
                    discovered.append(alias.name.split(".")[-1])
    return discovered


def _entry_point_local_imports_v2() -> list[str]:
    return _local_imports_in_source_v2(
        _PRODUCT_ENTRY_POINT_V2.read_text(encoding="utf-8")
    )


def _module_local_imports_v2(module_name: str) -> list[str]:
    source_path = _PACKAGE_ROOT_V2 / f"{module_name}.py"
    if not source_path.exists():
        return []
    return _local_imports_in_source_v2(source_path.read_text(encoding="utf-8"))


@functools.cache
@functools.cache
def _v2_product_path_closure_v2() -> frozenset[str]:
    """Every package module a real run can execute, derived from the CLI.

    The first revision seeded this from a hand-written tuple of module names
    and carried a comment claiming the closure "cannot silently drift narrower
    than the code". That claim was **false**, and adversarial review proved
    it: six of the eight ``operational_*`` modules -- including
    ``operational_run_v2``, which the inner calls directly -- were outside the
    closure, because they *import* the listed roots rather than being imported
    by them. Invariant A3 was therefore blind to exactly the modules this
    slice added.

    A hand-maintained list with a false claim of automatic completeness is the
    `#276` control in different clothing. Seeding from the entry point's own
    imports removes the list: a module reachable from the CLI is in the
    closure because the CLI reaches it, and nobody has to remember.
    """
    seen: set[str] = set()
    pending = collections.deque(_entry_point_local_imports_v2())
    while pending:
        module_name = pending.popleft()
        if module_name in seen:
            continue
        seen.add(module_name)
        pending.extend(
            dependency
            for dependency in _module_local_imports_v2(module_name)
            if dependency not in seen
        )
    return seen


@functools.cache
def _class_definition_index_v2() -> dict[str, tuple[str, ...]]:
    """Map class name -> the module(s) whose source actually defines it.

    ``cls.__module__`` is **not** trustworthy here.
    ``_target_pack_epoch_contract_v2.py`` rewrites
    ``TargetPackEpochError.__module__`` to name the public module instead of
    the private one that defines it, so a source lookup keyed on
    ``__module__`` reads the wrong file, finds no ``reason_code``, and
    reports a false violation. Resolving definitions from the AST removes the
    dependence on a mutable runtime attribute that any module is free to
    rewrite.
    """
    index: dict[str, list[str]] = collections.defaultdict(list)
    for source_path in sorted(_PACKAGE_ROOT_V2.glob("*.py")):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                index[node.name].append(source_path.stem)
    return {name: tuple(modules) for name, modules in index.items()}


@functools.cache
def _defining_module_of_v2(exception_class: type[BaseException]) -> str | None:
    """The module that textually defines the class, or None if absent.

    Fails loudly on an ambiguous name rather than picking one silently: two
    same-named classes would make every downstream answer arbitrary.
    """
    candidates = _class_definition_index_v2().get(exception_class.__name__, ())
    if not candidates:
        return None
    assert len(candidates) == 1, (
        f"{exception_class.__name__} is defined in more than one module "
        f"({list(candidates)}); this control cannot attribute it"
    )
    return candidates[0]


@functools.cache
def _publishes_a_structured_refusal_code_v2(exception_class: type[BaseException]) -> bool:
    """True when the class assigns ``self.reason_code`` in its own body.

    Read from the AST rather than by constructing a probe instance. `#276`
    probed with `Cls("probe-reason")`, which silently reports False for every
    owner whose constructor takes a different shape -- that is how 21 classes
    disappeared from a control that claimed completeness.
    """
    defining_module = _defining_module_of_v2(exception_class)
    if defining_module is None:
        return False
    source_path = _PACKAGE_ROOT_V2 / f"{defining_module}.py"
    for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef) or node.name != exception_class.__name__:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign) and any(
                isinstance(target, ast.Attribute)
                and target.attr == "reason_code"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                for target in inner.targets
            ):
                return True
    return False


@functools.cache
def _is_module_private_control_flow_signal_v2(exception_class: type[BaseException]) -> bool:
    """True when the class is an underscore-named, module-local signal.

    The first revision asked only whether the module contained *a* raise and
    *a* handler somewhere, which proves nothing about the raise sites it did
    not inspect; adversarial review added a public function raising outside
    any ``try`` and the invariant stayed green.

    The obvious repair -- prove by static analysis that every raise is
    guarded -- was attempted and **abandoned**, which is worth recording. A
    sound version needs call-graph plus exception-propagation analysis: the
    real pattern here is a nested closure that raises and an enclosing
    function that catches, so a lexical rule rejects valid code, while a
    call-graph rule has to attribute raises to the *innermost* function and
    then decide reachability from outside the module. Two attempts produced,
    in turn, false negatives on the clean tree and false positives on the
    mutant. A control I cannot get right is worse than a narrower one I can,
    because its greenness would mean nothing.

    So the exemption is narrowed to what is decidable by inspection:

    1. the class name is underscore-prefixed, i.e. not part of any public
       surface by the convention this codebase already relies on; and
    2. the name appears in no other module in the package, so no other module
       can catch it, raise it, or name it.

    Anything else must join the family and publish a reason code. That is a
    stricter bar than before -- ``AmbiguousProfileDocumentV2`` no longer
    qualifies and was made a member -- and it removes the analysis entirely
    rather than shipping one that is merely plausible.
    """
    owning_module = _defining_module_of_v2(exception_class)
    if owning_module is None:
        return False

    name = exception_class.__name__
    if not name.startswith("_"):
        return False

    for source_path in _PACKAGE_ROOT_V2.glob("*.py"):
        if source_path.stem == owning_module:
            continue
        if name in source_path.read_text(encoding="utf-8"):
            return False

    own_source = (_PACKAGE_ROOT_V2 / f"{owning_module}.py").read_text(encoding="utf-8")
    tree = ast.parse(own_source)
    raised = any(
        isinstance(node, ast.Raise)
        and node.exc is not None
        and name in ast.unparse(node.exc)
        for node in ast.walk(tree)
    )
    caught = any(
        isinstance(node, ast.ExceptHandler)
        and node.type is not None
        and name in ast.unparse(node.type)
        for node in ast.walk(tree)
    )
    return raised and caught


def test_every_reason_code_publisher_is_a_member_of_the_family() -> None:
    """A1 -- publishing a reason code *is* declaring an operational refusal.

    This is the invariant that makes the boundary derivable: a new owner
    refusal joins the family by construction, so `except
    ExpectedOperationalRefusalV2` at the boundary can never fall behind the
    semantic layer the way an enumerated tuple did.
    """
    offenders = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in _package_exception_classes_v2()
        if _publishes_a_structured_refusal_code_v2(cls)
        and not issubclass(cls, ExpectedOperationalRefusalV2)
    )
    assert offenders == [], (
        "these classes publish a reason_code but are not members of the "
        f"operational-refusal family: {offenders}"
    )


def test_family_membership_is_not_claimed_without_a_reason_code() -> None:
    """A2 -- the converse, so A1 cannot be 'fixed' by dropping the marker.

    Without this, a failing A1 could be silenced either by adding the marker
    (correct) or by deleting the ``reason_code`` the boundary needs in order
    to report anything (a regression that would look like a fix).
    """
    offenders = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in _package_exception_classes_v2()
        if issubclass(cls, ExpectedOperationalRefusalV2)
        and cls is not ExpectedOperationalRefusalV2
        and not _publishes_a_structured_refusal_code_v2(cls)
    )
    assert offenders == [], (
        "these classes claim family membership but publish no reason_code, "
        f"so the boundary would have nothing to emit: {offenders}"
    )


def test_every_non_member_on_the_v2_product_path_cannot_reach_the_boundary() -> None:
    """A3 -- the residue is disposed of structurally, never by prose.

    A package exception outside the family is acceptable only if it is
    *provably* unable to surface at the boundary, and only for one of two
    computed reasons:

    * its owning module is not in the v2 product-path import closure, so a
      run cannot execute the code that raises it; or
    * it is a module-private control-flow signal, raised and caught inside its
      own module and named nowhere else.

    Anything else fails here and must be given a reason code and joined to the
    family. `#276` would have written a sentence instead -- and one of its
    sentences was false.
    """
    product_path = _v2_product_path_closure_v2()
    unclassified: list[str] = []

    for cls in _package_exception_classes_v2():
        if issubclass(cls, ExpectedOperationalRefusalV2):
            continue
        owning_module = _defining_module_of_v2(cls)
        if owning_module is None or owning_module not in product_path:
            continue
        if _is_module_private_control_flow_signal_v2(cls):
            continue
        unclassified.append(f"{cls.__module__}.{cls.__name__}")

    assert sorted(unclassified) == [], (
        "these non-member exceptions are reachable on the v2 product path and "
        "are not module-private control-flow signals, so they would escape the "
        f"boundary as a raw traceback: {sorted(unclassified)}"
    )


def test_the_family_is_not_vacuous() -> None:
    """Non-vacuity control.

    Every invariant above is satisfied trivially if the family is empty or if
    the class enumeration silently collects nothing -- which is precisely the
    failure mode that let `#276`'s control stay green. Pin real magnitudes and
    a real member.
    """
    all_exceptions = _package_exception_classes_v2()
    members = {
        cls
        for cls in all_exceptions
        if issubclass(cls, ExpectedOperationalRefusalV2)
        and cls is not ExpectedOperationalRefusalV2
    }

    assert len(all_exceptions) >= 40, len(all_exceptions)
    assert len(members) >= 30, len(members)

    from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError

    assert DiffAcquisitionError in members
    assert issubclass(DiffAcquisitionError, ValueError), (
        "historical bases must be retained so existing except/raises sites keep working"
    )


def test_foreign_exceptions_are_deliberately_outside_the_family() -> None:
    """The negative half of the two-epoch discipline.

    A programmer defect must stay a programmer defect. If any of these ever
    became a family member the boundary would start reporting internal bugs as
    orderly product outcomes, which is worse than crashing.
    """
    import pydantic

    for foreign in (
        pydantic.ValidationError,
        TypeError,
        AttributeError,
        KeyError,
        ValueError,
        RuntimeError,
        OSError,
    ):
        assert not issubclass(foreign, ExpectedOperationalRefusalV2), foreign


@pytest.mark.parametrize(
    "module_name, class_name",
    [
        ("diff_acquisition_v2", "DiffAcquisitionError"),
        ("run_assembly_v2", "RunAssemblyError"),
        ("synthesis_v2", "SynthesisErrorV2"),
        ("readiness_decision_v2", "ReadinessDecisionError"),
        ("chunk_result_scope_v2", "ChunkResultScopeError"),
        ("review_content_v2", "ReviewContentBindingError"),
        ("_router_receipt_v2", "RouterReceiptError"),
    ],
)
def test_specific_reason_codes_survive_family_membership(
    module_name: str, class_name: str
) -> None:
    """Reason codes are preserved, not collapsed into one generic value.

    The grant is explicit that joining a family must not cost an owner its
    specific code; a boundary that reported a single ``operational_refusal``
    for everything would be uninformative in exactly the cases operators care
    about.
    """
    module = importlib.import_module(f"app.agent_review.{module_name}")
    error_class = getattr(module, class_name)

    raised = error_class("some_specific_reason")

    assert isinstance(raised, ExpectedOperationalRefusalV2)
    assert raised.reason_code == "some_specific_reason"
