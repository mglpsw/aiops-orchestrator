#!/usr/bin/env python3
"""Deterministic validator and materializer for AgentReview standalone distribution (#351-B0).

Implements Layer S (Static Boundary Contract) and Layer M (Safe Materializer).
Executable isolation is proven by Layer E test suite, and the offline installation
contract is proven by Layer I (requirements-agent-review.lock + toolrepo install tests).

Explicit Non-Claims:
  B0-N1: Universal static proof of every possible dynamic/latent Python import is NOT claimed.
  B0-N2: Universal static mapping between AST import names and PyPI distributions is NOT claimed.
  B0-N3: Standalone wheel/package packaging is deferred to subsequent slice (#351-B1).
  B0-N4: Proof of every latent unexecuted execution path is NOT claimed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT / "config" / "agent-review" / "standalone-distribution-manifest.v1.json"
)


SUPPORTED_SCHEMA_IDS = frozenset({"agent-review.standalone-distribution-manifest.v1"})

REQUIRED_DISTRIBUTION_CLIS_V1: frozenset[str] = frozenset(
    {
        "scripts/agent-review-target-pack-v2.py",
        "scripts/aiops-acquire-authoritative-checks-v2.py",
        "scripts/aiops-review-build-payload-set-v2.py",
        "scripts/aiops-review-build-payloads.py",
        "scripts/aiops-review-false-positives.py",
        "scripts/aiops-review-intake.py",
        "scripts/aiops-review-parse-chunks.py",
        "scripts/aiops-review-plan-chunks.py",
        "scripts/aiops-review-quality-gate-v2.py",
        "scripts/aiops-review-quality-gate.py",
        "scripts/aiops-review-synthesize.py",
        "scripts/aiops-review-telemetry.py",
        "scripts/export-agent-review-v2-schemas.py",
        "scripts/github_agent_review.py",
        "scripts/migrate-agent-review-profile-v1-v2.py",
        "scripts/verify-agent-review-v2-conformance.py",
    }
)

REQUIRED_CORE_PACKAGES_V1: frozenset[str] = frozenset({"app/agent_review"})
REQUIRED_PACKAGE_ROOTS_V1: frozenset[str] = frozenset({"app/__init__.py"})
REQUIRED_SHARED_PRIMITIVES_V1: frozenset[str] = frozenset(
    {
        "app/common/strict_json.py",
        "app/common/__init__.py",
        "app/services/environment_context.py",
        "app/services/__init__.py",
    }
)
REQUIRED_ASSET_TREES_V1: frozenset[str] = frozenset(
    {
        "templates/agentreview-v2-target-pack",
        "schemas/agent-review/v2",
    }
)
REQUIRED_INSTALL_BOUNDARY_V1: frozenset[str] = frozenset(
    {
        "requirements-agent-review.lock",
        "scripts/install-agent-review-toolrepo.sh",
        "docs/AGENT_REVIEW_V2_INSTALLATION.md",
    }
)

REQUIRED_BOUNDARY_ANCHORS_V1: dict[str, frozenset[str]] = {
    "core_packages": REQUIRED_CORE_PACKAGES_V1,
    "package_roots": REQUIRED_PACKAGE_ROOTS_V1,
    "shared_primitives": REQUIRED_SHARED_PRIMITIVES_V1,
    "required_asset_trees": REQUIRED_ASSET_TREES_V1,
    "install_boundary": REQUIRED_INSTALL_BOUNDARY_V1,
    "distribution_clis": REQUIRED_DISTRIBUTION_CLIS_V1,
}

REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1: frozenset[str] = frozenset(
    {
        "app/main.py",
        "app/api",
        "app/agent_router",
        "app/models",
        "app/policies",
        "app/adapters",
        "app/utils",
        "app/services/orchestrator.py",
        "app/services/provider_registry.py",
        "app/services/task_service.py",
        "app/services/action_planner.py",
        "app/services/action_catalog.py",
        "app/services/aiops_chat_router.py",
        "app/caem_consumer",
        "app/ri_b0a",
        "app/projectops",
        "deploy",
        "config/actions.yaml",
        "config/policies.yml",
        "config/providers.yml",
        "config/routes.yml",
        "scripts/aiops-runtime-backup-manifest.py",
        "scripts/aiops-runtime-inventory.py",
        "scripts/aiops-runtime-postcheck.py",
        "scripts/backup.sh",
        "scripts/rollback.sh",
        "scripts/install.sh",
        "scripts/smoke_test.sh",
        "scripts/validate_actions_catalog.sh",
        "scripts/validate_bluegreen.sh",
        "scripts/compare_aiops_runtimes.sh",
        "scripts/migrate_savings_to_sqlite.py",
    }
)

KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1: dict[str, frozenset[str]] = {
    "aiosqlite": frozenset({"aiosqlite"}),
    "asyncpg": frozenset({"asyncpg"}),
    "duckduckgo-search": frozenset({"duckduckgo_search"}),
    "fastapi": frozenset({"fastapi"}),
    "httpx": frozenset({"httpx"}),
    "psycopg": frozenset({"psycopg"}),
    "psycopg2": frozenset({"psycopg2"}),
    "pydantic-settings": frozenset({"pydantic_settings"}),
    "python-multipart": frozenset({"multipart", "python_multipart"}),
    "sqlalchemy": frozenset({"sqlalchemy"}),
    "starlette": frozenset({"starlette"}),
    "uvicorn": frozenset({"uvicorn"}),
}

REQUIRED_FORBIDDEN_RUNTIME_PACKAGES_V1: frozenset[str] = frozenset(
    KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1.keys()
)

REQUIRED_FORBIDDEN_RUNTIME_IMPORT_ROOTS_V1: frozenset[str] = frozenset(
    root
    for roots in KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1.values()
    for root in roots
)

REQUIRED_FORBIDDEN_RUNTIME_SCRIPT_IMPORT_ROOTS_V1: frozenset[str] = frozenset(
    {
        "migrate_savings_to_sqlite",
        "compare_aiops_runtimes",
    }
)

KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1: dict[str, frozenset[str]] = {
    "annotated-types": frozenset({"annotated_types"}),
    "pydantic": frozenset({"pydantic"}),
    "pydantic-core": frozenset({"pydantic_core"}),
    "pyyaml": frozenset({"yaml"}),
    "typing-extensions": frozenset({"typing_extensions"}),
    "typing-inspection": frozenset({"typing_inspection"}),
}

REQUIRED_ALLOWED_THIRD_PARTY_PACKAGES_V1: frozenset[str] = frozenset(
    KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1.keys()
)

REQUIRED_ALLOWED_THIRD_PARTY_IMPORT_ROOTS_V1: frozenset[str] = frozenset(
    root
    for roots in KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1.values()
    for root in roots
)

STDLIB_TOP_LEVELS_CPYTHON_311_V1: frozenset[str] = frozenset(
    {
        "__future__", "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
        "asyncore", "base64", "bdb", "binascii", "bisect", "builtins", "bz2",
        "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
        "codeop", "collections", "colorsys", "compileall", "concurrent", "configparser",
        "contextlib", "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
        "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
        "dis", "distutils", "doctest", "email", "encodings", "ensurepip", "enum",
        "errno", "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions",
        "ftplib", "functools", "gc", "getopt", "getpass", "gettext", "glob", "graphlib",
        "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib",
        "imghdr", "imp", "importlib", "inspect", "io", "ipaddress", "itertools", "json",
        "keyword", "lib2to3", "linecache", "locale", "logging", "lzma", "mailbox",
        "mailcap", "marshal", "math", "mimetypes", "mmap", "modulefinder", "msilib",
        "msvcrt", "multiprocessing", "netrc", "nis", "nntplib", "numbers", "operator",
        "optparse", "os", "ossaudiodev", "pathlib", "pdb", "pickle", "pickletools",
        "pipes", "pkgutil", "platform", "plistlib", "poplib", "posix", "posixpath",
        "pprint", "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
        "queue", "quopri", "random", "re", "readline", "reprlib", "resource", "rlcompleter",
        "runpy", "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil",
        "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
        "spwd", "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl", "stat",
        "statistics", "string", "stringprep", "struct", "subprocess", "sunau", "symtable",
        "sys", "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile",
        "termios", "test", "textwrap", "threading", "time", "timeit", "tkinter",
        "token", "tokenize", "tomllib", "trace", "traceback", "tracemalloc", "tty",
        "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib",
        "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser", "winreg",
        "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
        "zipimport", "zlib", "zoneinfo",
        # Internal modules invoked within sandbox without package prefix
        "trusted_check_namespace_kernel_v2",
        "trusted_check_stream_capture_v2",
        "trusted_check_supervisor_v2",
    }
)
STDLIB_TOP_LEVELS_V1 = STDLIB_TOP_LEVELS_CPYTHON_311_V1


def canonical_distribution_name(name: str) -> str:
    """Normalize distribution package name according to PEP 503 / Python packaging conventions."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock_distributions(lock_path: Path) -> frozenset[str]:
    """Parse requirement names from requirements-agent-review.lock and return canonical distribution names."""
    if not lock_path.is_file():
        return frozenset()
    pkgs: set[str] = set()
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*==\s*", line)
        if m:
            pkgs.add(canonical_distribution_name(m.group(1)))
    return frozenset(pkgs)


@dataclass(frozen=True)
class CanonicalStandaloneContractV1:
    """The single canonical code-owned product contract for AgentReview standalone distribution."""

    manifest_version: str = "1.0.0"
    schema_id: str = "agent-review.standalone-distribution-manifest.v1"
    authority_effect: str = "projection_only"
    core_packages: frozenset[str] = REQUIRED_CORE_PACKAGES_V1
    package_roots: frozenset[str] = REQUIRED_PACKAGE_ROOTS_V1
    shared_primitives: frozenset[str] = REQUIRED_SHARED_PRIMITIVES_V1
    required_asset_trees: frozenset[str] = REQUIRED_ASSET_TREES_V1
    install_boundary: frozenset[str] = REQUIRED_INSTALL_BOUNDARY_V1
    distribution_clis: frozenset[str] = REQUIRED_DISTRIBUTION_CLIS_V1
    forbidden_runtime_surfaces: frozenset[str] = REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1
    forbidden_runtime_packages: frozenset[str] = REQUIRED_FORBIDDEN_RUNTIME_PACKAGES_V1
    allowed_third_party_packages: frozenset[str] = REQUIRED_ALLOWED_THIRD_PARTY_PACKAGES_V1
    known_allowed_dependencies: dict[str, frozenset[str]] = field(
        default_factory=lambda: dict(KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1)
    )
    known_forbidden_dependencies: dict[str, frozenset[str]] = field(
        default_factory=lambda: dict(KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1)
    )
    forbidden_script_roots: frozenset[str] = REQUIRED_FORBIDDEN_RUNTIME_SCRIPT_IMPORT_ROOTS_V1
    stdlib_top_levels: frozenset[str] = STDLIB_TOP_LEVELS_CPYTHON_311_V1


CANONICAL_CONTRACT_V1 = CanonicalStandaloneContractV1()

# Disjointness invariants
_allowed_dist_norm = frozenset(canonical_distribution_name(p) for p in REQUIRED_ALLOWED_THIRD_PARTY_PACKAGES_V1)
_forbidden_dist_norm = frozenset(canonical_distribution_name(p) for p in REQUIRED_FORBIDDEN_RUNTIME_PACKAGES_V1)
assert _allowed_dist_norm.isdisjoint(_forbidden_dist_norm), "Allowed packages overlap forbidden runtime packages"
assert REQUIRED_ALLOWED_THIRD_PARTY_IMPORT_ROOTS_V1.isdisjoint(
    REQUIRED_FORBIDDEN_RUNTIME_IMPORT_ROOTS_V1
), "Allowed import roots overlap forbidden runtime import roots"
assert REQUIRED_ALLOWED_THIRD_PARTY_IMPORT_ROOTS_V1.isdisjoint(
    REQUIRED_FORBIDDEN_RUNTIME_SCRIPT_IMPORT_ROOTS_V1
), "Allowed import roots overlap forbidden script import roots"


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


def module_is_same_or_descendant(module: str, forbidden_root: str) -> bool:
    """Return True if module equals forbidden_root or is a submodule of it (non-recursive import semantics)."""
    return module == forbidden_root or module.startswith(forbidden_root + ".")


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
    source_text: str | None = None,
) -> tuple[set[str], set[str]]:
    """Parse a Python source file and return (external_packages, internal_app_imports)."""
    text = source_text if source_text is not None else file_path.read_text(encoding="utf-8")
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
                root_pkg = name.split(".")[0]
                if root_pkg == "app":
                    app_imports.add(name)
                elif root_pkg == "scripts":
                    external_pkgs.add("scripts")
                    parts = name.split(".")
                    if len(parts) > 1:
                        external_pkgs.add(parts[1])
                else:
                    external_pkgs.add(root_pkg)
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

            mod_root = mod.split(".")[0] if mod else ""
            if mod_root == "app":
                app_imports.add(mod)
                for alias in node.names:
                    if alias.name == "*":
                        app_imports.add(f"{mod}.*")
                    else:
                        candidate = f"{mod}.{alias.name}" if mod else alias.name
                        # Check whether candidate is a submodule / package on disk or forbidden surface
                        is_submodule = False

                        for fb in forbidden:
                            if fb.startswith("app/"):
                                fb_mod = fb.removesuffix(".py").replace("/", ".")
                                if module_is_same_or_descendant(candidate, fb_mod):
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
            elif mod_root == "scripts":
                external_pkgs.add("scripts")
                parts = mod.split(".")
                if len(parts) > 1:
                    external_pkgs.add(parts[1])
                for alias in node.names:
                    external_pkgs.add(alias.name)
            elif mod_root:
                external_pkgs.add(mod_root)

    return external_pkgs, app_imports


def extract_git_commit_tree_snapshot(
    repo_root: Path,
    commit_sha: str,
    target_dir: Path,
    all_declared_items: list[str],
    forbidden_surfaces: set[str],
) -> list[str]:
    """Extract strictly the declared standalone items from a Git commit tree blob database into target_dir.

    Scans and validates all records in the commit tree before writing any file.
    Rejects symlinks (120000), gitlinks (160000), forbidden surfaces, and non-regular modes fail-closed.
    Returns list of extracted relative path strings.
    """
    repo_resolved = repo_root.resolve()
    proc_rev = subprocess.run(
        ["git", "-C", str(repo_resolved), "rev-parse", "--verify", commit_sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_rev.returncode != 0:
        raise StandaloneClosureValidationError(
            f"Failed to resolve git commit {commit_sha} in {repo_root}: {proc_rev.stderr.strip()}"
        )
    resolved_commit = proc_rev.stdout.strip()

    proc_tree = subprocess.run(
        ["git", "-C", str(repo_resolved), "ls-tree", "-r", "-z", resolved_commit],
        capture_output=True,
        check=False,
    )
    if proc_tree.returncode != 0:
        raise StandaloneClosureValidationError(
            f"Failed to read git commit tree at {resolved_commit} in {repo_root}: {proc_tree.stderr.decode('utf-8', errors='replace')}"
        )
    records = proc_tree.stdout.split(b"\0")
    git_declared_records: list[tuple[str, str, str]] = []
    for rec in records:
        if not rec:
            continue
        try:
            meta, path_bytes = rec.split(b"\t", 1)
            meta_str = meta.decode("utf-8")
            mode_str, type_str, object_sha = meta_str.split()
            path_str = path_bytes.decode("utf-8")
        except Exception as exc:
            raise StandaloneClosureValidationError(f"Malformed git ls-tree record: {rec!r}: {exc}")

        is_declared = any(
            paths_overlap(path_str, decl)
            for decl in all_declared_items
        )
        if not is_declared:
            continue

        for fb in forbidden_surfaces:
            if paths_overlap(path_str, fb):
                raise StandaloneClosureValidationError(
                    f"Refusing to materialize forbidden surface in git tree: {path_str} overlaps {fb}"
                )

        if mode_str == "120000":
            raise StandaloneClosureValidationError(
                f"Refusing to materialize symlink in git tree: {path_str}"
            )
        if mode_str == "160000":
            raise StandaloneClosureValidationError(
                f"Refusing to materialize gitlink/submodule in git tree: {path_str}"
            )
        if mode_str not in ("100644", "100755"):
            raise StandaloneClosureValidationError(
                f"Unsupported file mode in git tree: {mode_str} for {path_str}"
            )

        git_declared_records.append((mode_str, object_sha, path_str))

    copied_paths: list[str] = []
    if git_declared_records:
        batch_input = b"".join(f"{obj_sha}\n".encode("ascii") for _, obj_sha, _ in git_declared_records)
        proc_batch = subprocess.run(
            ["git", "-C", str(repo_resolved), "cat-file", "--batch"],
            input=batch_input,
            capture_output=True,
            check=False,
        )
        if proc_batch.returncode != 0:
            raise StandaloneClosureValidationError(
                f"Failed to read blobs from git object database in {repo_root}: {proc_batch.stderr.decode('utf-8', errors='replace')}"
            )
        data = proc_batch.stdout
        idx = 0
        for mode_str, object_sha, path_str in git_declared_records:
            header_end = data.find(b"\n", idx)
            if header_end == -1:
                raise StandaloneClosureValidationError(
                    f"Unexpected EOF reading git batch header for {path_str} ({object_sha})"
                )
            header = data[idx:header_end].decode("ascii")
            parts = header.split()
            if len(parts) < 3 or parts[1] != "blob":
                raise StandaloneClosureValidationError(
                    f"Git object {object_sha} for {path_str} is missing or not a blob: {header}"
                )
            size = int(parts[2])
            content_start = header_end + 1
            content = data[content_start:content_start + size]
            idx = content_start + size + 1

            dest_path = target_dir / path_str
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content)
            if mode_str == "100755":
                dest_path.chmod(0o755)
            else:
                dest_path.chmod(0o644)
            copied_paths.append(path_str)

    return copied_paths


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path | None = None,
    git_commit: str | None = None,
) -> list[str]:
    """Validate distribution boundary declarations and AST import closure against the repo."""
    root = repo_root or REPO_ROOT
    if git_commit is not None:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                dist_boundary = manifest.get("distribution_boundary", {}) if isinstance(manifest, dict) else {}
                all_declared_items: list[str] = []
                for s in (
                    "core_packages",
                    "package_roots",
                    "shared_primitives",
                    "required_asset_trees",
                    "install_boundary",
                    "distribution_clis",
                ):
                    items = dist_boundary.get(s, [])
                    if isinstance(items, list):
                        all_declared_items.extend(items)
                forbidden_surfaces = set(manifest.get("forbidden_runtime_surfaces", [])) if isinstance(manifest, dict) else set()
                extract_git_commit_tree_snapshot(
                    repo_root=root,
                    commit_sha=git_commit,
                    target_dir=temp_path,
                    all_declared_items=all_declared_items,
                    forbidden_surfaces=forbidden_surfaces,
                )
                return validate_manifest(manifest, repo_root=temp_path)
        except Exception as exc:
            return [f"Failed to extract and validate git commit tree at {git_commit}: {exc}"]

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

    provenance = manifest.get("provenance")
    if isinstance(provenance, dict):
        auth_effect = provenance.get("authority_effect")
        if auth_effect is not None and auth_effect != CANONICAL_CONTRACT_V1.authority_effect:
            errors.append(
                f"Unsupported provenance authority_effect: {auth_effect!r}. Expected: {CANONICAL_CONTRACT_V1.authority_effect!r}"
            )

    # 1. Structural schema requirements and non-vacuity enforcement
    dist_boundary = manifest.get("distribution_boundary")
    if not isinstance(dist_boundary, dict):
        return ["Manifest is missing required 'distribution_boundary' dictionary."]

    extra_sections = sorted(set(dist_boundary.keys()) - set(REQUIRED_BOUNDARY_ANCHORS_V1.keys()))
    if extra_sections:
        errors.append(f"Undeclared section(s) in distribution_boundary violating canonical contract: {extra_sections}")

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
        entries_set = set(entries)
        missing_anchors = sorted(required_anchors - entries_set)
        if missing_anchors:
            errors.append(
                f"Required anchor(s) omitted from '{section_name}': {missing_anchors}"
            )
        extra_entries = sorted(entries_set - required_anchors)
        if extra_entries:
            errors.append(
                f"Undeclared entry(ies) in '{section_name}' violating canonical boundary: {extra_entries}"
            )

    # 2. Negative boundary contract enforcement (Layer S)
    if "forbidden_runtime_surfaces" not in manifest:
        errors.append("Manifest is missing required 'forbidden_runtime_surfaces' list.")
        return errors
    raw_forbidden = manifest.get("forbidden_runtime_surfaces")
    if not isinstance(raw_forbidden, list):
        errors.append("Field 'forbidden_runtime_surfaces' must be a list.")
        return errors
    if not raw_forbidden:
        errors.append("Field 'forbidden_runtime_surfaces' cannot be empty.")
        return errors

    forbidden_surfaces = set(raw_forbidden)
    missing_negative_anchors = sorted(REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1 - forbidden_surfaces)
    if missing_negative_anchors:
        errors.append(
            f"Required negative runtime anchor(s) omitted from 'forbidden_runtime_surfaces': {missing_negative_anchors}"
        )

    # 2b. Runtime package and dependency closure contract enforcement (Layer S)
    if "dependency_closure" not in manifest or not isinstance(manifest.get("dependency_closure"), dict):
        errors.append("Manifest is missing required 'dependency_closure' dictionary.")
        return errors
    dep_closure = manifest["dependency_closure"]
    if "forbidden_runtime_packages" not in dep_closure:
        errors.append("Field 'dependency_closure' is missing required 'forbidden_runtime_packages' list.")
        return errors
    raw_forbidden_pkgs = dep_closure.get("forbidden_runtime_packages")
    if not isinstance(raw_forbidden_pkgs, list):
        errors.append("Field 'forbidden_runtime_packages' must be a list.")
        return errors
    if not raw_forbidden_pkgs:
        errors.append("Field 'forbidden_runtime_packages' cannot be empty.")
        return errors
    forbidden_packages_norm = {canonical_distribution_name(p) for p in raw_forbidden_pkgs if isinstance(p, str)}
    missing_negative_pkg_anchors = sorted(_forbidden_dist_norm - forbidden_packages_norm)
    if missing_negative_pkg_anchors:
        errors.append(
            f"Required negative runtime package anchor(s) omitted from 'forbidden_runtime_packages': {missing_negative_pkg_anchors}"
        )

    # Validate allowed third party packages contract (B1)
    if "allowed_third_party_packages" not in dep_closure:
        errors.append("Field 'dependency_closure' is missing required 'allowed_third_party_packages' list.")
        return errors
    raw_allowed_pkgs = dep_closure.get("allowed_third_party_packages")
    if not isinstance(raw_allowed_pkgs, list):
        errors.append("Field 'allowed_third_party_packages' must be a list.")
        return errors
    if not raw_allowed_pkgs:
        errors.append("Field 'allowed_third_party_packages' cannot be empty.")
        return errors
    allowed_packages_norm = {canonical_distribution_name(p) for p in raw_allowed_pkgs if isinstance(p, str)}
    missing_allowed_pkg_anchors = sorted(_allowed_dist_norm - allowed_packages_norm)
    if missing_allowed_pkg_anchors:
        errors.append(
            f"Required allowed third-party package anchor(s) omitted from 'allowed_third_party_packages': {missing_allowed_pkg_anchors}"
        )
    extra_allowed_pkgs = sorted(allowed_packages_norm - _allowed_dist_norm)
    if extra_allowed_pkgs:
        errors.append(
            f"Undeclared package(s) in 'allowed_third_party_packages' violating canonical boundary: {extra_allowed_pkgs}"
        )

    # Finding D2: Disjointness check between allowed and forbidden packages
    if not allowed_packages_norm.isdisjoint(forbidden_packages_norm):
        overlap = sorted(allowed_packages_norm & forbidden_packages_norm)
        errors.append(
            f"Allowed third-party packages overlap forbidden runtime packages: {overlap}"
        )

    # Finding D3 / P2: Parity check between allowed_third_party_packages and requirements-agent-review.lock
    lock_file = root / "requirements-agent-review.lock"
    if lock_file.is_file():
        lock_pkgs = parse_lock_distributions(lock_file)
        if not lock_pkgs:
            errors.append(
                f"requirements-agent-review.lock at {lock_file} is empty or contains no valid distribution pins."
            )
        else:
            missing_from_lock = sorted(allowed_packages_norm - lock_pkgs)
            if missing_from_lock:
                errors.append(
                    f"Allowed package(s) declared in manifest but absent from requirements-agent-review.lock: {missing_from_lock}"
                )
            extra_in_lock = sorted(lock_pkgs - allowed_packages_norm)
            if extra_in_lock:
                errors.append(
                    f"Package(s) present in requirements-agent-review.lock but omitted from manifest allowed_third_party_packages: {extra_in_lock}"
                )

    # Project declared allowed packages to import roots via known contract
    allowed_third_party_import_roots: set[str] = set(REQUIRED_ALLOWED_THIRD_PARTY_IMPORT_ROOTS_V1)
    for pkg in raw_allowed_pkgs:
        pkg_norm = canonical_distribution_name(pkg)
        if pkg_norm in KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1:
            allowed_third_party_import_roots.update(KNOWN_ALLOWED_THIRD_PARTY_DEPENDENCIES_V1[pkg_norm])

    # Project declared forbidden packages to import roots via known contract
    forbidden_import_roots: set[str] = set(REQUIRED_FORBIDDEN_RUNTIME_IMPORT_ROOTS_V1)
    for pkg in raw_forbidden_pkgs:
        pkg_norm = canonical_distribution_name(pkg)
        if pkg_norm in KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1:
            forbidden_import_roots.update(KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1[pkg_norm])

    # Disjointness check between allowed and forbidden import roots
    if not allowed_third_party_import_roots.isdisjoint(forbidden_import_roots):
        overlap_roots = sorted(allowed_third_party_import_roots & forbidden_import_roots)
        errors.append(
            f"Allowed import roots overlap forbidden runtime import roots: {overlap_roots}"
        )

    # 3. Check for existence, path confinement and runtime surface leaks in declared files
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

            # Check if any forbidden runtime surface is declared
            for forbidden in forbidden_surfaces:
                if paths_overlap(rel_path_str, forbidden):
                    errors.append(
                        f"Forbidden runtime surface declared in distribution_boundary: {rel_path_str} overlaps {forbidden}"
                    )

            declared_paths.append(rel_path_str)
            full_path = root / rel_path

            if not full_path.exists():
                errors.append(f"Declared distribution path does not exist: {rel_path_str}")
                continue

            # Symlinks not permitted in distribution boundary or its ancestor path (Layer S/M fail-closed policy)
            has_symlink_component = False
            curr = root
            for part in rel_path.parts:
                curr = curr / part
                if curr.is_symlink():
                    errors.append(
                        f"Symlinks not permitted in distribution boundary or ancestor path: {curr.relative_to(root).as_posix()} in {rel_path_str}"
                    )
                    has_symlink_component = True
                    break
            if has_symlink_component:
                continue

            # Enforce expected path kinds for each section (trees as directories, others as regular files)
            if section_name in ("core_packages", "required_asset_trees"):
                if not full_path.is_dir():
                    errors.append(
                        f"Declared path in '{section_name}' must be a directory: {rel_path_str}"
                    )
                elif section_name == "core_packages" and not (full_path / "__init__.py").is_file():
                    errors.append(
                        f"Core package directory '{rel_path_str}' must contain __init__.py"
                    )
            elif section_name in ("package_roots", "shared_primitives", "install_boundary", "distribution_clis"):
                if not full_path.is_file():
                    errors.append(
                        f"Declared path in '{section_name}' must be a regular file: {rel_path_str}"
                    )

            # Resolved-source confinement check (prevent repository root escape)
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

            # If directory, ensure no contained item is a symlink or special file
            if full_path.is_dir():
                for sub in full_path.rglob("*"):
                    if sub.is_symlink():
                        errors.append(
                            f"Symlink found in declared distribution tree '{rel_path_str}': {sub.relative_to(root)}"
                        )
                    elif not (sub.is_file() or sub.is_dir()):
                        errors.append(
                            f"Special file (FIFO, socket, device) not permitted in distribution boundary '{rel_path_str}': {sub.relative_to(root)}"
                        )

    # Physical admission must precede content/AST reads (A4). If any physical error
    # was encountered (missing files, symlinks, FIFOs, special files, escape), fail immediately.
    if errors:
        return errors

    # 3. Gather all Python files in the distribution boundary (admitted regular files only)
    distribution_py_files: list[Path] = []
    for rel_path_str in declared_paths:
        full_path = root / rel_path_str
        if full_path.is_file() and not full_path.is_symlink() and full_path.suffix == ".py":
            distribution_py_files.append(full_path)
        elif full_path.is_dir() and not full_path.is_symlink():
            for py_path in sorted(full_path.rglob("*.py")):
                if "__pycache__" not in py_path.parts and py_path.is_file() and not py_path.is_symlink():
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

    forbidden_script_roots = set(REQUIRED_FORBIDDEN_RUNTIME_SCRIPT_IMPORT_ROOTS_V1)
    for fb in forbidden_surfaces:
        fb_path = Path(fb)
        if fb_path.suffix == ".py" and fb_path.stem.isidentifier() and not fb.startswith("app/"):
            forbidden_script_roots.add(fb_path.stem)

    for py_file in distribution_py_files:
        try:
            ext_pkgs, local_app_imports = derive_ast_imports_from_file(
                py_file, repo_root=root, forbidden_surfaces=forbidden_surfaces
            )
        except Exception as exc:
            errors.append(f"Failed to parse AST of {py_file.relative_to(root)}: {exc}")
            continue

        # Check external packages against positive third-party closure (B1) - Exact case matching!
        for pkg in ext_pkgs:
            if pkg in STDLIB_TOP_LEVELS_V1:
                continue
            elif pkg in allowed_third_party_import_roots:
                continue
            elif pkg in forbidden_import_roots:
                errors.append(
                    f"Forbidden runtime package '{pkg}' imported by {py_file.relative_to(root)}"
                )
            elif pkg in forbidden_script_roots:
                errors.append(
                    f"Forbidden runtime script import root '{pkg}' imported by {py_file.relative_to(root)}"
                )
            else:
                errors.append(
                    f"Undeclared external package '{pkg}' imported by {py_file.relative_to(root)}: "
                    f"package is neither stdlib nor in allowed_third_party_packages"
                )

        # Check local app imports
        for mod in local_app_imports:
            if mod.endswith(".*"):
                errors.append(
                    f"Ambiguous internal star import '{mod}' in {py_file.relative_to(root)}: star imports from internal modules are forbidden"
                )
                continue

            # Check forbidden runtime prefixes using module semantics (exact or descendant)
            is_forbidden = any(
                module_is_same_or_descendant(mod, fb.removesuffix(".py").replace("/", "."))
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
    source_sha: str | None = None,
) -> Path:
    """Materialize strictly the declared standalone AgentReview distribution into target_dir.

    No files outside the declared boundary are copied. Any attempt to copy a forbidden
    surface raises StandaloneClosureValidationError.
    """
    manifest_data = load_manifest() if manifest is None else manifest

    repo_resolved = repo_root.resolve()
    target_resolved = target_dir.resolve()

    # Resolve verifiable source identity to preserve in the materialized distribution (P-09, P-14, A2)
    resolved_sha: str | None = None
    is_git_repo = False

    # 1. Detect usable Git identity first (Git HEAD wins whenever Git is available)
    if not repo_root.is_symlink():
        try:
            proc_top = subprocess.run(
                ["git", "-C", str(repo_resolved), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc_top.returncode == 0 and Path(proc_top.stdout.strip()).resolve() == repo_resolved:
                proc_head = subprocess.run(
                    ["git", "-C", str(repo_resolved), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc_head.returncode == 0:
                    cand = proc_head.stdout.strip().lower()
                    if re.fullmatch(r"^[0-9a-f]{40}$", cand) and cand != "0" * 40:
                        resolved_sha = cand
                        is_git_repo = True
        except Exception:
            pass

    src_commit_file = repo_root / ".source-commit"
    toolrepo_sha_file = repo_root / ".toolrepo-sha"

    if src_commit_file.is_symlink():
        raise StandaloneClosureValidationError(
            f"Attestation file .source-commit in {repo_root} cannot be a symlink"
        )
    if toolrepo_sha_file.is_symlink():
        raise StandaloneClosureValidationError(
            f"Attestation file .toolrepo-sha in {repo_root} cannot be a symlink"
        )

    commit_file_sha: str | None = None
    if src_commit_file.is_file():
        c = src_commit_file.read_text(encoding="utf-8").strip().lower()
        if not re.fullmatch(r"^[0-9a-f]{40}$", c) or c == "0" * 40:
            raise StandaloneClosureValidationError(
                f"Invalid .source-commit format in {repo_root}: must be 40 hex chars non-zero"
            )
        commit_file_sha = c

    toolrepo_file_sha: str | None = None
    if toolrepo_sha_file.is_file():
        c = toolrepo_sha_file.read_text(encoding="utf-8").strip().lower()
        if not re.fullmatch(r"^[0-9a-f]{40}$", c) or c == "0" * 40:
            raise StandaloneClosureValidationError(
                f"Invalid .toolrepo-sha format in {repo_root}: must be 40 hex chars non-zero"
            )
        toolrepo_file_sha = c

    if is_git_repo:
        # Git HEAD is authoritative. Any present attestation files must match it!
        if commit_file_sha is not None and commit_file_sha != resolved_sha:
            raise StandaloneClosureValidationError(
                f"Attestation file .source-commit ({commit_file_sha}) disagrees with authoritative Git HEAD ({resolved_sha})"
            )
        if toolrepo_file_sha is not None and toolrepo_file_sha != resolved_sha:
            raise StandaloneClosureValidationError(
                f"Attestation file .toolrepo-sha ({toolrepo_file_sha}) disagrees with authoritative Git HEAD ({resolved_sha})"
            )
    else:
        # Standalone non-git source directory: validate manifest on repo_root before checking attestations
        validation_errors = validate_manifest(manifest_data, repo_root=repo_root)
        if validation_errors:
            raise StandaloneClosureValidationError(
                f"Cannot materialize invalid distribution:\n" + "\n".join(validation_errors)
            )

        # Only when Git identity is genuinely unavailable: use standalone attestation files
        if commit_file_sha is not None and toolrepo_file_sha is not None:
            if commit_file_sha != toolrepo_file_sha:
                raise StandaloneClosureValidationError(
                    f"Conflicting standalone attestations: .source-commit ({commit_file_sha}) != .toolrepo-sha ({toolrepo_file_sha})"
                )
            resolved_sha = commit_file_sha
        elif commit_file_sha is not None:
            resolved_sha = commit_file_sha
        elif toolrepo_file_sha is not None:
            resolved_sha = toolrepo_file_sha
        else:
            raise StandaloneClosureValidationError(
                f"Cannot determine verifiable source identity from {repo_root}: not a git repository and no source attestation found"
            )

    # Require explicit source_sha to match independently resolved identity (P-14)
    if source_sha is not None:
        cand_explicit = source_sha.strip().lower()
        if not re.fullmatch(r"^[0-9a-f]{40}$", cand_explicit) or cand_explicit == "0" * 40:
            raise StandaloneClosureValidationError(
                f"Explicit source_sha must be a full 40-character non-zero lowercase hex commit SHA, got: {source_sha!r}"
            )
        if cand_explicit != resolved_sha:
            raise StandaloneClosureValidationError(
                f"Explicit source_sha '{cand_explicit}' does not match independently resolved source identity '{resolved_sha}' from {repo_root}"
            )
        attested_sha = cand_explicit
    else:
        attested_sha = resolved_sha

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

    forbidden_surfaces = set(manifest_data.get("forbidden_runtime_surfaces", []))

    # Target directory safety checks
    if target_dir.is_symlink() or target_resolved.is_symlink():
        raise StandaloneClosureValidationError(
            f"Materialization target directory cannot be a symlink: {target_dir}"
        )

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

    if target_resolved.exists():
        if not target_resolved.is_dir():
            raise StandaloneClosureValidationError(
                f"Materialization target exists and is not a directory: {target_resolved}"
            )
        existing_items = list(target_resolved.iterdir())
        if existing_items:
            raise StandaloneClosureValidationError(
                f"Materialization target directory must be empty or absent, but contains {len(existing_items)} existing item(s): {target_resolved}"
            )

    copied_manifest_paths: list[str] = []

    if is_git_repo:
        # Layer M: Extract strictly from authoritative immutable Git commit tree
        with tempfile.TemporaryDirectory() as temp_snapshot_dir:
            temp_snapshot = Path(temp_snapshot_dir)
            extract_git_commit_tree_snapshot(
                repo_root=repo_resolved,
                commit_sha=resolved_sha,
                target_dir=temp_snapshot,
                all_declared_items=all_declared_items,
                forbidden_surfaces=forbidden_surfaces,
            )

            # Validate the immutable Git commit tree snapshot against the product contract
            snapshot_errors = validate_manifest(manifest_data, repo_root=temp_snapshot)
            if snapshot_errors:
                raise StandaloneClosureValidationError(
                    f"Cannot materialize invalid distribution from git commit tree:\n"
                    + "\n".join(snapshot_errors)
                )

            # Now that complete validation succeeded, create target directory and populate
            try:
                target_resolved.mkdir(parents=True, exist_ok=True)
                for item in sorted(temp_snapshot.rglob("*")):
                    if item.is_file():
                        rel = item.relative_to(temp_snapshot)
                        dest = target_resolved / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dest)
                        copied_manifest_paths.append(rel.as_posix())

                # Write verifiable source identity attestation files (P-09)
                (target_resolved / ".source-commit").write_text(f"{attested_sha}\n", encoding="utf-8")
                (target_resolved / ".toolrepo-sha").write_text(f"{attested_sha}\n", encoding="utf-8")

                # Output closure check: verify every materialized file belongs to the declared boundary or attestation metadata
                MATERIALIZED_ATTESTATION_FILES = frozenset({".source-commit", ".toolrepo-sha"})
                for item in target_resolved.rglob("*"):
                    if item.is_file():
                        rel_to_target = item.relative_to(target_resolved).as_posix()
                        if rel_to_target in MATERIALIZED_ATTESTATION_FILES:
                            continue
                        is_declared = any(
                            paths_overlap(rel_to_target, decl)
                            for decl in copied_manifest_paths
                        )
                        if not is_declared:
                            raise StandaloneClosureValidationError(
                                f"Output closure violation: Undeclared file found in materialized target: {rel_to_target}"
                            )

                # Post-materialization closure verification: ensure the target itself passes validation
                post_errors = validate_manifest(manifest_data, repo_root=target_resolved)
                if post_errors:
                    raise StandaloneClosureValidationError(
                        f"Materialized distribution failed validation:\n" + "\n".join(post_errors)
                    )
            except Exception:
                shutil.rmtree(target_resolved, ignore_errors=True)
                raise
    else:
        # Pre-creation scan: verify all declared source items are regular files or directories without symlinks or special files (P-13)
        for rel_path_str in all_declared_items:
            rel_path = admit_manifest_relative_path_v1(rel_path_str)
            src_path = repo_root / rel_path
            if src_path.is_symlink():
                raise StandaloneClosureValidationError(
                    f"Refusing to materialize symlink: {rel_path_str}"
                )
            if src_path.is_dir():
                for sub in src_path.rglob("*"):
                    if sub.is_symlink():
                        raise StandaloneClosureValidationError(
                            f"Refusing to materialize distribution tree containing symlink: {sub.relative_to(repo_root)}"
                        )
                    if not (sub.is_file() or sub.is_dir()):
                        raise StandaloneClosureValidationError(
                            f"Special file (FIFO, socket, device) not permitted in distribution boundary: {sub.relative_to(repo_root)}"
                        )
            elif not src_path.is_file():
                raise StandaloneClosureValidationError(
                    f"Declared distribution item must be a regular file or directory: {rel_path_str}"
                )

        try:
            target_resolved.mkdir(parents=True, exist_ok=True)
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
                    dest_path = target_resolved / rel_path

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

                    # Check destination confinement against target_resolved
                    resolved_dest = dest_path.resolve()
                    if not (resolved_dest == target_resolved or resolved_dest.is_relative_to(target_resolved)):
                        raise StandaloneClosureValidationError(
                            f"Refusing to materialize escaping destination path: {rel_path_str} -> {resolved_dest}"
                        )

                    # Symlinks not permitted in declared distribution members or ancestor paths
                    curr = repo_root
                    for part in rel_path.parts:
                        curr = curr / part
                        if curr.is_symlink():
                            raise StandaloneClosureValidationError(
                                f"Refusing to materialize distribution member with symlink component: {curr.relative_to(repo_root).as_posix()} in {rel_path_str}"
                            )

                    if src_path.is_file():
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, dest_path)
                        copied_manifest_paths.append(rel_path_str)
                    elif src_path.is_dir():
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(
                            src_path,
                            dest_path,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                        )
                        copied_manifest_paths.append(rel_path_str)

            # Write verifiable source identity attestation files (P-09)
            (target_resolved / ".source-commit").write_text(f"{attested_sha}\n", encoding="utf-8")
            (target_resolved / ".toolrepo-sha").write_text(f"{attested_sha}\n", encoding="utf-8")

            # Output closure check: verify every materialized file belongs to the declared boundary or attestation metadata
            MATERIALIZED_ATTESTATION_FILES = frozenset({".source-commit", ".toolrepo-sha"})
            for item in target_resolved.rglob("*"):
                if item.is_file():
                    rel_to_target = item.relative_to(target_resolved).as_posix()
                    if rel_to_target in MATERIALIZED_ATTESTATION_FILES:
                        continue
                    is_declared = any(
                        paths_overlap(rel_to_target, decl)
                        for decl in copied_manifest_paths
                    )
                    if not is_declared:
                        raise StandaloneClosureValidationError(
                            f"Output closure violation: Undeclared file found in materialized target: {rel_to_target}"
                        )

            # Post-materialization closure verification: ensure the target itself passes validation
            post_errors = validate_manifest(manifest_data, repo_root=target_resolved)
            if post_errors:
                raise StandaloneClosureValidationError(
                    f"Materialized distribution failed validation:\n" + "\n".join(post_errors)
                )
        except Exception:
            shutil.rmtree(target_resolved, ignore_errors=True)
            raise

    return target_resolved


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
    parser.add_argument(
        "--source-sha",
        type=str,
        default=None,
        help="Optional full 40-character lowercase commit SHA to preserve as verifiable source identity",
    )

    args = parser.parse_args(argv)

    if args.check and args.materialize_to:
        parser.error(
            "argument --check: not allowed with argument --materialize-to (validation and dry-run are strictly write-zero)"
        )

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

    print("OK: Layer S (Static Boundary Contract) and Layer M (Materialization Contract) are valid.")
    print("NOTE: Executable isolation evidence is certified by Layer E test suite.")

    if args.materialize_to:
        try:
            dest = materialize_standalone_distribution(
                repo_root=REPO_ROOT,
                target_dir=args.materialize_to,
                manifest=manifest,
                source_sha=args.source_sha,
            )
            print(f"OK: Materialized standalone distribution to: {dest}")
        except Exception as exc:
            print(f"FAILED to materialize distribution: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
