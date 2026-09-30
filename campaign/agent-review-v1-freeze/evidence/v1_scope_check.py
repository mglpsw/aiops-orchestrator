"""V1 freeze campaign: executable per-slice isolation check (02 slice_gates.v1_v2_isolation).

Read-only. Two checks, run on a slice's live base/head:

1. reach (ADVISORY): scans every tracked .py/.sh/.yml file outside v1_exclusive
   (shared_v1_owned files included).
   Blocking = a static import (absolute, relative, or bare `from . import x`) of a
   `v1_exclusive` module. Notes = non-docstring string constants naming a v1_exclusive
   file or its `app.agent_review.<stem>` module path (possible load-by-path, dynamic
   import or subprocess); every note must be adjudicated in the slice PR. It cannot
   detect computed/dynamic imports or subprocess targets built at runtime: it is a
   review aid, not a proof of isolation.
2. diff (with --diff BASE..HEAD): every changed path is in v1_path_set, or is a new
   `tests/agent_review/test_*.py` / `campaign/agent-review-v1-freeze/**` file.
   `conftest.py`, `__init__.py` and anything under a `fixtures/` directory are never
   admitted as new files.

Usage (from inside the slice worktree):
    python campaign/agent-review-v1-freeze/evidence/v1_scope_check.py            # reach (advisory)
  Binding diff gate -- ALWAYS run the BASE revision's copy, never the checked-out one,
  so a slice cannot edit the checker to disable its own guard:
    git show <base>:campaign/agent-review-v1-freeze/evidence/v1_scope_check.py > /tmp/v1_scope_check_base.py
    python /tmp/v1_scope_check_base.py --diff <base>..<head>
    git diff --quiet <base> <head> -- campaign/agent-review-v1-freeze/02_OBLIGATION_MATRIX.json \
        campaign/agent-review-v1-freeze/evidence/v1_scope_check.py   # independent of the checker
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

# The repository is the git toplevel of the CURRENT DIRECTORY, not of this file, so
# the gate can execute the immutable BASE copy of this checker (see Usage) against
# the slice worktree.
ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True).stdout.strip())
MATRIX = ROOT / "campaign" / "agent-review-v1-freeze" / "02_OBLIGATION_MATRIX.json"


MATRIX_REL = "campaign/agent-review-v1-freeze/02_OBLIGATION_MATRIX.json"
SELF_REL = "campaign/agent-review-v1-freeze/evidence/v1_scope_check.py"


def _load_scope(revision: str | None = None) -> tuple[list[str], list[str]]:
    """Load v1_path_set. With --diff, it is read from the immutable BASE revision so a
    slice cannot widen its own scope in the same change it is checked against."""
    if revision:
        shown = subprocess.run(["git", "show", f"{revision}:{MATRIX_REL}"], cwd=ROOT, capture_output=True, text=True)
        if shown.returncode != 0:
            raise SystemExit(f"v1_scope_check: no isolation policy at base {revision} (only the contract slice that introduces it may lack one)")
        raw = shown.stdout
    else:
        raw = MATRIX.read_text(encoding="utf-8")
    gate = json.loads(raw)["slice_gates"]["v1_v2_isolation"]["positive_write_scope"]
    path_set = gate["v1_path_set"]
    return path_set["v1_exclusive"], path_set["shared_v1_owned"]



def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def check_reach(v1_exclusive: list[str], shared_paths: list[str]) -> list[str]:
    """Blocking: any tracked file outside v1_exclusive (shared_v1_owned included)
    statically imports a v1_exclusive module. Note (must be adjudicated in
    the PR): a v2 file holds a non-docstring string constant naming a v1_exclusive file
    (possible load-by-path or subprocess), or a v2 YAML/shell file names one."""
    concrete = [entry for entry in v1_exclusive if "*" not in entry]
    modules = {Path(e).stem: e for e in concrete if e.startswith("app/agent_review/") and e.endswith(".py")}
    names = {Path(e).name: e for e in concrete}
    findings: list[str] = []
    # shared_v1_owned files ARE scanned: a shared module importing a v1_exclusive
    # module would give v2 a static path into it (v2 -> contracts_v2 -> redaction -> ...).
    in_scope = set(concrete)
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.sh", "*.yml", "*.yaml"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    for rel in tracked:
        if rel in in_scope or rel.startswith("campaign/"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if not rel.endswith(".py"):
            for name, entry in names.items():
                if name in text:
                    findings.append(f"note: {rel} names v1_exclusive {entry} (non-Python; adjudicate)")
            for stem, entry in modules.items():
                if re.search(rf"app\.agent_review\.{stem}\b", text):
                    findings.append(f"note: {rel} names module of v1_exclusive {entry} (e.g. python -m / -c; adjudicate)")
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            findings.append(f"note: {rel} not parseable; adjudicate manually")
            continue
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"app.agent_review.{base}" if base else "app.agent_review"
                targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
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
                for stem, entry in modules.items():
                    if f"app.agent_review.{stem}" in node.value:
                        findings.append(f"note: {rel}:{node.lineno} string constant names module of v1_exclusive {entry} (possible dynamic import; adjudicate)")
    return findings


def _admissible_new_file(path: str) -> bool:
    name = Path(path).name
    if name in {"conftest.py", "__init__.py"} or "/fixtures/" in f"/{path}":
        return False
    pure = Path(path)
    if pure.parent.as_posix() == "tests/agent_review":
        return fnmatch.fnmatch(pure.name, "test_*.py")
    return path.startswith("campaign/agent-review-v1-freeze/")


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
    for row in rows:
        for path in row.split("\t")[1:]:
            if path in {MATRIX_REL, SELF_REL}:
                findings.append(f"diff: {path} changed; the isolation policy/checker may change only in a dedicated contract slice (owner decision), never in a behavioral slice")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff")
    args = parser.parse_args()
    v1_exclusive, shared = _load_scope()
    findings = check_reach(v1_exclusive, shared)
    if args.diff:
        base_exclusive, base_shared = _load_scope(args.diff.split("..", 1)[0])
        findings += check_diff(args.diff, base_exclusive, base_shared)
    blocking = [item for item in findings if not item.startswith("note:")]
    for item in findings:
        print(item)
    print(f"v1_scope_check: {len(blocking)} blocking, {len(findings) - len(blocking)} notes")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
