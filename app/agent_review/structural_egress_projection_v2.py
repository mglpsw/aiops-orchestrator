"""Structural egress closure for AgentReview v2's outbound Router payload
(#200-G2C, issue #299). Successor to #280/#286 (`#200-G2`,
`STOP_G2_SAFE_REVIEW_MATERIAL_NOT_CONVERGING`) and #287/#293 (`#200-G2B`,
`STOP_G2B_ARCHITECTURE_NOT_CONVERGING`) -- both content-classification
attempts over an open, unenumerable domain (arbitrary source text),
independently refuted, by a DIFFERENT mechanism each round.

## Load-bearing property

This module does NOT prove "no sensitive value was found" -- a content
judgement over an open domain, which is exactly what sank G2 (blocklist)
and G2B (allowlist oracle), each in a different way. It proves instead:

    the outbound representation has no structural slot capable of
    carrying ANY raw literal's bytes, regardless of what that literal
    contains.

That is a closure/type proof over Python's own grammar, established once
and independent of what any specific literal looks like. Because the
property is content-agnostic BY DESIGN, it is fully provable with
semantically neutral fixtures -- a value's resemblance to a credential is
irrelevant to whether the projected type can carry it at all.

## Design (Family B from #299's architecture spike)

Source text is parsed with the stdlib ``ast`` module. Every literal
(string/bytes/int/float/complex) reachable from ``ast.Constant.value`` is
unconditionally replaced by an opaque, schema-typed ``ProjectedLiteralV2``
placeholder (``kind``/``length``/``sha256_12``) -- there is no per-value
"redact or not" decision to be wrong about, because the projected type has
no field that could hold a raw value at all. A docstring is an ordinary
``ast.Expr(value=ast.Constant(str))`` under this rule; it needs no special
case and gets none. Comments (``ast`` does not represent them at all) are
collected via ``tokenize`` and projected through the identical
``ProjectedLiteralV2`` shape. ``None``/``True``/``False``/``Ellipsis`` are
NOT projected: as ``ast.Constant`` values they are a closed, finite,
zero-entropy domain (four possible values, carrying no user-controlled
bytes), and opaquing them would destroy real control-flow legibility
(``if x is None:``) for no safety benefit -- a deliberate, stated scope
decision, not an oversight.

Every identifier (``Name.id``, ``arg.arg``, ``FunctionDef.name``,
``Attribute.attr``, ...) is deterministically aliased to a stable, opaque
``sym_NNN`` token under ``EXTERNAL_SAFE_STRUCTURAL`` mode -- the ONLY mode
this module exposes for egress. The same source name always aliases to the
same token within one projection call, so call-graph/data-flow/control-flow
structure survives for review even though no raw identifier byte does. This
module deliberately does NOT ship a raw-identifier-preserving mode; if one
is ever added elsewhere for internal review-quality experiments, it must
carry its own explicit "not egress-eligible" marking -- see the module
docstring of any such future addition. Nothing here claims universal
raw-literal exclusion while leaving a side door for raw identifiers.

## Grammar universe authority (no self-certification)

``AUTHORIZED_AST_NODE_TYPES_V2`` is derived from the RUNNING interpreter's
own ``ast`` module by introspection (every ``ast.AST`` subclass ``ast``
itself exports) -- never a hand-maintained list this module also writes a
handler against, which is the exact circularity #299 names as the trap
("do not let the projector claim these are all the AST node types and
then prove only that it handles exactly those"). Every node visited during
a projection is asserted to be a member of that live-derived set; a node
type outside it is a hard block (``STRUCTURAL_PROJECTION_UNAUTHORIZED_
NODE_TYPE_V2``), never a silent pass-through.

Each FIELD on each node is separately classified by its RUNTIME PYTHON
TYPE (``_classify_leaf_v2``) against a small, closed, exhaustively-listed
set of shapes Python's own ASDL grammar actually produces: a nested AST
node, a list of them, an ``identifier``-typed string, an ``ast.Constant``
scalar, or one of a small, explicitly named set of structural-integer
exceptions -- ``ImportFrom.level`` (relative-import dot count),
``FormattedValue.conversion`` (f-string conversion code, one of a
four-member closed set), ``AnnAssign.simple`` and ``comprehension.
is_async`` (0/1 parser flags). Every one of these was found by
EXHAUSTIVELY scanning this repository's own real source corpus for every
non-``Constant``, non-identifier, non-node leaf shape the CPython 3.11
grammar actually emits (see ``test_structural_egress_projection_v2.py::
test_structural_int_exceptions_are_exhaustive_over_the_real_corpus``) --
not hand-guessed and not claimed complete by assertion. All four are
parser-derived flags/counts, never user-authored text, and structurally
incapable of carrying arbitrary content. Any field shape outside this
enumeration is a hard block
(``STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2``). This makes "every
literal is projected" a completeness property of a total classifying
function, not an audit of which node types someone remembered to handle.

## Scope: Python only, fail closed otherwise

Non-``.py`` paths, and Python text that does not parse (a real risk: real
reviewable content is often a WINDOWED diff-hunk excerpt, not necessarily
a syntactically complete file) are BLOCKED from egress entirely -- never
"best-effort" line-based scanning, which would silently reintroduce G2/
G2B's enumeration failure mode one layer down. A best-effort, FORMAT-bound
(not content-based) transformation is applied first: real hunk bodies
carry a per-line ``' '``/``'+'``/``'-'`` prefix (``diff_acquisition_v2``'s
own convention); when EVERY line of the input carries one of those three
markers, the "new side" is reconstructed (drop ``'-'`` lines, strip the
marker from the rest) before parsing is attempted. That is a structural,
universal deshaping step over the diff-hunk WIRE FORMAT, not a decision
about what any line's content means.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import io
import secrets
import tokenize
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, StrictStr, model_validator

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ContractV2Model,
    CoverageDegradationReasonValue,
    CoverageStateValue,
    PayloadContractReferenceV2,
    SafeIdentifier,
    Sha256,
)

if TYPE_CHECKING:
    from app.agent_review.review_content_v2 import ChunkContentV2


# -- grammar universe authority: derived, never hand-maintained ------------


def _derive_authorized_ast_node_types_v2() -> frozenset[str]:
    """The one and only place this module is allowed to enumerate AST node
    types: read off the RUNNING interpreter's own ``ast`` module. Anything
    calling into the projector with a node type this set does not contain
    is, by construction, a node type this same interpreter's ``ast.parse``
    could not have produced -- so it is a corruption/tamper signal, not a
    normal input, and is hard-blocked accordingly."""

    return frozenset(
        name
        for name, obj in vars(ast).items()
        if isinstance(obj, type) and issubclass(obj, ast.AST)
    )


AUTHORIZED_AST_NODE_TYPES_V2: frozenset[str] = _derive_authorized_ast_node_types_v2()

# The explicitly named, structurally-justified exceptions to "every
# non-Constant leaf is either an AST node or an identifier string": every
# non-Constant, non-identifier scalar leaf the CPython 3.11 ASDL grammar
# actually produces, per an EXHAUSTIVE scan of this repository's own real
# source corpus (``test_structural_egress_projection_v2.py::
# test_structural_int_exceptions_are_exhaustive_over_the_real_corpus``),
# not a hand-guessed list:
#
#   (ImportFrom, level)            relative-import dot count
#   (FormattedValue, conversion)   f-string conversion code: one of
#                                   {-1, 97 ('a'), 114 ('r'), 115 ('s')}
#   (AnnAssign, simple)             0/1 "is a bare name target" parser flag
#   (comprehension, is_async)       0/1 "is `async for`" parser flag
#
# Every one is a parser-derived flag/count, never user-authored text --
# structurally incapable of carrying arbitrary content.
_STRUCTURAL_INT_EXCEPTIONS_V2: frozenset[tuple[str, str]] = frozenset(
    {
        ("ImportFrom", "level"),
        ("FormattedValue", "conversion"),
        ("AnnAssign", "simple"),
        ("comprehension", "is_async"),
    }
)

# -- reason codes ------------------------------------------------------------

STRUCTURAL_PROJECTION_UNSUPPORTED_LANGUAGE_V2 = "structural_projection_unsupported_language"
STRUCTURAL_PROJECTION_PARSE_FAILED_V2 = "structural_projection_parse_failed"
STRUCTURAL_PROJECTION_UNAUTHORIZED_NODE_TYPE_V2 = "structural_projection_unauthorized_node_type"
STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2 = "structural_projection_unsupported_leaf_shape"
STRUCTURAL_PROJECTION_MAX_DEPTH_EXCEEDED_V2 = "structural_projection_max_depth_exceeded"

# Non-bracketed left-recursive AST shapes (attribute chains `a.b.b.b...`,
# chained binary/unary operators `1+1+1+...`/`not not not...`) have no
# CPython parser-level nesting guard -- unlike bracketed literals, which the
# parser itself refuses past ~200 levels. A single ~1-2KB source line can
# produce a several-hundred-level-deep AST. Empirically (this module's own
# regression corpus): unbounded, ``_project_node_v2``'s own Python-level
# recursive descent raises an UNCAUGHT ``RecursionError`` around AST depth
# ~990 (just under the interpreter's default 1000-frame limit, since this
# function's own stack frames compound with everything else already on the
# stack); at a LOWER depth (~250-254 for this exact node/field shape),
# pydantic-core's serializer -- a SEPARATE recursive walk performed by the
# caller's later ``.model_dump(mode="json")``, entirely outside this
# module's own recursion -- hits its own internal recursion-depth safeguard
# first and raises ``ValueError: Circular reference detected (depth
# exceeded)`` even though there is no actual cycle, only depth. Both are
# real, independently reproduced crash vectors, and both sit outside this
# module's own ``try/except StructuralProjectionBlockedV2`` handling as
# originally written -- contradicting this module's own "never a crash,
# always degrade to a controlled block" design contract. Set with a wide
# safety margin under BOTH observed ceilings (roughly 4x under the lower,
# ~250-254 pydantic-core one): no legitimate, human-authored Python source
# in this repository's own corpus comes close to 100 levels of AST nesting.
MAX_STRUCTURAL_PROJECTION_DEPTH_V2 = 100


class StructuralProjectionBlockedV2(Exception):
    """Raised whenever a fragment cannot be PROVEN closed. Carries a stable
    ``reason_code`` only -- never the content that triggered it. The
    correct disposition for a caller is always "do not send this content",
    never "send it anyway, best-effort"."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# -- closed, schema-typed projection shapes ----------------------------------
#
# Every string field below is EITHER a fixed-pattern digest/alias/tag (no
# free length, no free content) or a closed enum. There is no field on any
# model in this section with an unconstrained ``str``/``Any`` type -- see
# ``test_structural_egress_projection_v2.py::
# test_schema_field_closure_no_free_text_slot_anywhere`` for the automated
# audit of that property over the full recursive schema graph.

_Sha256Prefix12 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{12}$")]
_SymbolAlias = Annotated[StrictStr, Field(pattern=r"^sym_[0-9]{3,}$")]
_PathAlias = Annotated[StrictStr, Field(pattern=r"^path_[0-9]{3,}$")]
_AstNodeTypeName = Annotated[StrictStr, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
_AstFieldName = Annotated[StrictStr, Field(pattern=r"^[a-z_][a-z0-9_]*$")]


class ProjectedLiteralV2(ContractV2Model):
    """One opaque literal: never the value, only its shape. ``kind`` is a
    closed enum; ``length`` is the encoded-byte count for every kind
    (UTF-8 bytes for ``str``, the same unit ``sha256_12`` is computed over
    -- NOT the Python character/code-point count, which would under-count
    for multibyte characters and disagree with what was actually hashed;
    #200-G2C round-1 external review, P2, Lane B); ``sha256_12`` is an
    HMAC-SHA256 digest truncated to 12 hex chars, keyed with a key
    generated randomly once per process (never derived from ``run_id`` or
    any other externally-computable value -- see ``_structural_projection_
    hmac_key_v2``'s docstring) -- equality-checkable across two identical
    literals WITHIN one process's lifetime, never reversible to the source
    bytes, and -- unlike a plain unsalted digest -- neither offline-
    dictionary-confirmable by an observer with a candidate wordlist nor
    correlatable across different outbound payloads over time (#200-G2C
    round-1 external review, P2, Lane A)."""

    kind: Literal["str", "bytes", "int", "float", "complex", "comment"]
    length: NonNegativeInt
    sha256_12: _Sha256Prefix12


class ProjectedSymbolV2(ContractV2Model):
    """One identifier's opaque, stable alias."""

    alias: _SymbolAlias


class ProjectedPathV2(ContractV2Model):
    """One repository-relative path's opaque, stable alias -- same
    treatment as ``ProjectedSymbolV2`` and for the same reason: a real repo
    file path is PR-author-controlled text (the author of a diff chooses
    every file name in it), not distinguishable in principle from any other
    arbitrary literal this module already refuses to judge content-wise.
    Payload fields that carry real paths (``ChunkCoverageV2``'s six
    ``tuple[RelativePath, ...]`` fields, ``PayloadContractReferenceV2.
    paths``) are validated only by ``RelativePath``'s near-open pattern
    (``^[^*?\\[\\]]+$``, excluding just glob metacharacters) plus
    ``sanitize_artifact_value``'s keyword/regex blocklist -- no AWS-key
    pattern, no JWT shape, no generic entropy check -- so an attacker-
    chosen filename shaped like a credential (e.g. an AWS-access-key-shaped
    basename) reaches those fields exactly as validly as any ordinary
    filename. The SAME name aliases to the SAME token across one outbound
    payload (one shared alias table across ``coverage`` and
    ``contract_references``), so coverage-tracking cross-references (e.g.
    "the file missing from must-review coverage is the SAME file that
    appears in expected_files") remain legible without any raw path byte
    surviving -- the identical trade-off already made for identifiers."""

    alias: _PathAlias


class ProjectedClosedValueV2(ContractV2Model):
    """A member of a closed, finite, zero-entropy value domain
    (``ast.Constant`` values ``None``/``True``/``False``/``Ellipsis``, or
    the ``Constant.kind`` legacy-unicode marker) kept as a TAG, never as
    the Python value itself."""

    tag: Literal["none", "true", "false", "ellipsis", "legacy_unicode_kind"]


class ProjectedStructuralIntV2(ContractV2Model):
    """A parser-derived flag/count with no free-text carrying capacity (see
    ``_STRUCTURAL_INT_EXCEPTIONS_V2``). Bounded to a small range -- ``-1``
    covers ``FormattedValue.conversion``'s "no conversion" sentinel; the
    upper bound is defense-in-depth headroom, not a claim any real value
    approaches it. Named ``count`` rather than ``value`` deliberately --
    ``value`` is exactly the kind of generic name a later, careless
    extension could repurpose for something free-text; ``count`` states
    what this field structurally IS."""

    count: Annotated[int, Field(ge=-1, le=1_000_000, strict=True)]


class ProjectedChildNodeV2(ContractV2Model):
    field_name: _AstFieldName
    node: "ProjectedNodeV2"


class ProjectedLiteralFieldV2(ContractV2Model):
    field_name: _AstFieldName
    literal: ProjectedLiteralV2


class ProjectedSymbolFieldV2(ContractV2Model):
    field_name: _AstFieldName
    symbol: ProjectedSymbolV2


class ProjectedClosedFieldV2(ContractV2Model):
    field_name: _AstFieldName
    closed: ProjectedClosedValueV2


class ProjectedStructuralIntFieldV2(ContractV2Model):
    field_name: _AstFieldName
    structural_int: ProjectedStructuralIntV2


class ProjectedNodeV2(ContractV2Model):
    """One AST node, fully closed: ``node_type`` is asserted (at
    construction, redundantly with the producer's own pre-check) to be a
    member of the LIVE-derived ``AUTHORIZED_AST_NODE_TYPES_V2``; every
    field is routed into exactly one of the four closed field-kind tuples
    below by ``_classify_leaf_v2`` -- there is no fifth, catch-all field."""

    node_type: _AstNodeTypeName
    child_nodes: tuple[ProjectedChildNodeV2, ...] = ()
    literal_fields: tuple[ProjectedLiteralFieldV2, ...] = ()
    symbol_fields: tuple[ProjectedSymbolFieldV2, ...] = ()
    closed_fields: tuple[ProjectedClosedFieldV2, ...] = ()
    structural_int_fields: tuple[ProjectedStructuralIntFieldV2, ...] = ()

    @model_validator(mode="after")
    def _validate_node_type_authorized(self) -> "ProjectedNodeV2":
        if self.node_type not in AUTHORIZED_AST_NODE_TYPES_V2:
            raise ValueError(
                f"node_type {self.node_type!r} is not in the live-derived "
                "AST node universe -- refusing to construct"
            )
        return self


ProjectedNodeV2.model_rebuild()


class ProjectedCommentV2(ContractV2Model):
    lineno: PositiveInt
    literal: ProjectedLiteralV2


class ProjectedFragmentV2(ContractV2Model):
    fragment_id: Sha256
    identifier_alias_mode: Literal["EXTERNAL_SAFE_STRUCTURAL"]
    root: ProjectedNodeV2
    comments: tuple[ProjectedCommentV2, ...] = ()


class ProjectedChunkContentV2(ContractV2Model):
    # Reuses the SAME authority as ``ChunkContentV2.chunk_id``
    # (``review_content_v2.py``) rather than a bare, unconstrained string
    # -- a chunk id is already a system-derived, pattern-constrained
    # identifier upstream, never arbitrary user content, and this field's
    # own schema must say so, not merely happen to be assigned safe values.
    chunk_id: SafeIdentifier
    fragments: tuple[ProjectedFragmentV2, ...]


# -- diff-hunk wire-format deshaping (structural, not content-based) --------


def _looks_like_diff_hunk_lines_v2(text: str) -> bool:
    lines = text.split("\n")
    non_empty = [line for line in lines if line]
    if not non_empty:
        return False
    return all(line[0] in (" ", "+", "-") for line in non_empty)


def _reconstruct_new_side_source_v2(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        if not line:
            out.append("")
            continue
        marker, rest = line[0], line[1:]
        if marker == "-":
            continue
        out.append(rest)
    return "\n".join(out)


def _candidate_source_text_v2(raw_text: str) -> str:
    if _looks_like_diff_hunk_lines_v2(raw_text):
        return _reconstruct_new_side_source_v2(raw_text)
    return raw_text


# -- HMAC-keyed digest: closes offline-dictionary-confirmation and
# cross-payload-correlation against an unsalted digest (#200-G2C round-1
# external review, P2, Lane A) ------------------------------------------

_structural_projection_hmac_key_v2_cache: bytes | None = None


def _structural_projection_hmac_key_v2() -> bytes:
    """A random key, generated once and cached for the lifetime of this
    PROCESS -- never derived from ``run_id``, a timestamp, or any other
    value an outside observer could also compute (this repository's
    execution model runs one process per review invocation, so process-
    scoped and run-scoped coincide in practice). Deriving the key from
    ``run_id`` instead would defeat the whole fix: ``run_id`` is a
    deterministic function of public identity (repo/PR/SHAs), so an
    attacker with a candidate wordlist AND the (often public) run identity
    could recompute the identical key and dictionary-confirm exactly as
    before. ``secrets.token_bytes`` (not ``random``) because this key
    stands in an adversarial role, however narrow -- it is a real, if
    small, HMAC key."""

    global _structural_projection_hmac_key_v2_cache
    if _structural_projection_hmac_key_v2_cache is None:
        _structural_projection_hmac_key_v2_cache = secrets.token_bytes(32)
    return _structural_projection_hmac_key_v2_cache


# -- leaf classification: a total function over runtime shape ---------------


def _numeric_kind_v2(value: object) -> str:
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "complex"


def _make_literal_v2(kind: str, raw_value: object) -> ProjectedLiteralV2:
    if kind == "str":
        assert isinstance(raw_value, str)
        data = raw_value.encode("utf-8")
    elif kind == "bytes":
        assert isinstance(raw_value, (bytes, bytearray))
        data = bytes(raw_value)
    else:
        data = repr(raw_value).encode("utf-8")
    # Same unit for `length` and what `sha256_12` is computed over, for
    # every kind (#200-G2C round-1 external review, P2, Lane B) -- `str`
    # previously reported the Python character/code-point count here,
    # which under-counts for multibyte characters and disagreed with the
    # UTF-8 byte length actually hashed.
    length = len(data)
    digest = hmac.new(_structural_projection_hmac_key_v2(), data, hashlib.sha256).hexdigest()[:12]
    return ProjectedLiteralV2(kind=kind, length=length, sha256_12=digest)


def _classify_leaf_v2(node_type: str, field_name: str, value: object) -> tuple[str, object]:
    """Total classifier: every branch either returns a recognized shape or
    raises ``StructuralProjectionBlockedV2``. There is no default
    pass-through branch."""

    if isinstance(value, ast.AST):
        return ("node", value)

    if node_type == "MatchSingleton" and field_name == "value":
        # `case True:` / `case False:` / `case None:` -- structural pattern
        # matching (Python 3.10+) holds these directly, NOT wrapped in a
        # `Constant` node, unlike every other occurrence of these three
        # values. Same closed, three-member domain, same rule: kept as a
        # tag, not projected as a "literal". Checked BEFORE the generic
        # "value is None -> omit" rule below: unlike every other None
        # this classifier sees (an absent optional field), this ``None``
        # is itself the matched pattern and must be recorded, not dropped.
        if isinstance(value, bool):
            return ("closed", "true" if value else "false")
        if value is None:
            return ("closed", "none")
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2)

    if value is None:
        return ("omit", None)

    if node_type == "Constant" and field_name == "value":
        if isinstance(value, bool):
            return ("closed", "true" if value else "false")
        if value is Ellipsis:
            return ("closed", "ellipsis")
        if isinstance(value, str):
            return ("literal", ("str", value))
        if isinstance(value, (bytes, bytearray)):
            return ("literal", ("bytes", value))
        if isinstance(value, (int, float, complex)):
            return ("literal", (_numeric_kind_v2(value), value))
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2)

    if node_type == "Constant" and field_name == "kind":
        if value == "u":
            return ("closed", "legacy_unicode_kind")
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2)

    if (node_type, field_name) in _STRUCTURAL_INT_EXCEPTIONS_V2:
        if isinstance(value, int) and not isinstance(value, bool):
            return ("structural_int", value)
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2)

    if isinstance(value, str):
        return ("identifier", value)

    raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2)


def _alias_for_v2(name: str, alias_table: dict[str, str], *, prefix: str = "sym") -> str:
    if name not in alias_table:
        alias_table[name] = f"{prefix}_{len(alias_table) + 1:03d}"
    return alias_table[name]


def _project_node_v2(
    node: ast.AST, *, alias_table: dict[str, str], depth: int = 0
) -> ProjectedNodeV2:
    if depth > MAX_STRUCTURAL_PROJECTION_DEPTH_V2:
        # A controlled block, not a crash -- see MAX_STRUCTURAL_PROJECTION_
        # DEPTH_V2's own comment for the two independently reproduced
        # uncaught-crash vectors (this function's own recursion, and the
        # caller's later pydantic-core serialization) this guard closes.
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_MAX_DEPTH_EXCEEDED_V2)

    node_type = type(node).__name__
    if node_type not in AUTHORIZED_AST_NODE_TYPES_V2:
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNAUTHORIZED_NODE_TYPE_V2)

    child_nodes: list[ProjectedChildNodeV2] = []
    literal_fields: list[ProjectedLiteralFieldV2] = []
    symbol_fields: list[ProjectedSymbolFieldV2] = []
    closed_fields: list[ProjectedClosedFieldV2] = []
    structural_int_fields: list[ProjectedStructuralIntFieldV2] = []

    for field_name, field_value in ast.iter_fields(node):
        items = field_value if isinstance(field_value, list) else [field_value]
        for item in items:
            kind, payload = _classify_leaf_v2(node_type, field_name, item)
            if kind == "omit":
                continue
            if kind == "node":
                child_nodes.append(
                    ProjectedChildNodeV2(
                        field_name=field_name,
                        node=_project_node_v2(item, alias_table=alias_table, depth=depth + 1),
                    )
                )
            elif kind == "literal":
                lit_kind, raw_value = payload
                literal_fields.append(
                    ProjectedLiteralFieldV2(
                        field_name=field_name, literal=_make_literal_v2(lit_kind, raw_value)
                    )
                )
            elif kind == "identifier":
                alias = _alias_for_v2(payload, alias_table)
                symbol_fields.append(
                    ProjectedSymbolFieldV2(
                        field_name=field_name, symbol=ProjectedSymbolV2(alias=alias)
                    )
                )
            elif kind == "closed":
                closed_fields.append(
                    ProjectedClosedFieldV2(
                        field_name=field_name, closed=ProjectedClosedValueV2(tag=payload)
                    )
                )
            elif kind == "structural_int":
                structural_int_fields.append(
                    ProjectedStructuralIntFieldV2(
                        field_name=field_name,
                        structural_int=ProjectedStructuralIntV2(count=payload),
                    )
                )
            else:  # pragma: no cover -- _classify_leaf_v2 is total over the above
                raise StructuralProjectionBlockedV2(
                    STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2
                )

    return ProjectedNodeV2(
        node_type=node_type,
        child_nodes=tuple(child_nodes),
        literal_fields=tuple(literal_fields),
        symbol_fields=tuple(symbol_fields),
        closed_fields=tuple(closed_fields),
        structural_int_fields=tuple(structural_int_fields),
    )


def _project_comments_v2(source: str) -> tuple[ProjectedCommentV2, ...]:
    comments: list[ProjectedCommentV2] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments.append(
                    ProjectedCommentV2(
                        lineno=tok.start[0],
                        literal=_make_literal_v2("comment", tok.string),
                    )
                )
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_PARSE_FAILED_V2) from exc
    return tuple(comments)


# -- public API ---------------------------------------------------------------


def project_fragment_structural_v2(
    *, fragment_id: str, path: str, content: str, alias_table: dict[str, str]
) -> ProjectedFragmentV2:
    """Project ONE fragment's real, reviewable content. Raises
    ``StructuralProjectionBlockedV2`` -- never returns a partial/best-effort
    result -- for anything this module cannot prove closed: a non-Python
    path, text that does not parse as Python (including a genuinely
    unterminated/malformed literal, or -- #200-G2C round-1 external review
    follow-up, discovered while mutation-testing ``MAX_STRUCTURAL_
    PROJECTION_DEPTH_V2`` -- an extreme left-recursive chain, e.g. several
    thousand chained attribute/binary-op levels in one line, deep enough
    that CPython's OWN ``ast.parse`` raises an uncaught ``RecursionError``
    before this module's node-by-node depth guard ever gets a tree to
    walk), an AST node type outside the live-derived universe, or a field
    shape outside the closed leaf classification."""

    if not path.endswith(".py"):
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_UNSUPPORTED_LANGUAGE_V2)

    source = _candidate_source_text_v2(content)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise StructuralProjectionBlockedV2(STRUCTURAL_PROJECTION_PARSE_FAILED_V2) from exc

    root = _project_node_v2(tree, alias_table=alias_table)
    comments = _project_comments_v2(source)
    return ProjectedFragmentV2(
        fragment_id=fragment_id,
        identifier_alias_mode="EXTERNAL_SAFE_STRUCTURAL",
        root=root,
        comments=comments,
    )


def project_chunk_content_structural_v2(chunk_content: "ChunkContentV2") -> ProjectedChunkContentV2:
    """Project every INCLUDED fragment of one chunk. A fragment whose
    ``content`` is ``None`` (omitted/blocked upstream, per
    ``ReviewContentPolicyV2``) carries no content risk and is skipped, not
    projected -- there is no text there to leak. The identifier alias table
    is shared across the whole chunk (not reset per fragment) so the SAME
    name aliases to the SAME token everywhere within it, preserving
    cross-fragment data-flow legibility for review."""

    alias_table: dict[str, str] = {}
    fragments: list[ProjectedFragmentV2] = []
    for fragment in chunk_content.fragments:
        if fragment.content is None:
            continue
        fragments.append(
            project_fragment_structural_v2(
                fragment_id=fragment.fragment_id,
                path=fragment.path,
                content=fragment.content,
                alias_table=alias_table,
            )
        )
    return ProjectedChunkContentV2(chunk_id=chunk_content.chunk_id, fragments=tuple(fragments))


# -- payload path closure (coverage / contract references) ------------------
#
# #200-G2C round-1 external review (P0, Lane C, against head 0614965):
# ``_build_agent_router_messages_v2`` only ever routed ``chunk_content``
# through this module's closure -- the OTHER five top-level outbound keys
# (``semantic_group``, ``coverage``, ``artifact_references``, ``contract_
# references``, ``output_contract``) were raw ``.model_dump(mode="json")``
# on ``payload``. Of those, ``semantic_group``/``artifact_references``/
# ``output_contract`` carry no free-text/path-shaped field at all (already
# closed enums, pattern-constrained ``SafeIdentifier``s, and a JSON SCHEMA
# generated purely from this codebase's own type annotations, never from PR
# content); but ``payload.coverage``'s six ``tuple[RelativePath, ...]``
# fields and ``payload.contract_references[].paths`` carry REAL repo file
# paths -- exactly the shape #200-G2/#200-G2B's blocklist-over-raw-text
# failure mode was refuted for, recreated inside a PR whose whole premise
# is "no blocklist, structural closure only." Reproduced end-to-end through
# the real production pipeline: a legal ``RelativePath`` filename shaped
# like an AWS access key sailed straight into the literal outbound body
# bytes ``build_agent_router_request_body_v2`` produces.
#
# Fix: the SAME closure this module already applies to identifiers --
# deterministic, stable, opaque aliasing (``ProjectedPathV2``), not a
# per-value "is this shaped like a secret" judgement -- extended to every
# path-bearing payload field. A real path's raw bytes never reach the wire;
# what reaches it is a token that is stable within one outbound payload, so
# coverage-tracking cross-references (the same file appearing in more than
# one coverage field) remain legible.


class ProjectedCoverageDegradationV2(ContractV2Model):
    """Mirrors ``CoverageDegradationV2`` field-for-field, closing the two
    fields it carries that are not already a closed enum: ``affected_files``
    (paths -- aliased, same as everywhere else in this section) and
    ``detail`` (free text, typed ``SafeText`` upstream -- i.e. protected
    only by the same keyword/regex blocklist as everything else this
    section closes; opaqued via the SAME ``ProjectedLiteralV2`` shape
    ordinary string literals get, since content-wise it IS one: caller-
    authored free text, not distinguishable in principle from any AST
    string literal this module already refuses to judge). Currently always
    constructed with an empty ``degradation_causes`` tuple upstream
    (``payload_builder_v2.py``'s only call site hardcodes it to ``()``) --
    this field is dormant in production today, but the schema itself
    allows content, and nothing before this fix would have caught a future
    call site populating it unprojected; closing it structurally here means
    there is no future call site to get wrong."""

    reason_code: CoverageDegradationReasonValue
    affected_files: tuple[ProjectedPathV2, ...]
    detail: ProjectedLiteralV2


class ProjectedChunkCoverageV2(ContractV2Model):
    """Mirrors ``ChunkCoverageV2`` field-for-field: ``status`` is already a
    closed enum (unchanged); every ``tuple[RelativePath, ...]`` field
    becomes ``tuple[ProjectedPathV2, ...]``."""

    status: CoverageStateValue
    expected_files: tuple[ProjectedPathV2, ...]
    reviewed_files: tuple[ProjectedPathV2, ...]
    partially_reviewed_files: tuple[ProjectedPathV2, ...]
    missing_files: tuple[ProjectedPathV2, ...]
    must_review_files: tuple[ProjectedPathV2, ...]
    missing_must_review_files: tuple[ProjectedPathV2, ...]
    degradation_causes: tuple[ProjectedCoverageDegradationV2, ...]


class ProjectedContractReferenceV2(ContractV2Model):
    """Mirrors ``PayloadContractReferenceV2`` field-for-field: ``contract_
    id``/``contract_version``/``sha256``/``scope`` are already closed
    (pattern-constrained identifiers, a hash, a closed enum -- all operator/
    target-profile-controlled, not PR-diff-controlled, so out of THIS
    finding's scope); ``paths`` becomes aliased, same as ``coverage``."""

    contract_id: SafeIdentifier
    contract_version: SafeIdentifier
    sha256: Sha256
    scope: Literal["repository", "semantic_group", "chunk", "file"]
    paths: tuple[ProjectedPathV2, ...]


def _project_path_v2(path: str, *, path_alias_table: dict[str, str]) -> ProjectedPathV2:
    return ProjectedPathV2(alias=_alias_for_v2(path, path_alias_table, prefix="path"))


def project_chunk_coverage_structural_v2(
    coverage: ChunkCoverageV2, *, path_alias_table: dict[str, str]
) -> ProjectedChunkCoverageV2:
    """Project one ``ChunkCoverageV2`` into its closed form. ``path_alias_
    table`` is shared with ``project_contract_references_structural_v2``
    (both are called against the SAME outbound payload in ``_build_agent_
    router_messages_v2``) so the same real path aliases identically across
    both -- e.g. a file appearing in both ``missing_must_review_files``
    here and a contract reference's ``paths`` there gets the same token."""

    def _paths(files: tuple[str, ...]) -> tuple[ProjectedPathV2, ...]:
        return tuple(_project_path_v2(f, path_alias_table=path_alias_table) for f in files)

    return ProjectedChunkCoverageV2(
        status=coverage.status,
        expected_files=_paths(coverage.expected_files),
        reviewed_files=_paths(coverage.reviewed_files),
        partially_reviewed_files=_paths(coverage.partially_reviewed_files),
        missing_files=_paths(coverage.missing_files),
        must_review_files=_paths(coverage.must_review_files),
        missing_must_review_files=_paths(coverage.missing_must_review_files),
        degradation_causes=tuple(
            ProjectedCoverageDegradationV2(
                reason_code=cause.reason_code,
                affected_files=_paths(cause.affected_files),
                detail=_make_literal_v2("str", cause.detail),
            )
            for cause in coverage.degradation_causes
        ),
    )


def project_contract_references_structural_v2(
    contract_references: list[PayloadContractReferenceV2], *, path_alias_table: dict[str, str]
) -> tuple[ProjectedContractReferenceV2, ...]:
    """Project every contract reference's paths into their closed, aliased
    form. See ``project_chunk_coverage_structural_v2`` for why ``path_
    alias_table`` is shared across both."""

    return tuple(
        ProjectedContractReferenceV2(
            contract_id=item.contract_id,
            contract_version=item.contract_version,
            sha256=item.sha256,
            scope=item.scope,
            paths=tuple(
                _project_path_v2(p, path_alias_table=path_alias_table) for p in item.paths
            ),
        )
        for item in contract_references
    )
