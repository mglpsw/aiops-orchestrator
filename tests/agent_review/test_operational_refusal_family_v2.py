"""`#200-G4` -- the operational-refusal family is derivable, scoped to what
this slice owns.

## Why this file is narrower than its `#277` namesake

`#277`'s ``test_operational_refusal_family_v2.py`` asserted three invariants
over **every** exception class defined anywhere in ``app.agent_review`` --
across all ~75 modules the whole v2 product comprises -- seeded from a
now-dead two-process CLI (`operational_inner_control_v2.py`,
`operational_run_v2.py`, `operational_subject_v2.py`, ...) that does not exist
on `master` and is explicitly out of scope for this primitive (`#200`-G1/G3/G5
own that composition). Porting that file unmodified would either (a) fail
immediately for want of the modules it imports, or (b) if patched to import
what exists instead, silently narrow its own claimed scope while keeping
prose that claims whole-package coverage -- exactly the failure mode its own
docstring warns against ("no free-text excuse field").

Retrofitting `ExpectedOperationalRefusalV2` onto every reason-code-carrying
class in the package (`RunAssemblyError`, `SynthesisErrorV2`,
`ReadinessDecisionError`, `ChunkResultScopeError`, `ReviewContentBindingError`,
`_router_receipt_v2.RouterReceiptError`, and the rest) is real, valuable work
-- but it is `#200`-G5 recomposition's work, touching dozens of modules this
primitive does not own and has no independent-review budget to cover. Per the
port ledger, the whole `#274`/`#276`/`#277` adversarial corpus is carried
forward `PORT_AS_RED_TEST`, not as passing inheritance; this file is the G4
slice of that carry-forward, scoped honestly to the modules G4 actually adds
or touches: ``operational_refusal_v2``, ``operational_ingress_v2``,
``operational_workspace_v2``, and the two pre-existing classes G4 needed to
bring into the family for its own product path
(``diff_acquisition_v2.DiffAcquisitionError``,
``profile_loader_v2.TargetProfileLoadErrorV2``).

The three invariants themselves -- A1 (every reason-code publisher is a
member), A2 (every member publishes a reason code), A3 (every non-member
reachable from this slice's own entry point is a provably inert
control-flow signal) -- are unchanged in kind from `#277`; only the universe
they range over is scoped to this primitive's own surface.
"""

from __future__ import annotations

import ast
import functools
import importlib
import pathlib

import pytest

import app.agent_review as agent_review_package
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

_PACKAGE_ROOT_V2 = pathlib.Path(agent_review_package.__file__).parent

#: `#200-G4`'s own surface: the modules this slice adds or edits. Unlike
#: `#277`'s whole-package sweep, this list *is* the scope declaration -- G4
#: makes no claim about modules it did not touch.
_G4_OWNED_MODULES_V2: tuple[str, ...] = (
    "operational_refusal_v2",
    "operational_ingress_v2",
    "operational_workspace_v2",
    "diff_acquisition_v2",
    "profile_loader_v2",
)


@functools.cache
def _g4_owned_exception_classes_v2() -> frozenset[type[BaseException]]:
    found: set[type[BaseException]] = set()
    for module_name in _G4_OWNED_MODULES_V2:
        module = importlib.import_module(f"app.agent_review.{module_name}")
        for attribute_name in dir(module):
            candidate = getattr(module, attribute_name)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, BaseException)
                and candidate.__module__ == f"app.agent_review.{module_name}"
            ):
                found.add(candidate)
    return frozenset(found)


def _local_imports_in_source_v2(source: str) -> list[str]:
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


@functools.cache
def _publishes_a_structured_refusal_code_v2(exception_class: type[BaseException]) -> bool:
    """True when the class assigns ``self.reason_code`` in its own body.

    Read from the AST rather than by constructing a probe instance -- `#276`
    probed with ``Cls("probe-reason")``, which silently reports ``False`` for
    any owner whose constructor takes a different shape.
    """
    module_name = exception_class.__module__.rsplit(".", 1)[-1]
    source_path = _PACKAGE_ROOT_V2 / f"{module_name}.py"
    if not source_path.exists():
        return False
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


def test_every_g4_reason_code_publisher_is_a_member_of_the_family() -> None:
    """A1, scoped to what `#200-G4` owns."""
    offenders = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in _g4_owned_exception_classes_v2()
        if _publishes_a_structured_refusal_code_v2(cls)
        and not issubclass(cls, ExpectedOperationalRefusalV2)
    )
    assert offenders == [], (
        "these G4-owned classes publish a reason_code but are not members of "
        f"the operational-refusal family: {offenders}"
    )


def test_g4_family_membership_is_not_claimed_without_a_reason_code() -> None:
    """A2, the converse."""
    offenders = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in _g4_owned_exception_classes_v2()
        if issubclass(cls, ExpectedOperationalRefusalV2)
        and cls is not ExpectedOperationalRefusalV2
        and not _publishes_a_structured_refusal_code_v2(cls)
    )
    assert offenders == [], (
        "these G4-owned classes claim family membership but publish no "
        f"reason_code: {offenders}"
    )


def test_every_non_member_on_the_g4_script_path_cannot_reach_the_boundary() -> None:
    """A3, seeded from G4's own entry point rather than the dead `#277` CLI.

    Every exception class defined in a G4-owned module, that is reachable
    from the live `#200-G4` script's own import closure, and that is not a
    family member, would escape the script's
    ``except ExpectedOperationalRefusalV2`` as a raw traceback. There should
    be none: everything G4 added is a family member by construction, and the
    two pre-existing classes it pulled onto the family (`DiffAcquisitionError`,
    `TargetProfileLoadErrorV2`) were joined for exactly this reason.
    """
    entry_point = (
        pathlib.Path(agent_review_package.__file__).parents[2]
        / "scripts"
        / "aiops-review-run-v2.py"
    )
    reachable = set(_local_imports_in_source_v2(entry_point.read_text(encoding="utf-8")))
    for module_name in list(reachable):
        source_path = _PACKAGE_ROOT_V2 / f"{module_name}.py"
        if source_path.exists():
            reachable.update(_local_imports_in_source_v2(source_path.read_text(encoding="utf-8")))

    unclassified = sorted(
        f"{cls.__module__}.{cls.__name__}"
        for cls in _g4_owned_exception_classes_v2()
        if not issubclass(cls, ExpectedOperationalRefusalV2)
        and cls.__module__.rsplit(".", 1)[-1] in reachable
    )
    assert unclassified == [], (
        "these non-member exceptions are reachable from the G4 script entry "
        f"point and would escape its boundary as a raw traceback: {unclassified}"
    )


def test_the_g4_family_is_not_vacuous() -> None:
    """Non-vacuity control: every invariant above is trivially satisfied if
    the enumeration silently collects nothing."""
    all_exceptions = _g4_owned_exception_classes_v2()
    members = {
        cls
        for cls in all_exceptions
        if issubclass(cls, ExpectedOperationalRefusalV2)
        and cls is not ExpectedOperationalRefusalV2
    }

    assert len(all_exceptions) >= 3, len(all_exceptions)
    assert len(members) >= 3, len(members)

    from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError
    from app.agent_review.operational_ingress_v2 import OperationalIngressError

    assert DiffAcquisitionError in members
    assert OperationalIngressError in members
    assert issubclass(DiffAcquisitionError, ValueError), (
        "historical bases must be retained so existing except/raises sites keep working"
    )


def test_foreign_exceptions_are_deliberately_outside_the_family() -> None:
    """The negative half of the two-epoch discipline, unchanged from `#277`."""
    import pydantic

    for foreign in (
        pydantic.ValidationError,
        TypeError,
        AttributeError,
        KeyError,
        ValueError,
        RuntimeError,
        OSError,
        OverflowError,
    ):
        assert not issubclass(foreign, ExpectedOperationalRefusalV2), foreign


@pytest.mark.parametrize(
    "module_name, class_name",
    [
        ("operational_ingress_v2", "OperationalIngressError"),
        ("diff_acquisition_v2", "DiffAcquisitionError"),
        ("profile_loader_v2", "TargetProfileLoadErrorV2"),
    ],
)
def test_specific_reason_codes_survive_family_membership(
    module_name: str, class_name: str
) -> None:
    """Reason codes are preserved, not collapsed into one generic value."""
    module = importlib.import_module(f"app.agent_review.{module_name}")
    error_class = getattr(module, class_name)

    raised = error_class("some_specific_reason")

    assert isinstance(raised, ExpectedOperationalRefusalV2)
    assert raised.reason_code == "some_specific_reason"
