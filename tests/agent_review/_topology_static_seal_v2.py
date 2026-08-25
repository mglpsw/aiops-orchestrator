"""Intermodular static topology-authority seal (`#262`, recurrence #3).

THE single static authority for the topology seal. It supersedes the per-file
marker analyzer that recurrence #3 falsified; there is no parallel heuristic
route left, because two analyzers with overlapping jurisdiction is how the
`app/`-only inventory survived a review that believed it was repository-wide.

WHAT RECURRENCE #3 FALSIFIED.  The previous model asked "which production
DIRECTORY does this file live in?" and answered with one hand-chosen root.  A
list of length one is still a list: `scripts/` was omitted, and
`scripts/agent-review-target-pack-v2.py` imports this very module.  Adding
`scripts` -- or `scripts` and `evals` -- reproduces the defect on the next
surface nobody remembered.  Root SELECTION was the defect, not root count.

WHAT REPLACES IT.  Directory membership decides nothing.  The universe is the
repository's own non-test Python, and a module falls under the seal only when
it is statically BOUND to the topology subject through the import / alias /
re-export graph.  That is why `evals/*/case_sources/**` -- fixture corpora
standing in for other repositories -- need no exclusion rule: they never import
the subject, so they are never bound.  The four real `evals/` modules that DO
import `app` are sealed, as they should be.

BOUND, STATED NOT IMPLIED.  This resolves static imports, aliases, re-exports
(multi-hop), literal attribute names and statically identifiable bindings.  It
does NOT perform whole-Python type inference.  A dynamically computed attribute
name or a dynamic import is an explicit NONCLAIM.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

SUBJECT_MODULE = "app.agent_review.target_pack_epoch_v2"
SUBJECT_CLASS = "MountTopologySnapshotV2"

RAW_PRIMITIVES = frozenset({"_governing_mount_raw_v2", "_is_visible_raw_v2"})
RELEVANCE_INTERNALS = frozenset({"records", "children", "by_id"})

# `tests/` is the one surface with explicit authority to stand outside the
# seal: tests legitimately construct snapshots and exercise internals -- that
# is their job.  Everything else in the repository is in the universe, and
# reachability (not location) decides whether the seal applies.
PRIVILEGED_TOP_LEVEL = frozenset({"tests"})
NON_SOURCE_PARTS = frozenset({
    "__pycache__", ".venv", "venv", "build", "dist", ".git", "node_modules",
})

# Permitted owners are MODULE-qualified (`#262` N21).  A `(class, method)` pair
# is not identity: an unrelated module may define a class of the same name with
# a method of the same name, and it would inherit resolver authority by pure
# name collision.
#
# Both sets are derived from SEMANTIC NECESSITY -- the methods that genuinely
# implement the resolver's own derivation -- not from what happened to be in
# the previous allowlist.  Consumer wrappers were in it (`#262` N22) and are
# not here: `project_v2`, `governing_mount_v2`, `is_visible_v2` and
# `visible_child_mounts_v2` must obtain relevance through the typed resolution
# contract like any other consumer.
PERMITTED_RAW_OWNERS = frozenset({
    # raw visibility is defined in terms of the raw governing walk
    (SUBJECT_MODULE, SUBJECT_CLASS, "_is_visible_raw_v2"),
    # the sole relevance authority; the only thing entitled to drive raw traversal
    (SUBJECT_MODULE, SUBJECT_CLASS, "resolve_query_v2"),
})

PERMITTED_RELEVANCE_OWNERS = frozenset({
    (SUBJECT_MODULE, SUBJECT_CLASS, name) for name in (
        "__init__",                    # builds the indices themselves
        "_semantic_seeds_v2",          # seeds the query frontier
        "_dependency_closure_v2",      # closes it over the parent relation
        "_governing_mount_raw_v2",     # the raw pathname walk
        "_climb_stack_v2",             # same-point stack, graph-private
        "_visible_root_v2",            # top of the mount tree, graph-private
        "validate_relevant_chain_v2",  # chain validation over the frontier
    )
})


class SealNonclaim(Exception):
    """Raised only to make a bound explicit; never used for control flow."""


@dataclass
class ModuleNode:
    path: Path
    identity: str
    # local name -> (defining_module, symbol)  |  (module, None) for module alias
    bindings: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    tree: ast.Module | None = None


def repository_python_universe(root: Path) -> list[Path]:
    """Every repository-owned Python source the seal may judge.

    Derived from the repository root.  A directory that does not exist today is
    covered the moment it appears -- there is nothing to add to.
    """

    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in NON_SOURCE_PARTS for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] in PRIVILEGED_TOP_LEVEL:
            continue
        out.append(path)
    return out


def module_identity(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _collect_bindings(tree: ast.Module, identity: str) -> dict[str, tuple[str, str | None]]:
    bindings: dict[str, tuple[str, str | None]] = {}
    package = identity.rsplit(".", 1)[0] if "." in identity else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else alias.name.split(".")[0]
                bindings[local] = (target, None)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:                      # relative import
                base = package
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0] if "." in base else ""
                module = f"{base}.{module}" if module else base
            for alias in node.names:
                if alias.name == "*":
                    continue                    # star imports: not resolved, nonclaim
                bindings[alias.asname or alias.name] = (module, alias.name)
    return bindings


def build_module_graph(sources: list[Path], root: Path) -> dict[str, ModuleNode]:
    graph: dict[str, ModuleNode] = {}
    for path in sources:
        identity = module_identity(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue                            # unparseable source judges nothing
        node = ModuleNode(path=path, identity=identity, tree=tree)
        node.bindings = _collect_bindings(tree, identity)
        graph[identity] = node
    return graph


def _resolve(graph: dict[str, ModuleNode], module: str, symbol: str,
             seen: frozenset[tuple[str, str]] = frozenset()) -> tuple[str, str]:
    """Follow re-exports to the DEFINING module, multi-hop, cycle-safe."""

    key = (module, symbol)
    if key in seen:
        return key
    node = graph.get(module)
    if node is None:
        return key
    onward = node.bindings.get(symbol)
    if onward is None or onward[1] is None:
        return key
    return _resolve(graph, onward[0], onward[1], seen | {key})


def subject_local_names(graph: dict[str, ModuleNode], node: ModuleNode) -> set[str]:
    """Local names in *node* that statically denote the topology subject."""

    names: set[str] = set()
    for local, (module, symbol) in node.bindings.items():
        if symbol is None:
            continue
        if _resolve(graph, module, symbol) == (SUBJECT_MODULE, SUBJECT_CLASS):
            names.add(local)
    # the defining module refers to the subject by its own class name
    if node.identity == SUBJECT_MODULE:
        names.add(SUBJECT_CLASS)
    return names


def subject_module_aliases(node: ModuleNode) -> set[str]:
    """Local names bound to the subject's MODULE (`import ... as tp`)."""

    return {local for local, (module, symbol) in node.bindings.items()
            if symbol is None and module == SUBJECT_MODULE}


def subject_returning_callables(graph: dict[str, ModuleNode]) -> set[tuple[str, str]]:
    """Callables whose RETURN ANNOTATION denotes the subject.

    A snapshot is normally obtained from a factory -- `observe()`, `parse()` --
    not by calling the class. Without this, `snap = Snapshot.observe()` then
    `snap.records` escapes the seal, and that path is statically expressible,
    so leaving it out would be a hole rather than a bound.

    This reads an EXPLICIT annotation. It is not return-type inference: an
    unannotated factory stays outside the claim.
    """

    factories: set[tuple[str, str]] = set()
    for identity, node in graph.items():
        names = subject_local_names(graph, node)
        aliases = subject_module_aliases(node)

        def denotes(ann: ast.expr | None) -> bool:
            if ann is None:
                return False
            if isinstance(ann, ast.Name):
                return ann.id in names
            if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
                return ann.value in names
            if isinstance(ann, ast.Attribute):
                return (isinstance(ann.value, ast.Name)
                        and ann.value.id in aliases and ann.attr == SUBJECT_CLASS)
            return False

        stack: list[str] = []

        def walk(scope: ast.AST) -> None:
            for child in ast.iter_child_nodes(scope):
                if isinstance(child, ast.ClassDef):
                    stack.append(child.name)
                    walk(child)
                    stack.pop()
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if denotes(child.returns):
                        factories.add((identity, ".".join([*stack, child.name])))
                    stack.append(child.name)
                    walk(child)
                    stack.pop()

        walk(node.tree)
    return factories


@dataclass(frozen=True)
class Offence:
    module: str
    path: str
    owner: tuple[str, str, str]
    kind: str            # "raw" | "relevance"
    name: str
    form: str
    line: int


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, node: ModuleNode, subject_names: set[str],
                 module_aliases: set[str], factories: set[tuple[str, str]],
                 graph: dict[str, ModuleNode]) -> None:
        self.node = node
        self.subject_names = subject_names
        self.module_aliases = module_aliases
        self.factories = factories
        self.graph = graph
        self.cls: str | None = None
        self.fn: str | None = None
        self.bound: set[str] = set()      # locals statically bound to the subject
        self.offences: list[Offence] = []

    # -- owner identity is (module, class, method) ---------------------------
    def _owner(self) -> tuple[str, str, str]:
        return (self.node.identity, self.cls or "<module>", self.fn or "<module>")

    def _record(self, kind: str, name: str, form: str, line: int) -> None:
        owner = self._owner()
        permitted = PERMITTED_RAW_OWNERS if kind == "raw" else PERMITTED_RELEVANCE_OWNERS
        if owner in permitted:
            return
        self.offences.append(Offence(self.node.identity, str(self.node.path),
                                     owner, kind, name, form, line))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        outer, self.cls = self.cls, node.name
        self.generic_visit(node)
        self.cls = outer

    def _visit_function(self, node) -> None:
        outer_fn, self.fn = self.fn, node.name
        outer_bound = set(self.bound)
        # a parameter annotated with a subject-bound name IS the subject
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.annotation is not None and self._annotation_is_subject(arg.annotation):
                self.bound.add(arg.arg)
        # inside the DEFINING class, `self` is the subject
        if self.node.identity == SUBJECT_MODULE and self.cls == SUBJECT_CLASS:
            if args.args:
                self.bound.add(args.args[0].arg)
        self.generic_visit(node)
        self.fn, self.bound = outer_fn, outer_bound

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _annotation_is_subject(self, ann: ast.expr) -> bool:
        if isinstance(ann, ast.Name):
            return ann.id in self.subject_names
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            return ann.value in self.subject_names
        if isinstance(ann, ast.Attribute):
            return (isinstance(ann.value, ast.Name)
                    and ann.value.id in self.module_aliases
                    and ann.attr == SUBJECT_CLASS)
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        # x = Snapshot(...)  /  x = tp.MountTopologySnapshotV2(...)  -> x is subject
        value = node.value
        constructed = (isinstance(value, ast.Call)
                       and (self._annotation_is_subject(value.func)
                            or self._call_returns_subject(value)))
        if constructed:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bound.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation is not None and self._annotation_is_subject(node.annotation):
            if isinstance(node.target, ast.Name):
                self.bound.add(node.target.id)
        self.generic_visit(node)

    def _call_returns_subject(self, call: ast.Call) -> bool:
        """`Subject.observe()` / an imported or local annotated factory."""

        func = call.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in self.subject_names:
                return (SUBJECT_MODULE, f"{SUBJECT_CLASS}.{func.attr}") in self.factories
            if isinstance(func.value, ast.Name) and func.value.id in self.module_aliases:
                return (SUBJECT_MODULE, func.attr) in self.factories
            return False
        if isinstance(func, ast.Name):
            if (self.node.identity, func.id) in self.factories:
                return True
            binding = self.node.bindings.get(func.id)
            if binding and binding[1] is not None:
                return _resolve(self.graph, binding[0], binding[1]) in self.factories
        return False

    def _receiver_is_subject(self, expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in self.bound
        if isinstance(expr, ast.Attribute):
            # tp.MountTopologySnapshotV2._x  -> unbound class access
            return (isinstance(expr.value, ast.Name)
                    and expr.value.id in self.module_aliases
                    and expr.attr == SUBJECT_CLASS)
        return False

    # -- reference forms -----------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        # RAW primitives: the NAMES are private to the subject, so any static
        # reference outside a permitted module-qualified owner is an offence,
        # whatever the receiver -- that is what makes an impostor class fail.
        if node.attr in RAW_PRIMITIVES:
            form = "unbound-alias" if self._receiver_is_subject(node) else "attribute"
            self._record("raw", node.attr, form, node.lineno)
        # RELEVANCE internals: the names are generic, so the receiver must be
        # statically bound to the subject (`#262` N25).
        elif node.attr in RELEVANCE_INTERNALS and self._receiver_is_subject(node.value):
            self._record("relevance", node.attr, "attribute", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # literal getattr, for BOTH classes (`#262` N23)
        if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            name = node.args[1].value
            if name in RAW_PRIMITIVES:
                self._record("raw", name, "literal-getattr", node.lineno)
            elif name in RELEVANCE_INTERNALS and self._receiver_is_subject(node.args[0]):
                self._record("relevance", name, "literal-getattr", node.lineno)
        self.generic_visit(node)


def analyze_module(graph: dict[str, ModuleNode], node: ModuleNode,
                   factories: set[tuple[str, str]] | None = None) -> list[Offence]:
    if factories is None:
        factories = subject_returning_callables(graph)
    visitor = _ModuleVisitor(node, subject_local_names(graph, node),
                             subject_module_aliases(node), factories, graph)
    visitor.visit(node.tree)
    return visitor.offences


def seal_offences(root: Path) -> list[Offence]:
    """THE seal. Derived universe, intermodule bindings, one authority."""

    sources = repository_python_universe(root)
    graph = build_module_graph(sources, root)
    factories = subject_returning_callables(graph)
    offences: list[Offence] = []
    for identity in sorted(graph):
        offences.extend(analyze_module(graph, graph[identity], factories))
    return offences


def bound_module_identities(root: Path) -> set[str]:
    """Modules statically bound to the topology subject. Diagnostic only."""

    sources = repository_python_universe(root)
    graph = build_module_graph(sources, root)
    return {identity for identity, node in graph.items()
            if subject_local_names(graph, node) or subject_module_aliases(node)}
