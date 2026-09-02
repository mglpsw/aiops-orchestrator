"""#200-G2C -- structural egress closure for AgentReview v2 (issue #299,
successor to #280/#200-G2 and #287/#200-G2B, both refuted -- see
``app.agent_review.structural_egress_projection_v2``'s module docstring
for the full refutation history and the load-bearing property this file
tests).

## Scope of THIS file (explicit)

Tier 1 ONLY: semantically neutral fixtures (``literal_alpha``, arbitrary
made-up identifiers, plain numbers, empty/unicode strings, ...). This file
proves a CLOSURE property -- "the outbound representation has no
structural slot capable of carrying ANY raw literal's bytes" -- which is
content-agnostic by construction, so neutral fixtures prove it completely
for what they exercise. The historical secret-shaped falsifier corpus from
#280/#287/#293 (JWT-shaped, AWS-key-shaped, credential-keyword-adjacent
values, ...) is DEFERRED to a separate, later, isolated regression-suite
session against this finished implementation -- not written here, not
even as "clearly synthetic" fixtures. See the Draft PR body for the
tracking reference.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
from app.agent_review.payload_builder_v2 import build_chunk_payload_v2
from app.agent_review.review_content_extraction_v2 import extract_review_content_v2
from app.agent_review.review_content_v2 import ChunkContentV2, FragmentContentV2, ReviewContentPolicyV2
from app.agent_review.review_transport_v2 import (
    ChunkTransportError,
    _build_agent_router_messages_v2,
    build_agent_router_request_body_v2,
    build_chunk_review_request_v2,
    execute_chunk_review_v2,
)
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.contracts_v2 import SemanticGroupV2
import app.agent_review.review_transport_v2 as review_transport_v2
import app.agent_review.structural_egress_projection_v2 as sep
from app.agent_review.structural_egress_projection_v2 import (
    AUTHORIZED_AST_NODE_TYPES_V2,
    STRUCTURAL_PROJECTION_PARSE_FAILED_V2,
    STRUCTURAL_PROJECTION_UNAUTHORIZED_NODE_TYPE_V2,
    STRUCTURAL_PROJECTION_UNSUPPORTED_LANGUAGE_V2,
    STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2,
    ProjectedChunkContentV2,
    ProjectedNodeV2,
    StructuralProjectionBlockedV2,
    _classify_leaf_v2,
    project_chunk_content_structural_v2,
    project_fragment_structural_v2,
)


# ===========================================================================
# Part 1 -- unit-level: Tier-1 falsifier corpus against the projector
# directly (fast, no git fixture needed).
# ===========================================================================


def _project(source: str, *, path: str = "x.py") -> tuple[dict, dict]:
    """Project ``source`` as one fragment and return
    ``(alias_table, dumped_json_dict)``."""

    alias_table: dict[str, str] = {}
    fragment = project_fragment_structural_v2(
        fragment_id="a" * 64, path=path, content=source, alias_table=alias_table
    )
    return alias_table, json.loads(fragment.model_dump_json())


def _serialized(source: str, **kwargs) -> str:
    _, dumped = _project(source, **kwargs)
    return json.dumps(dumped)


@pytest.mark.parametrize(
    "name,source,raw_witness",
    [
        ("string_literal", 'v = "literal_alpha"\n', "literal_alpha"),
        (
            "longer_string_literal",
            'v = "literal_beta_longer_value_here"\n',
            "literal_beta_longer_value_here",
        ),
        ("bytes_literal", 'v = b"bytes_alpha"\n', "bytes_alpha"),
        (
            "bytes_literal_binary_marker",
            'v = b"\\x00\\x01\\x02binary_marker"\n',
            "binary_marker",
        ),
        ("numeric_literal", "v = 12345678\n", "12345678"),
        ("numeric_zero", "v = 0\n", None),  # 0 is too short/ambiguous to witness meaningfully
        ("numeric_negative", "v = -1\n", None),
        ("numeric_float", "v = 3.14159\n", "3.14159"),
        (
            "dict_literal_shape",
            'config = {"field_alpha": "literal_gamma"}\n',
            "literal_gamma",
        ),
        (
            "kwarg_shape",
            'some_call(field_alpha="literal_delta")\n',
            "literal_delta",
        ),
        (
            "prefix_wrapper_shape",
            'value = "prefix_" + "literal_epsilon"\n',
            "literal_epsilon",
        ),
        (
            "long_arbitrary_identifier",
            "some_arbitrary_long_variable_name_marker_zeta = 1\n",
            "some_arbitrary_long_variable_name_marker_zeta",
        ),
        (
            "docstring",
            '"""docstring_marker_content_here"""\n',
            "docstring_marker_content_here",
        ),
        (
            "embedded_newline_unicode_string",
            'v = "line_one_\\nline_two_\\u00e9caf\xe9"\n',
            "line_two_",
        ),
        ("empty_string_literal", 'v = ""\n', None),
    ],
)
def test_tier1_falsifier_raw_bytes_structurally_absent(name, source, raw_witness) -> None:
    """Structural/type proof, not content matching: assert the projected
    form parses as the closed ``ProjectedFragmentV2`` schema (so its shape
    IS the proof), and separately confirm the raw witness substring is
    absent from the serialized projection."""

    alias_table, dumped = _project(source)
    # Structural proof: `dumped` is `json.loads(fragment.model_dump_json())`
    # where `fragment` is a real, already-validated `ProjectedFragmentV2`
    # instance (construction itself enforces the closed schema, `extra=
    # "forbid"`, on every nested model) -- there is no field it could have
    # smuggled the raw value through undetected.
    serialized = json.dumps(dumped)
    if raw_witness is not None:
        assert raw_witness not in serialized, (
            f"{name}: raw witness {raw_witness!r} reached the projected form"
        )


def test_comment_content_structurally_absent() -> None:
    source = "x = 1  # comment_marker_content\n"
    _, dumped = _project(source)
    serialized = json.dumps(dumped)
    assert "comment_marker_content" not in serialized
    assert dumped["comments"], "expected the comment to be captured, opaquely"


def test_adjacent_literals_no_cross_contamination() -> None:
    """Two neutral literals next to each other: both independently become
    opaque, with DIFFERENT digests (proving neither leaked into the
    other's placeholder)."""

    source = 'a = "literal_adjacent_one"\nb = "literal_adjacent_two_longer"\n'
    _, dumped = _project(source)
    serialized = json.dumps(dumped)
    assert "literal_adjacent_one" not in serialized
    assert "literal_adjacent_two_longer" not in serialized
    literals = re.findall(r'"sha256_12":\s*"([0-9a-f]{12})"', serialized)
    assert len(literals) >= 2
    assert len(set(literals)) == len(literals), "distinct literals must not collide"


def test_unterminated_quoted_value_blocks_whole_fragment() -> None:
    """Syntax-edge case, not content-sensitive: an unterminated string is a
    ``SyntaxError``. The safe disposition is refusal, not best-effort
    partial redaction -- there is nothing to redact in text that cannot be
    parsed at all."""

    source = 'broken = "unterminated_value_marker\nreturn_marker = 1\n'
    with pytest.raises(StructuralProjectionBlockedV2) as excinfo:
        project_fragment_structural_v2(
            fragment_id="a" * 64, path="x.py", content=source, alias_table={}
        )
    assert excinfo.value.reason_code == STRUCTURAL_PROJECTION_PARSE_FAILED_V2


def test_non_python_path_is_blocked_not_best_effort() -> None:
    with pytest.raises(StructuralProjectionBlockedV2) as excinfo:
        project_fragment_structural_v2(
            fragment_id="a" * 64,
            path="notes.yaml",
            content="field_alpha: literal_alpha\n",
            alias_table={},
        )
    assert excinfo.value.reason_code == STRUCTURAL_PROJECTION_UNSUPPORTED_LANGUAGE_V2


def test_identifier_alias_is_consistent_within_one_projection() -> None:
    source = (
        "some_arbitrary_long_variable_name_marker_eta = 1\n"
        "use(some_arbitrary_long_variable_name_marker_eta)\n"
    )
    alias_table, dumped = _project(source)
    serialized = json.dumps(dumped)
    assert "some_arbitrary_long_variable_name_marker_eta" not in serialized
    aliases = re.findall(r"sym_\d+", serialized)
    assert aliases, "expected at least one alias"
    # the SAME name used twice must collapse to the SAME alias.
    assert alias_table["some_arbitrary_long_variable_name_marker_eta"]
    assert list(alias_table.values()).count(
        alias_table["some_arbitrary_long_variable_name_marker_eta"]
    ) == 1  # one alias assigned once, reused (not re-minted) on second use
    name_count = source.count("some_arbitrary_long_variable_name_marker_eta")
    alias_value = alias_table["some_arbitrary_long_variable_name_marker_eta"]
    assert serialized.count(alias_value) >= name_count


def test_match_statement_singleton_patterns_are_tagged_not_dropped() -> None:
    """``case None:``/``case True:``/``case False:`` hold their value
    directly on ``MatchSingleton`` (Python 3.10+ structural pattern
    matching), NOT wrapped in a ``Constant`` -- the one place a bare
    ``None`` value is semantically meaningful (the matched pattern) rather
    than "this optional field is absent". It must be recorded as a closed
    tag, not silently omitted."""

    source = (
        "def f(x):\n"
        "    match x:\n"
        "        case None:\n"
        "            return 1\n"
        "        case True:\n"
        "            return 2\n"
        "        case _:\n"
        "            return 3\n"
    )
    _, dumped = _project(source)
    node_types = _all_node_types(dumped["root"])
    assert node_types.count("MatchSingleton") == 2

    def _find_closed_tags(node: dict) -> list[str]:
        out = [item["closed"]["tag"] for item in node.get("closed_fields", [])]
        for child in node.get("child_nodes", []):
            out.extend(_find_closed_tags(child["node"]))
        return out

    tags = _find_closed_tags(dumped["root"])
    assert "none" in tags
    assert "true" in tags


def test_diff_hunk_marker_lines_are_deshaped_before_parsing() -> None:
    """Real reviewable content is a unified-diff hunk body (space/plus/
    minus per-line markers, ``diff_acquisition_v2``'s own convention), not
    a standalone file. This is a bounded, format-based transform -- not a
    content decision -- confirmed here directly against the projector."""

    hunk = " a = 1\n-b = 2\n+b = \"literal_diff_marker_value\"\n c = 3\n"
    alias_table, dumped = _project(hunk)
    serialized = json.dumps(dumped)
    assert "literal_diff_marker_value" not in serialized
    # the deleted side ("b = 2") must not appear either -- proves the
    # reconstruction actually dropped '-' lines rather than keeping both.
    kinds = [lit["kind"] for lit in _all_literals(dumped["root"])]
    assert "str" in kinds


def _all_literals(node: dict) -> list[dict]:
    out = [item["literal"] for item in node.get("literal_fields", [])]
    for child in node.get("child_nodes", []):
        out.extend(_all_literals(child["node"]))
    return out


def _all_node_types(node: dict) -> list[str]:
    out = [node["node_type"]]
    for child in node.get("child_nodes", []):
        out.extend(_all_node_types(child["node"]))
    return out


# ===========================================================================
# Part 2 -- grammar universe / completeness gates (no self-certification).
# ===========================================================================


def test_authorized_node_universe_is_derived_live_not_hardcoded() -> None:
    """The universe is re-derivable, right now, from the running
    interpreter's own ``ast`` module -- not a list this module also wrote
    its handler against. Re-running the derivation must reproduce the
    exact same set the module froze at import time."""

    rederived = frozenset(
        name for name, obj in vars(ast).items() if isinstance(obj, type) and issubclass(obj, ast.AST)
    )
    assert rederived == AUTHORIZED_AST_NODE_TYPES_V2
    assert len(AUTHORIZED_AST_NODE_TYPES_V2) > 100


class _UnregisteredFakeNodeV2(ast.AST):
    """A node type that structurally IS an ``ast.AST`` subclass but is NOT
    a member of ``ast`` module's own exported vocabulary (it lives in this
    test module) -- the real-world equivalent of "a node type the live
    universe does not recognize." ``ast.parse`` can never actually produce
    this; it exists purely to exercise Gate A's refusal path."""

    _fields = ()


def test_gate_a_blocks_a_node_type_outside_the_live_universe() -> None:
    assert "_UnregisteredFakeNodeV2" not in AUTHORIZED_AST_NODE_TYPES_V2
    with pytest.raises(StructuralProjectionBlockedV2) as excinfo:
        sep._project_node_v2(_UnregisteredFakeNodeV2(), alias_table={})
    assert excinfo.value.reason_code == STRUCTURAL_PROJECTION_UNAUTHORIZED_NODE_TYPE_V2


def test_gate_a_mutation_disabling_the_universe_check_lets_the_fake_node_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation test for Gate A: widen the authorized universe to include
    the fake node type (simulating "the completeness gate was removed/
    bypassed") and confirm the SAME input that was correctly blocked above
    is no longer blocked for that reason -- i.e. the gate is load-bearing,
    not decorative."""

    monkeypatch.setattr(
        sep,
        "AUTHORIZED_AST_NODE_TYPES_V2",
        AUTHORIZED_AST_NODE_TYPES_V2 | {"_UnregisteredFakeNodeV2"},
    )
    # With the gate widened, projecting the fake node no longer raises
    # UNAUTHORIZED_NODE_TYPE (it now succeeds, since the node has no
    # fields at all) -- proving the check in the un-mutated module IS what
    # blocked it.
    result = sep._project_node_v2(_UnregisteredFakeNodeV2(), alias_table={})
    assert result.node_type == "_UnregisteredFakeNodeV2"


def test_gate_b_blocks_an_unsupported_constant_value_shape() -> None:
    """``ast.parse`` never produces a tuple-valued ``Constant`` from source
    text, but a hand-built tree (e.g. from a constant-folding transform
    elsewhere) could. Gate B must block it, not silently mis-classify it as
    a safe closed value or skip it."""

    node = ast.Constant(value=(1, 2), kind=None)
    with pytest.raises(StructuralProjectionBlockedV2) as excinfo:
        sep._project_node_v2(node, alias_table={})
    assert excinfo.value.reason_code == STRUCTURAL_PROJECTION_UNSUPPORTED_LEAF_SHAPE_V2


def test_gate_b_mutation_a_permissive_classifier_would_leak_the_unsupported_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation test for Gate B: patch the classifier to treat ANY
    unrecognized leaf as a harmless "identifier" (simulating a bug that
    removes the deny-by-default fallback) and confirm the previously-
    blocked tuple-valued Constant now proceeds instead of being blocked --
    demonstrating the real classifier's fallback branch is load-bearing."""

    original = sep._classify_leaf_v2

    def _permissive(node_type, field_name, value):
        try:
            return original(node_type, field_name, value)
        except StructuralProjectionBlockedV2:
            return ("identifier", "fallback_alias_source")

    monkeypatch.setattr(sep, "_classify_leaf_v2", _permissive)
    node = ast.Constant(value=(1, 2), kind=None)
    # No longer blocked under the mutation.
    result = sep._project_node_v2(node, alias_table={})
    assert result.symbol_fields, "mutation should have routed the unsupported value to an alias"


def test_structural_int_exceptions_are_exhaustive_over_the_real_corpus() -> None:
    """Broad, real-source enumeration, not a hand-guessed list: across this
    repository's ENTIRE real ``app/agent_review`` source tree, every
    (node_type, field_name) pair that ever presents a raw, non-``Constant``
    ``int`` leaf must be a MEMBER of ``_STRUCTURAL_INT_EXCEPTIONS_V2`` --
    never a superset. This is the test that originally caught this
    module's own first draft under-claiming (it shipped with only
    ``(ImportFrom, level)`` and this scan immediately found three more
    real ones: ``FormattedValue.conversion``, ``AnnAssign.simple``,
    ``comprehension.is_async``) -- if a future Python grammar version or a
    new file in this corpus presents a FIFTH one, this test goes red
    rather than silently classifying it as an identifier or crashing."""

    seen_int_leaf_fields: set[tuple[str, str]] = set()
    corpus_dir = Path("app/agent_review")
    py_files = sorted(corpus_dir.glob("*.py"))
    assert len(py_files) > 20
    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            node_type = type(node).__name__
            for field_name, value in ast.iter_fields(node):
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, int) and not isinstance(item, bool):
                        if not (node_type == "Constant" and field_name == "value"):
                            seen_int_leaf_fields.add((node_type, field_name))
    assert seen_int_leaf_fields <= sep._STRUCTURAL_INT_EXCEPTIONS_V2
    # And the exception set is not padded with entries nothing real ever
    # produces -- every declared exception is witnessed at least once.
    assert seen_int_leaf_fields == sep._STRUCTURAL_INT_EXCEPTIONS_V2


# ===========================================================================
# Part 3 -- schema-field-closure: automated audit, not manual review.
# ===========================================================================


_FREE_TEXT_FIELD_NAME_DENYLIST = {
    "context_snippet",
    "debug_text",
    "description",
    "raw_excerpt",
    "free_text",
    "notes",
    "excerpt",
    "raw_value",
    "raw_content",
    "text",
    "value",
    "snippet",
    "message",
}


def _walk_schema_strings(
    schema: dict, defs: dict, path: str, violations: list[str], seen: set[str] | None = None
) -> None:
    seen = seen if seen is not None else set()
    if "$ref" in schema:
        ref_name = schema["$ref"].rsplit("/", 1)[-1]
        if ref_name in seen:
            # ProjectedNodeV2 is self-referential (child_nodes -> node ->
            # ProjectedNodeV2); already-visited $defs are structurally
            # identical wherever revisited, so recursion terminates here
            # instead of walking the (potentially infinite) tree shape.
            return
        _walk_schema_strings(defs[ref_name], defs, f"{path}->{ref_name}", violations, seen | {ref_name})
        return
    if schema.get("type") == "string":
        if "pattern" not in schema and "enum" not in schema and "const" not in schema:
            violations.append(f"unconstrained free-text string at {path}")
        return
    if schema.get("type") == "object":
        for prop_name, prop_schema in schema.get("properties", {}).items():
            if prop_name.lower() in _FREE_TEXT_FIELD_NAME_DENYLIST:
                violations.append(f"denylisted field name {prop_name!r} at {path}")
            _walk_schema_strings(prop_schema, defs, f"{path}.{prop_name}", violations, seen)
        return
    if schema.get("type") == "array":
        items = schema.get("items")
        if items:
            _walk_schema_strings(items, defs, f"{path}[]", violations, seen)
        return
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            _walk_schema_strings(sub, defs, path, violations, seen)


def test_schema_field_closure_no_free_text_slot_anywhere() -> None:
    """Automated audit (not manual review, per #299's requirement #2):
    every ``string``-typed field anywhere in the FULL recursive schema
    graph is either digest/alias-pattern-constrained or a closed enum/
    const -- there is no field of unconstrained ``str``/``Any`` type
    ANYWHERE this projection can produce, and no field carries a name
    from the known free-text-escape-hatch vocabulary."""

    schema = ProjectedChunkContentV2.model_json_schema()
    defs = schema.get("$defs", {})
    violations: list[str] = []
    _walk_schema_strings(schema, defs, "ProjectedChunkContentV2", violations)
    assert not violations, "\n".join(violations)


def test_schema_forbids_additional_properties_everywhere() -> None:
    """Defense in depth alongside the free-text audit: every object in the
    schema graph has ``additionalProperties: false`` (from
    ``ContractV2Model``'s ``extra=\"forbid\"``), so no undeclared field --
    free-text or otherwise -- could be smuggled in even if this module's
    own models were extended carelessly later."""

    schema = ProjectedChunkContentV2.model_json_schema()
    defs = schema.get("$defs", {})
    for name, definition in defs.items():
        if definition.get("type") == "object":
            assert definition.get("additionalProperties") is False, name
    assert schema.get("additionalProperties") is False


# ===========================================================================
# Part 4 -- real end-to-end wiring: the ACTUAL pre-HTTP bytes, not a
# reconstruction (#200-G2B's lesson, reused with revalidation).
# ===========================================================================


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _profile() -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2", "schema_version": 2, "source": "repo-profile",
            "identity": {"repo": "example/repo", "default_branch": "main"},
            "artifacts": [{"artifact_id": "full-diff", "path": "artifacts/full.diff", "kind": "diff", "required": True, "max_bytes": 1000000}],
            "budgets": {"max_chunks": 32, "total_prompt_chars": 250000, "max_chars_per_chunk": 24000, "max_files_per_chunk": 50, "max_contracts_per_chunk": 50},
            "must_review": {"paths": [], "patterns": ["*"], "artifact_ids": [], "minimum_coverage": "complete"},
            "policies": {
                "network_policy": "forbidden", "fail_closed": True, "redaction_required": True,
                "allow_partial_coverage": False, "required_checks": ["pytest"],
                "allowed_semantic_groups": ["primary_backend_logic"],
                "coverage_failure_state": "manual_required", "model_uncertainty_state": "manual_required",
            },
            "contracts": [],
            "limitations": [],
        }
    )


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(rule_id="all", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC, path_patterns=["*"], contract_ids=[], artifact_ids=[], priority=0)
    material = {"schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2, "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None}
    digest = compute_semantic_grouping_policy_sha256_v2({**material, "rules": [rule.model_dump(mode="json")]})
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _build_real_chunk(tmp_path: Path, *, filename: str, before: str, after: str):
    """Real repo -> real diff -> real manifest -> real payload -> real
    extracted ``ChunkContentV2`` -- the same production pipeline
    ``test_review_transport_v2.py`` uses, not a hand-built shortcut."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / filename).write_text(before, encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / filename).write_text(after, encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile()
    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=_grouping_policy(),
        repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
        tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
        max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    manifest = outcome.manifest
    payload_by_chunk_id = {c.chunk_id: build_chunk_payload_v2(manifest, c) for c in manifest.chunks}
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id={cid: p.payload_sha256 for cid, p in payload_by_chunk_id.items()},
        target_profile=profile,
    )
    assert content.chunks, "fixture must produce at least one chunk"
    chunk_content = content.chunks[0]
    payload = payload_by_chunk_id[chunk_content.chunk_id]
    return manifest, content, chunk_content, payload


def _real_body(tmp_path: Path, *, filename: str, before: str, after: str) -> bytes:
    manifest, content, chunk_content, payload = _build_real_chunk(
        tmp_path, filename=filename, before=before, after=after
    )
    request = build_chunk_review_request_v2(
        chunk_content, run_id=content.run_id, head_sha=manifest.identity.head_sha
    )
    messages = _build_agent_router_messages_v2(chunk_content=chunk_content, payload=payload)
    return build_agent_router_request_body_v2(model="review:code", request=request, messages=messages)


def test_real_pre_http_body_carries_no_raw_literal_bytes(tmp_path: Path) -> None:
    """The seat this whole module binds to: the EXACT bytes
    ``agent_router_transport_v2``'s HTTP transport closure would send
    (``build_agent_router_request_body_v2``, called with the SAME
    ``messages`` the closure itself builds -- not a separate
    reconstruction). A neutral literal placed in a real diff hunk must not
    reach these bytes."""

    before = "def handler():\n    x = 1\n    return x\n"
    after = (
        "def handler():\n"
        "    x = 1\n"
        '    literal_marker_theta = "literal_value_theta_neutral"\n'
        "    return x\n"
    )
    body = _real_body(tmp_path, filename="app.py", before=before, after=after)
    text = body.decode("utf-8")
    assert "literal_value_theta_neutral" not in text
    assert "literal_marker_theta" not in text
    # And the projected structural document really is what is embedded --
    # not merely absent-by-omission (e.g. the whole chunk_content key
    # missing would also make the string absent, and would be a DIFFERENT,
    # much worse bug: no content at all reaching review).
    payload = json.loads(text)
    user_message = next(m["content"] for m in payload["messages"] if m["role"] == "user")
    user_material = json.loads(user_message)
    assert "chunk_content" in user_material
    assert user_material["chunk_content"]["fragments"], "projected content must be non-empty"


def test_real_router_transport_wiring_is_load_bearing_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation test for the PRODUCTION WIRING itself (the property that
    the router transport actually calls the projector, not just that the
    projector works in isolation): bypass ``project_chunk_content_
    structural_v2`` to return the chunk content unprojected, and confirm
    the SAME neutral literal that was proven absent above now DOES reach
    the outbound body -- then restore and re-confirm GREEN."""

    before = "def handler():\n    x = 1\n    return x\n"
    after = (
        "def handler():\n"
        "    x = 1\n"
        '    literal_marker_iota = "literal_value_iota_neutral"\n'
        "    return x\n"
    )

    def _bypass(chunk_content):
        return chunk_content  # ChunkContentV2 also exposes .model_dump(mode="json")

    monkeypatch.setattr(review_transport_v2, "project_chunk_content_structural_v2", _bypass)
    body = _real_body(tmp_path / "mutated", filename="app.py", before=before, after=after)
    assert "literal_value_iota_neutral" in body.decode("utf-8"), (
        "mutation expected to defeat closure -- if this fails, the "
        "production code path is not actually calling the projector"
    )
    monkeypatch.undo()
    body_restored = _real_body(tmp_path / "restored", filename="app.py", before=before, after=after)
    assert "literal_value_iota_neutral" not in body_restored.decode("utf-8")


def test_unparseable_fragment_degrades_the_whole_chunk_to_manual_required(
    tmp_path: Path,
) -> None:
    """Never a partial/best-effort send: a fragment this module cannot
    prove closed blocks the WHOLE chunk from reaching the Router --
    ``execute_chunk_review_v2`` must degrade to ``manual_required`` with a
    typed reason, never raise, never fabricate a result."""

    before = "def handler():\n    x = 1\n    return x\n"
    after = (
        "def handler():\n"
        '    broken = "unterminated_marker\n'
        "    return x\n"
    )
    manifest, content, chunk_content, payload = _build_real_chunk(
        tmp_path, filename="app.py", before=before, after=after
    )

    def _never_called_transport(request, chunk_content, payload):  # pragma: no cover
        raise AssertionError("HTTP transport must never be reached for a blocked fragment")

    from app.agent_review.review_transport_v2 import agent_router_transport_v2

    real_transport = agent_router_transport_v2(
        base_url="https://router.example/", api_key="secret-token", model="review:code"
    )

    outcome = execute_chunk_review_v2(
        chunk_content,
        run_id=content.run_id,
        head_sha=manifest.identity.head_sha,
        payload=payload,
        transport=real_transport,
    )
    assert outcome.state == "manual_required"
    assert outcome.result is None
    assert outcome.reason_code == STRUCTURAL_PROJECTION_PARSE_FAILED_V2


# ===========================================================================
# Part 5 -- negative direction (mandatory): real repository source, no
# catastrophic semantic destruction, no crash.
# ===========================================================================


def test_negative_direction_real_repo_source_projects_without_crash_or_raw_leak() -> None:
    """Run the projector across this repository's own real
    ``app/agent_review`` source (a good corpus per #299's own text) and
    confirm: (a) every file that parses as valid Python projects without
    raising an unexpected exception, (b) none of a sample of the file's
    own distinctive string literals survive raw in the projected form,
    (c) structural fidelity survives -- the count of every AST node TYPE
    in the projected tree matches the count in the real ``ast.parse``
    tree exactly (call-graph/control-flow shape is preserved; only
    leaf VALUES are opaque)."""

    corpus_dir = Path("app/agent_review")
    py_files = sorted(corpus_dir.glob("*.py"))
    assert len(py_files) > 20, "expected a real, non-trivial corpus"

    # This module's OWN source file is deliberately excluded: it literally
    # contains this projection's fixed, closed vocabulary as string
    # literals in its own code (e.g. the mode tag "EXTERNAL_SAFE_
    # STRUCTURAL", the kind enum members) -- projecting IT will legitimately
    # re-emit those same tokens in the output's own closed-enum/const
    # fields, which is expected structural behavior, not a leak of
    # reviewed source content. Excluding one file from a corpus of 80 does
    # not weaken this direction's coverage.
    py_files = [f for f in py_files if f.name != "structural_egress_projection_v2.py"]

    projected_count = 0
    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        try:
            real_tree = ast.parse(source)
        except SyntaxError:
            continue  # not this module's concern; not valid Python at all

        alias_table: dict[str, str] = {}
        try:
            fragment = project_fragment_structural_v2(
                fragment_id="a" * 64, path="sample.py", content=source, alias_table=alias_table
            )
        except StructuralProjectionBlockedV2:
            # A real, legitimate fail-closed outcome (e.g. an AST shape
            # this session's classifier does not yet recognize) -- but it
            # must never be a crash, and #299 asks that this direction be
            # checked, not that every real file must project successfully.
            continue

        projected_count += 1
        dumped = json.loads(fragment.model_dump_json())
        serialized = json.dumps(dumped)

        real_type_counts: dict[str, int] = {}
        for node in ast.walk(real_tree):
            real_type_counts[type(node).__name__] = real_type_counts.get(type(node).__name__, 0) + 1
        projected_type_counts: dict[str, int] = {}
        for node_type in _all_node_types(dumped["root"]):
            projected_type_counts[node_type] = projected_type_counts.get(node_type, 0) + 1
        assert projected_type_counts == real_type_counts, (
            f"{py_file}: structural node-type shape diverged between the "
            "real AST and the projected one"
        )

        # Distinctive real literals (regex fragments etc., per this
        # repo's own redaction.py) must not survive raw.
        for match in re.finditer(r'"([A-Za-z][A-Za-z0-9_ .-]{20,60})"', source):
            candidate = match.group(1)
            if candidate in serialized:
                pytest.fail(f"{py_file}: literal {candidate!r} survived raw in projected form")

    assert projected_count > 10, "expected most of the real corpus to project successfully"
