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

REQUIRED_BOUNDARY_ANCHORS_V1: dict[str, frozenset[str]] = {
    "core_packages": frozenset({"app/agent_review"}),
    "package_roots": frozenset({"app/__init__.py"}),
    "shared_primitives": frozenset(
        {
            "app/common/strict_json.py",
            "app/services/environment_context.py",
        }
    ),
    "required_asset_trees": frozenset(
        {
            "templates/agentreview-v2-target-pack",
            "schemas/agent-review/v2",
        }
    ),
    "install_boundary": frozenset(
        {
            "requirements-agent-review.lock",
            "scripts/install-agent-review-toolrepo.sh",
            "docs/AGENT_REVIEW_V2_INSTALLATION.md",
        }
    ),
    "distribution_clis": frozenset(
        {
            "scripts/agent-review-target-pack-v2.py",
            "scripts/aiops-review-intake.py",
            "scripts/aiops-review-quality-gate-v2.py",
            "scripts/aiops-review-synthesize.py",
            "scripts/github_agent_review.py",
        }
    ),
}

REQUIRED_INSTALL_BOUNDARY_V1 = REQUIRED_BOUNDARY_ANCHORS_V1["install_boundary"]


def admit_manifest_relative_path_v1(rel_path_str: str) -> Path:
    """Validate that rel_path_str is a canonical POSIX repository-relative path without escape or traversal."""
    if not isinstance(rel_path_str, str):
        raise ValueError(f"Path must be a string, got {type(rel_path_str).__name__}")
    if not rel_path_str:
        raise ValueError("Path cannot be empty")
    if "\\" in rel_path_str:
        raise ValueError(f"Path contains backslash separator: {rel_path_str!r}")
    if rel_path_str.strip() != rel_path_str:
        raise ValueError(f"Path contains leading or trailing whitespace: {rel_path_str!r}")
    if rel_path_str.startswith("/"):
        raise ValueError(f"Absolute paths not permitted in distribution boundary: {rel_path_str!r}")

    parts = rel_path_str.split("/")
    for part in parts:
        if part == "":
            raise ValueError(f"Non-canonical empty path component in: {rel_path_str!r}")
        if part == ".":
            raise ValueError(f"Current-directory '.' component not permitted in path: {rel_path_str!r}")
        if part == "..":
            raise ValueError(f"Parent-traversal '..' component not permitted in path: {rel_path_str!r}")

    return Path(rel_path_str)


def paths_overlap(path_a: str, path_b: str) -> bool:
    """Return True if path_a and path_b are identical or one is an ancestor/descendant of the other."""
    parts_a = [p for p in path_a.strip("/").split("/") if p and p != "."]
    parts_b = [p for p in path_b.strip("/").split("/") if p and p != "."]
    min_len = min(len(parts_a), len(parts_b))
    return bool(parts_a and parts_b and parts_a[:min_len] == parts_b[:min_len])


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
    repo_root: Path | None = None,
    forbidden_surfaces: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Parse a Python source file and return (external_packages, internal_app_imports)."""
    text = file_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(file_path))
    external_pkgs: set[str] = set()
    app_imports: set[str] = set()

    root = repo_root or REPO_ROOT
    forbidden = forbidden_surfaces or set()

    # Determine relative path of file_path to root to establish package hierarchy
    root_resolved = root.resolve()
    try:
        rel_file = file_path.resolve().relative_to(root_resolved)
    except ValueError:
        try:
            rel_file = file_path.relative_to(root)
        except ValueError:
            rel_file = file_path

    if rel_file.is_absolute():
        pkg_parts: list[str] = []
    else:
        pkg_parts = [p for p in rel_file.parent.parts if p and p != "."]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name.startswith("app"):
                    app_imports.add(name)
                else:
                    external_pkgs.add(name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.level > len(pkg_parts):
                    raise ValueError(
                        f"Relative import with level {node.level} escapes top-level package in {rel_file}"
                    )
                steps_up = node.level - 1
                base_parts = pkg_parts[: len(pkg_parts) - steps_up]
                if node.module:
                    mod = ".".join(base_parts + node.module.split("."))
                else:
                    mod = ".".join(base_parts)
            else:
                mod = node.module or ""

            if mod.startswith("app"):
                app_imports.add(mod)
                for alias in node.names:
                    if alias.name == "*":
                        app_imports.add(f"{mod}.*")
                    else:
                        candidate = f"{mod}.{alias.name}" if mod else alias.name
                        # Check whether candidate is a submodule / package on disk or forbidden surface
                        is_submodule = False

                        candidate_slash = candidate.replace(".", "/")
                        for fb in forbidden:
                            fb_clean = fb.removesuffix(".py")
                            if paths_overlap(candidate_slash, fb_clean):
                                is_submodule = True
                                break

                        if not is_submodule:
                            candidate_parts = candidate.split(".")
                            for check_root in {root, REPO_ROOT}:
                                check_file = check_root / Path(*candidate_parts).with_suffix(".py")
                                check_dir = check_root / Path(*candidate_parts)
                                if check_file.is_file() or check_dir.is_dir():
                                    is_submodule = True
                                    break

                        if is_submodule:
                            app_imports.add(candidate)
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

    if not isinstance(manifest, dict):
        return [f"Manifest must be a JSON dictionary object, got {type(manifest).__name__}"]

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

    # 1. Structural schema requirements and non-vacuity enforcement
    dist_boundary = manifest.get("distribution_boundary")
    if not isinstance(dist_boundary, dict):
        return ["Manifest is missing required 'distribution_boundary' dictionary."]

    for section_name, required_anchors in REQUIRED_BOUNDARY_ANCHORS_V1.items():
        if section_name not in dist_boundary:
            errors.append(f"Required boundary section '{section_name}' is missing from distribution_boundary.")
            continue
        entries = dist_boundary.get(section_name)
        if not isinstance(entries, list):
            errors.append(f"Section '{section_name}' in distribution_boundary must be a list.")
            continue
        if not entries:
            errors.append(f"Section '{section_name}' in distribution_boundary cannot be empty.")
            continue
        missing_anchors = sorted(required_anchors - set(entries))
        if missing_anchors:
            errors.append(
                f"Required anchor(s) omitted from '{section_name}': {missing_anchors}"
            )

    forbidden_surfaces = set(manifest.get("forbidden_runtime_surfaces", []))
    dep_closure = manifest.get("dependency_closure", {})
    forbidden_packages = {p.lower() for p in dep_closure.get("forbidden_runtime_packages", [])}
    allowed_packages = {p.lower() for p in dep_closure.get("allowed_third_party_packages", [])}

    # 2. Check for existence, path confinement and runtime surface leaks in declared files
    declared_paths: list[str] = []
    root_resolved = root.resolve()

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
            continue
        for rel_path_str in items:
            try:
                rel_path = admit_manifest_relative_path_v1(rel_path_str)
            except ValueError as exc:
                errors.append(f"Non-canonical or escaping distribution path: {exc}")
                continue

            declared_paths.append(rel_path_str)
            full_path = root / rel_path

            if not full_path.exists():
                errors.append(f"Declared distribution path does not exist: {rel_path_str}")
                continue

            # Resolved-source confinement check (prevent symlink escape)
            try:
                resolved_full = full_path.resolve()
                if not (resolved_full == root_resolved or resolved_full.is_relative_to(root_resolved)):
                    errors.append(
                        f"Resolved source path escapes repository root: {rel_path_str} -> {resolved_full}"
                    )
                    continue
                if resolved_full.is_relative_to(root_resolved):
                    resolved_rel_str = resolved_full.relative_to(root_resolved).as_posix()
                    for forbidden in forbidden_surfaces:
                        if paths_overlap(resolved_rel_str, forbidden):
                            errors.append(
                                f"Resolved source path resolves to forbidden surface: {rel_path_str} -> {resolved_rel_str} overlaps {forbidden}"
                            )
                            break
            except Exception as exc:
                errors.append(f"Failed to resolve path {rel_path_str}: {exc}")
                continue

            # If directory, ensure no contained symlinks escape repository root or leave declared subtree
            if full_path.is_dir():
                for sub in full_path.rglob("*"):
                    try:
                        resolved_sub = sub.resolve()
                        if not (resolved_sub == resolved_full or resolved_sub.is_relative_to(resolved_full)):
                            if not (resolved_sub == root_resolved or resolved_sub.is_relative_to(root_resolved)):
                                errors.append(
                                    f"Symlink in distribution tree escapes repository root: {sub.relative_to(root)} -> {resolved_sub}"
                                )
                            else:
                                errors.append(
                                    f"Symlink or descendant in distribution tree '{rel_path_str}' leaves declared subtree: {sub.relative_to(root)} -> {resolved_sub}"
                                )
                            continue
                        if resolved_sub.is_relative_to(root_resolved):
                            sub_rel_str = resolved_sub.relative_to(root_resolved).as_posix()
                            for forbidden in forbidden_surfaces:
                                if paths_overlap(sub_rel_str, forbidden):
                                    errors.append(
                                        f"Symlink or descendant in distribution tree '{rel_path_str}' resolves to forbidden surface: {sub_rel_str} overlaps {forbidden}"
                                    )
                                    break
                    except Exception as exc:
                        errors.append(f"Failed to resolve item {sub}: {exc}")

            # Check if any forbidden runtime surface is declared
            for forbidden in forbidden_surfaces:
                if paths_overlap(rel_path_str, forbidden):
                    errors.append(
                        f"Forbidden runtime surface declared in distribution_boundary: {rel_path_str} overlaps {forbidden}"
                    )

    # 3. Gather all Python files in the distribution boundary
    distribution_py_files: list[Path] = []
    for rel_path_str in declared_paths:
        full_path = root / rel_path_str
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
            ext_pkgs, local_app_imports = derive_ast_imports_from_file(
                py_file, repo_root=root, forbidden_surfaces=forbidden_surfaces
            )
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
                if pkg_lower == "yaml" and "pyyaml" in allowed_packages:
                    continue
                errors.append(
                    f"Unallowed third-party package '{pkg}' imported by {py_file.relative_to(root)}"
                )

        # Check local app imports
        for mod in local_app_imports:
            if mod.endswith(".*"):
                errors.append(
                    f"Ambiguous internal star import '{mod}' in {py_file.relative_to(root)}: star imports from internal modules are forbidden"
                )
                continue

            # Check forbidden runtime prefixes
            is_forbidden = any(
                paths_overlap(mod.replace(".", "/"), fb.removesuffix(".py"))
                for fb in forbidden_surfaces
                if fb.startswith("app/")
            )
            if is_forbidden:
                errors.append(
                    f"Forbidden runtime module '{mod}' imported by {py_file.relative_to(root)}"
                )

            # Check that mod is within allowed local modules or app.agent_review.*
            is_allowed = (
                mod == "app.agent_review"
                or mod.startswith("app.agent_review.")
                or mod in allowed_local_modules
            )
            if not is_allowed:
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
    manifest_data = load_manifest() if manifest is None else manifest
    validation_errors = validate_manifest(manifest_data, repo_root=repo_root)
    if validation_errors:
        raise StandaloneClosureValidationError(
            f"Cannot materialize invalid distribution:\n" + "\n".join(validation_errors)
        )

    repo_resolved = repo_root.resolve()
    target_resolved = target_dir.resolve()

    dist_boundary = manifest_data.get("distribution_boundary", {})
    all_declared_items: list[str] = []
    for section_name in (
        "core_packages",
        "package_roots",
        "shared_primitives",
        "required_asset_trees",
        "install_boundary",
        "distribution_clis",
    ):
        items = dist_boundary.get(section_name, [])
        if isinstance(items, list):
            all_declared_items.extend(items)

    # Disjointness check between target and declared source paths (run BEFORE creating target)
    for rel_path_str in all_declared_items:
        src_path = repo_root / admit_manifest_relative_path_v1(rel_path_str)
        src_resolved = src_path.resolve()
        if target_resolved == src_resolved:
            raise StandaloneClosureValidationError(
                f"Target directory overlaps declared source path: {target_dir} equals {src_path}"
            )
        if target_resolved.is_relative_to(src_resolved):
            raise StandaloneClosureValidationError(
                f"Target directory is nested inside declared source path: {target_dir} inside {src_path}"
            )
        if src_resolved.is_relative_to(target_resolved):
            raise StandaloneClosureValidationError(
                f"Declared source path is nested inside target directory: {src_path} inside {target_dir}"
            )

    if target_dir.exists():
        if not target_dir.is_dir():
            raise StandaloneClosureValidationError(
                f"Materialization target exists and is not a directory: {target_dir}"
            )
        existing_items = list(target_dir.iterdir())
        if existing_items:
            raise StandaloneClosureValidationError(
                f"Materialization target directory must be empty or absent, but contains {len(existing_items)} existing item(s): {target_dir}"
            )
    else:
        target_dir.mkdir(parents=True, exist_ok=True)

    forbidden_surfaces = set(manifest_data.get("forbidden_runtime_surfaces", []))
    copied_manifest_paths: list[str] = []

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
            rel_path = admit_manifest_relative_path_v1(rel_path_str)
            src_path = repo_root / rel_path
            dest_path = target_dir / rel_path

            # Guard against copying forbidden surfaces
            for forbidden in forbidden_surfaces:
                if paths_overlap(rel_path_str, forbidden):
                    raise StandaloneClosureValidationError(
                        f"Refusing to materialize forbidden surface: {rel_path_str} overlaps {forbidden}"
                    )

            # Check resolved source confinement
            resolved_src = src_path.resolve()
            if not (resolved_src == repo_resolved or resolved_src.is_relative_to(repo_resolved)):
                raise StandaloneClosureValidationError(
                    f"Refusing to materialize escaping source path: {rel_path_str} -> {resolved_src}"
                )

            # Check destination confinement
            resolved_dest = dest_path.resolve()
            if not resolved_dest.is_relative_to(target_dir):
                raise StandaloneClosureValidationError(
                    f"Refusing to materialize escaping destination path: {rel_path_str} -> {resolved_dest}"
                )

            if src_path.is_file():
                if resolved_src.is_relative_to(repo_resolved):
                    src_rel_str = resolved_src.relative_to(repo_resolved).as_posix()
                    for forbidden in forbidden_surfaces:
                        if paths_overlap(src_rel_str, forbidden):
                            raise StandaloneClosureValidationError(
                                f"Refusing to materialize file resolving to forbidden surface: {src_rel_str} overlaps {forbidden}"
                            )
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_path)
                copied_manifest_paths.append(rel_path_str)
            elif src_path.is_dir():
                for sub in src_path.rglob("*"):
                    resolved_sub = sub.resolve()
                    if not (resolved_sub == resolved_src or resolved_sub.is_relative_to(resolved_src)):
                        if not (resolved_sub == repo_resolved or resolved_sub.is_relative_to(repo_resolved)):
                            raise StandaloneClosureValidationError(
                                f"Refusing to materialize tree containing escaping symlink: {sub} -> {resolved_sub} (escapes repository root)"
                            )
                        else:
                            raise StandaloneClosureValidationError(
                                f"Refusing to materialize tree containing escaping symlink: {sub} -> {resolved_sub} (leaves declared subtree {rel_path_str})"
                            )
                    if resolved_sub.is_relative_to(repo_resolved):
                        sub_rel_str = resolved_sub.relative_to(repo_resolved).as_posix()
                        for forbidden in forbidden_surfaces:
                            if paths_overlap(sub_rel_str, forbidden):
                                raise StandaloneClosureValidationError(
                                    f"Refusing to materialize tree containing symlink to forbidden surface: {sub_rel_str} overlaps {forbidden}"
                                )
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_path,
                    dest_path,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                )
                copied_manifest_paths.append(rel_path_str)

    # Output closure check: verify every materialized file belongs to the declared boundary
    for item in target_dir.rglob("*"):
        if item.is_file():
            rel_to_target = item.relative_to(target_dir).as_posix()
            is_declared = any(
                paths_overlap(rel_to_target, decl)
                for decl in copied_manifest_paths
            )
            if not is_declared:
                raise StandaloneClosureValidationError(
                    f"Output closure violation: Undeclared file found in materialized target: {rel_to_target}"
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
