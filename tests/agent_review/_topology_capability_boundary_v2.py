"""Finite structural checks for the #262 raw/capability boundary.

This deliberately does not infer receiver types, follow import graphs, resolve
factories, or model Python semantics. It answers two finite questions only:
which non-test Python sources statically name the private raw module, and does
the real consumer object expose forbidden raw representation.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RAW_MODULE = "app.agent_review._mount_topology_raw_v2"
RAW_MODULE_BASENAME = "_mount_topology_raw_v2"
EXPECTED_PRODUCT_IMPORTER = "app.agent_review.target_pack_epoch_v2"

FORBIDDEN_API_NAMES = frozenset({
    "records",
    "children",
    "by_id",
    "raw",
    "raw_graph",
    "_raw",
    "_raw_graph",
    "_governing_mount_raw_v2",
    "_is_visible_raw_v2",
    "_visible_root_v2",
    "_climb_stack_v2",
    "validate_relevant_chain_v2",
    "_semantic_seeds_v2",
    "_dependency_closure_v2",
})


@dataclass(frozen=True)
class RawImportSite:
    module: str
    path: Path
    line: int
    form: str


def _module_identity(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


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


def _relative_base(module: str, level: int) -> str:
    package = module.split(".")[:-1]
    if level > 1:
        package = package[: -(level - 1)]
    return ".".join(package)


def _named_raw_module(node: ast.Import | ast.ImportFrom, module: str) -> bool:
    if isinstance(node, ast.Import):
        return any(
            alias.name == RAW_MODULE or alias.name.startswith(RAW_MODULE + ".")
            for alias in node.names
        )

    base = node.module or ""
    if node.level:
        relative = _relative_base(module, node.level)
        base = ".".join(part for part in (relative, base) if part)
    if base == RAW_MODULE or base.startswith(RAW_MODULE + "."):
        return True
    return base == "app.agent_review" and any(
        alias.name == RAW_MODULE_BASENAME for alias in node.names
    )


def raw_import_sites(
    root: Path, sources: Iterable[Path] | None = None
) -> tuple[RawImportSite, ...]:
    sites: list[RawImportSite] = []
    for path in product_python_sources(root) if sources is None else sources:
        module = _module_identity(root, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and _named_raw_module(
                node, module
            ):
                sites.append(RawImportSite(
                    module=module,
                    path=path,
                    line=node.lineno,
                    form=type(node).__name__,
                ))
    return tuple(sites)


def product_raw_importers(root: Path) -> frozenset[str]:
    return frozenset(site.module for site in raw_import_sites(root))


def capability_shape_violations(capability: object) -> tuple[str, ...]:
    """Inspect the actual object's ordinary representation and public shape."""

    names = set(dir(capability))
    slots: set[str] = set()
    for cls in type(capability).__mro__:
        declared = getattr(cls, "__slots__", ())
        slots.update((declared,) if isinstance(declared, str) else declared)
    exposed = names | slots
    violations = sorted(name for name in FORBIDDEN_API_NAMES if name in exposed)
    if hasattr(capability, "__dict__"):
        violations.append("__dict__")
    return tuple(violations)
