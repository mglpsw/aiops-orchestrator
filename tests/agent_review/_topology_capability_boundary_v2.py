"""Finite structural checks for the #262 raw/capability boundary.

This deliberately does not infer receiver types, follow import graphs, resolve
factories, reconstruct module/package identities, or model Python semantics.
It answers finite lexical-source and real-object-shape questions only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.agent_review._target_pack_epoch_contract_v2 import (
    _TOPOLOGY_CAPABILITY_FORBIDDEN_API_NAMES_V2,
)


RAW_MODULE_LEAF = "_mount_topology_raw_v2"
EXPECTED_PRODUCT_IMPORTER = Path("app/agent_review/target_pack_epoch_v2.py")

@dataclass(frozen=True)
class RawImportSite:
    path: Path
    line: int
    form: str


def _is_test_source(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return bool(relative.parts and relative.parts[0] == "tests")


def product_python_sources(root: Path) -> tuple[Path, ...]:
    """Derive the non-test Python universe without a product-root allowlist."""

    return tuple(sorted(
        path
        for path in root.rglob("*.py")
        if path.is_file()
        and not _is_test_source(root, path)
        and ".git" not in path.relative_to(root).parts
        and ".venv" not in path.relative_to(root).parts
        and "__pycache__" not in path.relative_to(root).parts
    ))


def _names_raw_module_leaf(node: ast.Import | ast.ImportFrom) -> bool:
    """Whether an ordinary static import literally names the unique leaf."""

    if isinstance(node, ast.Import):
        return any(
            RAW_MODULE_LEAF in alias.name.split(".")
            for alias in node.names
        )
    return (
        RAW_MODULE_LEAF in (node.module or "").split(".")
        or any(alias.name == RAW_MODULE_LEAF for alias in node.names)
    )


def raw_import_sites(
    root: Path, sources: Iterable[Path] | None = None
) -> tuple[RawImportSite, ...]:
    sites: list[RawImportSite] = []
    for path in product_python_sources(root) if sources is None else sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Import, ast.ImportFrom))
                and _names_raw_module_leaf(node)
            ):
                sites.append(RawImportSite(
                    path=path.relative_to(root),
                    line=node.lineno,
                    form=type(node).__name__,
                ))
    return tuple(sites)


def product_raw_importers(root: Path) -> frozenset[Path]:
    """Distinct product source paths, without module-identity reduction."""

    return frozenset(site.path for site in raw_import_sites(root))


def capability_shape_violations(capability: object) -> tuple[str, ...]:
    """Inspect the actual object's ordinary representation and public shape."""

    names = set(dir(capability))
    slots: set[str] = set()
    for cls in type(capability).__mro__:
        declared = getattr(cls, "__slots__", ())
        slots.update((declared,) if isinstance(declared, str) else declared)
    exposed = names | slots
    violations = sorted(
        name
        for name in _TOPOLOGY_CAPABILITY_FORBIDDEN_API_NAMES_V2
        if name in exposed
    )
    return tuple(violations)


def public_resolution_shape_violations(resolution: object) -> tuple[str, ...]:
    """Forbid proof/representation inventory in the ordinary typed result."""

    forbidden = ("validated_frontier", "records", "children", "by_id")
    return tuple(name for name in forbidden if hasattr(resolution, name))
