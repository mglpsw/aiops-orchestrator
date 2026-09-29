#!/usr/bin/env python3
"""Deterministic validator and materializer for AgentReview standalone distribution (#351-B0).

Proves that AgentReview can be materialized, imported, and exercised from an explicit
product boundary without depending on source or packages belonging exclusively to the
legacy AIOps Runtime.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT / "config" / "agent-review" / "standalone-distribution-manifest.v1.json"
)


SUPPORTED_SCHEMA_IDS = frozenset({"agent-review.standalone-distribution-manifest.v1"})
REQUIRED_INSTALL_BOUNDARY_V1 = frozenset(
    {
        "requirements-agent-review.lock",
        "scripts/install-agent-review-toolrepo.sh",
        "docs/AGENT_REVIEW_V2_INSTALLATION.md",
    }
)


class StandaloneClosureValidationError(Exception):
    """Raised when the distribution manifest or AST import closure fails validation."""


def load_manifest(manifest_path: Path | None = None) -> dict[str, Any]:
    path = manifest_path or DEFAULT_MANIFEST_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Distribution manifest not found at: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest at {path} must be a JSON object")
    return data


def derive_ast_imports_from_file(
    file_path: Path,
) -> tuple[set[str], set[str]]:
    """Parse a Python source file and return (external_packages, internal_app_imports)."""
    text = file_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(file_path))
    external_pkgs: set[str] = set()
    app_imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith("app"):
                    app_imports.add(name)
                else:
                    external_pkgs.add(name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("app"):
                app_imports.add(mod)
            elif mod:
                external_pkgs.add(mod.split(".")[0])

    return external_pkgs, app_imports


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path | None = None,
) -> list[str]:
    """Validate distribution boundary declarations and AST import closure against the repo."""
    root = repo_root or REPO_ROOT
    errors: list[str] = []

    # 0. Version / schema enforcement (fail-closed on unsupported versions)
    schema_id = manifest.get("schema_id")
    if schema_id not in SUPPORTED_SCHEMA_IDS:
        errors.append(
            f"Unsupported or missing schema_id: {schema_id!r}. Supported: {sorted(SUPPORTED_SCHEMA_IDS)}"
        )
        return errors

    manifest_version = manifest.get("manifest_version", "")
    if not isinstance(manifest_version, str) or not manifest_version.startswith("1."):
        errors.append(
            f"Unsupported or missing manifest_version: {manifest_version!r}. Supported: 1.x"
        )
        return errors

    # 1. Structural schema requirements
    dist_boundary = manifest.get("distribution_boundary")
    if not isinstance(dist_boundary, dict):
        return ["Manifest is missing required 'distribution_boundary' dictionary."]

    # Enforce minimum required install contract anchors for v1
    install_boundary_entries = set(dist_boundary.get("install_boundary", []))
    missing_install_anchors = sorted(REQUIRED_INSTALL_BOUNDARY_V1 - install_boundary_entries)
    if missing_install_anchors:
        errors.append(
            f"Required install contract artifact(s) omitted from install_boundary: {missing_install_anchors}"
        )

    forbidden_surfaces = set(manifest.get("forbidden_runtime_surfaces", []))
    dep_closure = manifest.get("dependency_closure", {})
    forbidden_packages = {p.lower() for p in dep_closure.get("forbidden_runtime_packages", [])}
    allowed_packages = {p.lower() for p in dep_closure.get("allowed_third_party_packages", [])}

    # 2. Check for existence and runtime surface leaks in declared files
    declared_paths: list[str] = []
    for section_name in (
        "core_packages",
        "package_roots",
        "shared_primitives",
        "required_asset_trees",
        "install_boundary",
        "distribution_clis",
    ):
        items = dist_boundary.get(section_name, [])
        if not isinstance(items, list):
            errors.append(f"Section '{section_name}' in distribution_boundary must be a list.")
            continue
        for rel_path in items:
            declared_paths.append(rel_path)
            full_path = root / rel_path
            if not full_path.exists():
                errors.append(f"Declared distribution path does not exist: {rel_path}")

            # Check if any forbidden runtime surface is declared
            for forbidden in forbidden_surfaces:
                if rel_path == forbidden or rel_path.startswith(forbidden.rstrip("/") + "/"):
                    errors.append(
                        f"Forbidden runtime surface declared in distribution_boundary: {rel_path} matches {forbidden}"
                    )

    # 3. Gather all Python files in the distribution boundary
    distribution_py_files: list[Path] = []
    for rel_path in declared_paths:
        full_path = root / rel_path
        if full_path.is_file() and full_path.suffix == ".py":
            distribution_py_files.append(full_path)
        elif full_path.is_dir():
            for py_path in full_path.rglob("*.py"):
                if "__pycache__" not in py_path.parts:
                    distribution_py_files.append(py_path)

    # 4. AST import audit on all distribution Python files
    allowed_local_modules = {
        "app",
        "app.agent_review",
        "app.common",
        "app.common.strict_json",
        "app.services",
        "app.services.environment_context",
    }

    # Also allow internal submodules within app.agent_review
    stdlib_top_levels = {
        "__future__", "argparse", "array", "ast", "asyncio", "base64", "builtins",
        "collections", "contextlib", "contextvars", "copy", "ctypes", "dataclasses",
        "datetime", "enum", "errno", "fcntl", "fnmatch", "functools", "glob",
        "gzip", "hashlib", "http", "importlib", "inspect", "io", "itertools",
        "json", "logging", "math", "multiprocessing", "operator", "os", "pathlib",
        "platform", "pwd", "queue", "re", "resource", "secrets", "select",
        "shutil", "signal", "socket", "stat", "string", "subprocess", "sys",
        "tarfile", "tempfile", "threading", "time", "token", "tokenize", "traceback",
        "types", "typing", "unicodedata", "unittest", "urllib", "uuid", "warnings",
        "weakref", "zipfile", "zlib",
        # Internal modules invoked within sandbox without package prefix
        "trusted_check_namespace_kernel_v2",
        "trusted_check_stream_capture_v2",
        "trusted_check_supervisor_v2",
    }

    for py_file in distribution_py_files:
        try:
            ext_pkgs, local_app_imports = derive_ast_imports_from_file(py_file)
        except Exception as exc:
            errors.append(f"Failed to parse AST of {py_file.relative_to(root)}: {exc}")
            continue

        # Check external packages
        for pkg in ext_pkgs:
            pkg_lower = pkg.lower()
            if pkg_lower in forbidden_packages:
                errors.append(
                    f"Forbidden runtime package '{pkg}' imported by {py_file.relative_to(root)}"
                )
            elif pkg not in stdlib_top_levels and pkg_lower not in allowed_packages:
                # Check normalized (e.g. pyyaml -> yaml)
                if pkg_lower == "yaml" and "pyyaml" in allowed_packages:
                    continue
                errors.append(
                    f"Unallowed third-party package '{pkg}' imported by {py_file.relative_to(root)}"
                )

        # Check local app imports
        for mod in local_app_imports:
            # Check forbidden runtime prefixes
            is_forbidden = any(
                mod == fb.replace("/", ".") or mod.startswith(fb.replace("/", ".") + ".")
                for fb in forbidden_surfaces
                if fb.startswith("app/")
            )
            if is_forbidden:
                errors.append(
                    f"Forbidden runtime module '{mod}' imported by {py_file.relative_to(root)}"
                )

            # Check that mod is within allowed local modules or app.agent_review.*
            if not (mod.startswith("app.agent_review") or mod in allowed_local_modules):
                errors.append(
                    f"Local module '{mod}' imported by {py_file.relative_to(root)} is outside declared boundary"
                )

    return errors


def materialize_standalone_distribution(
    repo_root: Path,
    target_dir: Path,
    manifest: dict[str, Any] | None = None,
) -> Path:
    """Materialize strictly the declared standalone AgentReview distribution into target_dir.

    No files outside the declared boundary are copied. Any attempt to copy a forbidden
    surface raises StandaloneClosureValidationError.
    """
    manifest_data = manifest or load_manifest()
    validation_errors = validate_manifest(manifest_data, repo_root=repo_root)
    if validation_errors:
        raise StandaloneClosureValidationError(
            f"Cannot materialize invalid distribution:\n" + "\n".join(validation_errors)
        )

    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    dist_boundary = manifest_data["distribution_boundary"]
    forbidden_surfaces = set(manifest_data.get("forbidden_runtime_surfaces", []))

    for section_name in (
        "core_packages",
        "package_roots",
        "shared_primitives",
        "required_asset_trees",
        "install_boundary",
        "distribution_clis",
    ):
        items = dist_boundary.get(section_name, [])
        for rel_path_str in items:
            rel_path = Path(rel_path_str)
            src_path = repo_root / rel_path
            dest_path = target_dir / rel_path

            # Guard against copying forbidden surfaces
            for forbidden in forbidden_surfaces:
                if rel_path_str == forbidden or rel_path_str.startswith(forbidden.rstrip("/") + "/"):
                    raise StandaloneClosureValidationError(
                        f"Refusing to materialize forbidden surface: {rel_path_str}"
                    )

            if src_path.is_file():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_path)
            elif src_path.is_dir():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_path,
                    dest_path,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                )

    return target_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify or materialize the standalone AgentReview distribution closure."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to standalone distribution manifest JSON",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the manifest and repository closure without materializing",
    )
    parser.add_argument(
        "--materialize-to",
        type=Path,
        default=None,
        help="Target directory to materialize the standalone distribution into",
    )

    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except Exception as exc:
        print(f"Error loading manifest {args.manifest}: {exc}", file=sys.stderr)
        return 1

    errors = validate_manifest(manifest, repo_root=REPO_ROOT)
    if errors:
        print(f"FAILED: Distribution closure validation found {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("OK: Standalone distribution boundary and AST import closure are valid.")

    if args.materialize_to:
        try:
            dest = materialize_standalone_distribution(
                repo_root=REPO_ROOT,
                target_dir=args.materialize_to,
                manifest=manifest,
            )
            print(f"OK: Materialized standalone distribution to: {dest}")
        except Exception as exc:
            print(f"FAILED to materialize distribution: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
