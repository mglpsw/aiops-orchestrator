"""V1 freeze campaign: executable per-slice isolation check (02 slice_gates.v1_v2_isolation).

Read-only. Two checks, run on a slice's live base/head:

1. reach: no v2 file imports, loads by path, or names any `v1_exclusive` entry of
   v1_path_set. A hit means that entry is really shared and must be reclassified
   (in 02) before the slice patches it.
2. diff (with --diff BASE..HEAD): every changed path is in v1_path_set, or is a new
   `tests/agent_review/test_*.py` / `campaign/agent-review-v1-freeze/**` file.
   `conftest.py`, `__init__.py` and anything under a `fixtures/` directory are never
   admitted as new files.

Usage (repo root):
    python campaign/agent-review-v1-freeze/evidence/v1_scope_check.py
    python campaign/agent-review-v1-freeze/evidence/v1_scope_check.py --diff <base>..<head>
Exit 0 = clean, 1 = findings printed.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
MATRIX = ROOT / "campaign" / "agent-review-v1-freeze" / "02_OBLIGATION_MATRIX.json"
V2_IMPORT = re.compile(r"(?:from|import)\s+(?:app\.agent_review\.\w+_v2\b|app\.agent_review\s+import\s+[^\n]*\b\w+_v2\b|evals\.agent_review_v2)")


def _load_scope() -> tuple[list[str], list[str]]:
    gate = json.loads(MATRIX.read_text(encoding="utf-8"))["slice_gates"]["v1_v2_isolation"]["positive_write_scope"]
    path_set = gate["v1_path_set"]
    return path_set["v1_exclusive"], path_set["shared_v1_owned"]


def _is_v2_file(rel: str, text: str) -> bool:
    return "v2" in Path(rel).name or "/v2/" in f"/{rel}" or "agent_review_v2" in rel or bool(V2_IMPORT.search(text))


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def check_reach(v1_exclusive: list[str]) -> list[str]:
    """Blocking: a v2 file IMPORTS a v1_exclusive module. Note (must be adjudicated in
    the PR): a v2 file holds a non-docstring string constant naming a v1_exclusive file
    (possible load-by-path or subprocess), or a v2 YAML/shell file names one."""
    concrete = [entry for entry in v1_exclusive if "*" not in entry]
    modules = {Path(e).stem: e for e in concrete if e.startswith("app/agent_review/") and e.endswith(".py")}
    names = {Path(e).name: e for e in concrete}
    findings: list[str] = []
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.sh", "*.yml", "*.yaml"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    for rel in tracked:
        if rel in concrete or rel.startswith("campaign/"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if not _is_v2_file(rel, text):
            continue
        if not rel.endswith(".py"):
            for name, entry in names.items():
                if name in text:
                    findings.append(f"note: {rel} names v1_exclusive {entry} (non-Python; adjudicate)")
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            findings.append(f"note: {rel} not parseable; adjudicate manually")
            continue
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
                if node.level:
                    targets = [f"app.agent_review.{node.module}"] + [f"app.agent_review.{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                targets = []
            for target in targets:
                parts = target.split(".")
                if len(parts) >= 3 and parts[:2] == ["app", "agent_review"] and parts[2] in modules:
                    findings.append(f"reach: {rel}:{node.lineno} imports v1_exclusive {modules[parts[2]]}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                for name, entry in names.items():
                    if name in node.value:
                        findings.append(f"note: {rel}:{node.lineno} string constant names v1_exclusive {entry} (possible path load; adjudicate)")
    return findings


def _admissible_new_file(path: str) -> bool:
    name = Path(path).name
    if name in {"conftest.py", "__init__.py"} or "/fixtures/" in f"/{path}":
        return False
    return fnmatch.fnmatch(path, "tests/agent_review/test_*.py") or path.startswith("campaign/agent-review-v1-freeze/")


def check_diff(spec: str, v1_exclusive: list[str], shared: list[str]) -> list[str]:
    base, head = spec.split("..", 1)
    rows = subprocess.run(["git", "diff", "--name-status", base, head], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    allowed = set(v1_exclusive) | set(shared)
    globs = [entry for entry in allowed if "*" in entry]
    findings: list[str] = []
    for row in rows:
        status, *paths = row.split("\t")
        for path in paths:
            in_set = path in allowed or any(fnmatch.fnmatch(path, g) for g in globs)
            if in_set:
                if path in shared:
                    findings.append(f"note: shared_v1_owned path changed, v2-unchanged justification required: {path}")
                continue
            if status.startswith("A") and _admissible_new_file(path):
                continue
            findings.append(f"diff: {status} {path} is outside v1_path_set")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff")
    args = parser.parse_args()
    v1_exclusive, shared = _load_scope()
    findings = check_reach(v1_exclusive)
    if args.diff:
        findings += check_diff(args.diff, v1_exclusive, shared)
    blocking = [item for item in findings if not item.startswith("note:")]
    for item in findings:
        print(item)
    print(f"v1_scope_check: {len(blocking)} blocking, {len(findings) - len(blocking)} notes")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
