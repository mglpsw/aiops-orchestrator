"""Strict, fail-closed loader for AgentReview ``TargetProfileV2``.

Unlike v1's ``repo_profile.py`` (``load_repo_profile``), there is no silent
degradation to a placeholder profile: an absent, unreadable, malformed, or
schema-invalid profile is a hard failure, and the caller never receives a
usable object in that case.

The hard boundaries a target cannot weaken (``network_policy=forbidden``,
``fail_closed=true``, ``redaction_required=true``,
``allow_partial_coverage=false``) are already enforced structurally by
``contracts_v2.TargetProfileV2`` itself (each is a ``Literal`` of exactly
one value) -- this module does not re-implement that; it only guarantees
the profile is loaded through full strict Python validation, never through
JSON Schema alone, and never coerced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Hashable
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.external_path_ingress_v2 import (
    EXTERNAL_PATH_MISSING_REASON_V2,
    EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
    ExternalInputFileV2,
    ExternalPathIngressError,
    validate_external_input_file_v2,
)

TARGET_PROFILE_MISSING_REASON_V2 = "target_profile_missing"
TARGET_PROFILE_UNREADABLE_REASON_V2 = "target_profile_unreadable"
TARGET_PROFILE_INVALID_REASON_V2 = "target_profile_invalid"

DEFAULT_TARGET_PROFILE_RELATIVE_PATH = Path(".aiops") / "target-profile.v2.yaml"


class TargetProfileLoadErrorV2(ValueError):
    """Raised for every profile-loading failure. Carries a stable
    ``reason_code`` only -- never raw YAML/JSON content, the original
    exception text, or a local path."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def load_target_profile_file_v2(profile_file: ExternalInputFileV2) -> TargetProfileV2:
    """Load profile bytes through an already-validated external-path capability."""

    try:
        raw_text = profile_file.read_text(encoding="utf-8")
    except ExternalPathIngressError as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    return load_target_profile_text_v2(raw_text)


def load_target_profile_v2(
    repo_root: Path | str,
    *,
    relative_path: Path = DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
) -> TargetProfileV2:
    """Load and fully revalidate a target profile from ``repo_root``.

    ``repo_root`` must be the trusted base/default checkout -- this
    function has no opinion on which checkout that is; the caller is
    responsible for only ever pointing it at base/default, never at a PR
    branch's working tree, in a privileged workflow.

    G4B: raw caller path interpretation is delegated to the centralized
    external-path authority.  This wrapper exists for compatibility; new
    callers that already hold a capability should use
    :func:`load_target_profile_file_v2` directly.
    """

    profile_path = Path(repo_root) / relative_path
    try:
        profile_file = validate_external_input_file_v2(profile_path, root=repo_root)
    except ExternalPathIngressError as exc:
        if exc.reason_code in {
            EXTERNAL_PATH_MISSING_REASON_V2,
            EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
        }:
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_MISSING_REASON_V2) from exc
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    return load_target_profile_file_v2(profile_file)


_YAML_MERGE_TAG_V2 = "tag:yaml.org,2002:merge"
_YAML_VALUE_TAG_V2 = "tag:yaml.org,2002:value"

# Failures target-authored YAML may legitimately produce. NOT `Exception`:
# an internal programmer error must not be relabelled as invalid input.
#
# PyYAML's scalar constructors do not validate the text they are handed --
# they index it, slice it, look it up in a table, call builtins on it -- so
# each fails in its own way and none of those is a `YAMLError`:
#
#     !!int nope        int()          -> ValueError
#     !!int ""          text[0]        -> IndexError
#     !!bool nope       bool_values[]  -> KeyError
#     !!timestamp nope  regex .group() -> AttributeError
#     !!timestamp {..}  regex on dict  -> TypeError
#
# The set is enumerated; its completeness is NOT proven. The corpus below
# is systematic evidence over the families it enumerates, not a
# demonstration that no other PyYAML behaviour can escape.
_YAML_PARSE_FAILURES_V2: tuple[type[BaseException], ...] = (
    yaml.YAMLError,
    RecursionError,
    ValueError,
    KeyError,
    IndexError,
    AttributeError,
    TypeError,
    UnicodeDecodeError,
)


class AmbiguousProfileDocumentV2(Exception):
    """Raised at the exact point PyYAML would otherwise pick silently."""


class _CollisionRefusingSafeLoaderV2(yaml.SafeLoader):
    """`yaml.SafeLoader` that REFUSES where stock would choose silently.

    The authority observes the moment the real constructor is about to
    resolve a collision between two entries the document authored, and
    refuses before the resolution happens. It does not compare readings,
    canonicalise values, or project to JSON to decide anything.

    Why not compare two readings, which is what this module did before:
    "two policies agree" does not mean "unambiguous". A key authored three
    times as A, B, A resolves to A under both first- and last-wins, so a
    comparison accepts it while the document plainly contains a conflict.
    Comparison samples two points of a larger space; observation does not
    sample at all.

    TWO collision points are instrumented. Both are places where stock
    PyYAML silently selects among competing authored entries. This is not
    a claim that PyYAML has no others -- it is what has been demonstrated,
    and it is an explicit target of adversarial review.

    What makes this different from the node-walker of PR #236: nothing
    here re-derives what the parser would do. There is no rule per
    `ScalarNode`/`MappingNode`, no structural surrogate, no model of
    contextual tag semantics, no reconstructed ancestry, no equivalence
    table, and no inference that "PyYAML will refuse this later". The key
    is whatever `construct_object` returns, the mapping is flattened by
    stock `flatten_mapping`, and the only added statement is a refusal.

    Merge keys are refused before any document reaches this loader, so
    `flatten_mapping` has nothing to splice and every pair present is one
    the document authored. Provenance is therefore not tracked -- it is
    unreachable, not re-derived.
    """

    def construct_mapping(self, node, deep: bool = False):  # type: ignore[override]
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, f"expected a mapping node, but found {node.id}", node.start_mark
            )
        self.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "found unhashable key", key_node.start_mark,
                )
            # COLLISION POINT 1 of 2 -- mapping assignment.
            # Stock overwrites the earlier entry and says nothing.
            if key in mapping:
                raise AmbiguousProfileDocumentV2()
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping

    def construct_scalar(self, node):  # type: ignore[override]
        # COLLISION POINT 2 of 2 -- a MappingNode consumed as a scalar.
        # Stock scans for `tag:yaml.org,2002:value` entries and takes the
        # FIRST. More than one candidate means it is choosing silently.
        #
        # Nothing here interprets what the tag MEANS or what the profile
        # should do with it; the only observation is "more than one
        # candidate exists".
        if isinstance(node, yaml.MappingNode):
            candidates = [v for k, v in node.value if k.tag == _YAML_VALUE_TAG_V2]
            if len(candidates) > 1:
                raise AmbiguousProfileDocumentV2()
            if candidates:
                return self.construct_scalar(candidates[0])
        return super().construct_scalar(node)


def _document_uses_merge_v2(raw_text: str) -> bool:
    """Whether the document authors a merge key anywhere.

    `<<:` is not part of the language `TargetProfileV2` accepts, matching
    `authoritative_check_policy_v2`, which has never supported it.

    Causal reason, not preference: YAML's merge spec already fixes which
    entry wins, so "the document authored this key twice" stops being
    well-defined once merged pairs are spliced in alongside authored ones.
    Excluding merge is what makes provenance unnecessary rather than
    re-derived.

    Deliberately narrow: observe the merge tag in the composed graph and
    refuse. No provenance, no precedence interpretation, no reproduction
    of `flatten_mapping`.
    """

    loader = None
    try:
        loader = yaml.SafeLoader(raw_text)
        root = loader.get_single_node()
    except _YAML_PARSE_FAILURES_V2:
        return False  # malformed input is refused by the reading itself
    finally:
        if loader is not None:
            loader.dispose()

    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if key_node.tag == _YAML_MERGE_TAG_V2:
                    return True
                stack.append(key_node)
                stack.append(value_node)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return False


def _read_unambiguously_v2(raw_text: str) -> object:
    """Read the bytes once, refusing at any point the parser would resolve
    a collision silently.

    One reading, not two. No canonicalisation, no JSON projection: the
    decision is made where the collision occurs, so nothing downstream can
    hide or manufacture one.
    """

    if _document_uses_merge_v2(raw_text):
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_INVALID_REASON_V2)

    try:
        return yaml.load(raw_text, Loader=_CollisionRefusingSafeLoaderV2)
    except AmbiguousProfileDocumentV2 as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    except _YAML_PARSE_FAILURES_V2 as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc


def load_target_profile_text_v2(raw_text: str) -> TargetProfileV2:
    """Strictly validate profile text without writing it to a target.

    Install planning must validate the *prospective* seed or the currently
    observed TARGET_OWNED bytes before it can construct a receipt. Routing
    that read-only path through this helper prevents preview from creating a
    temporary target file merely to reuse :func:`load_target_profile_v2`.
    """

    raw = _read_unambiguously_v2(raw_text)

    if not isinstance(raw, dict):
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)

    try:
        # Validate the object the parser produced, WITHOUT a JSON
        # round-trip. `json.dumps` coerces non-string mapping keys to
        # strings, so `{"1": a, 1: b}` -- two distinct Python keys, no
        # collision -- was re-serialised as the literal duplicate-key
        # document `{"1": "a", "1": "b"}` and reparsed last-wins. That is
        # a second key-resolution policy introduced by the validation
        # step, downstream of the authority that exists to refuse exactly
        # that. Contract validation must not introduce one.
        #
        # Equivalence measured, not assumed: `model_validate` and
        # `model_validate_json(json.dumps(...), strict=True)` agree on
        # every valid profile in the corpus (0 divergences).
        return TargetProfileV2.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_INVALID_REASON_V2) from exc


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_target_profile_bytes_v2(profile: TargetProfileV2) -> bytes:
    """Canonical bytes hashed for ``profile_hash``: every validated field
    of the profile, using the same canonical-JSON rules documented and
    tested throughout ``contracts_v2.py`` (sorted keys, no whitespace,
    UTF-8, no trailing newline)."""

    return _canonical_json_bytes_v2(profile.model_dump(mode="json"))


def compute_profile_hash_v2(profile: TargetProfileV2) -> str:
    return hashlib.sha256(canonical_target_profile_bytes_v2(profile)).hexdigest()


def canonical_target_policy_bytes_v2(profile: TargetProfileV2) -> bytes:
    """Canonical bytes hashed for ``policy_hash``: the profile's
    ``policies`` object alone, so a policy change is independently
    detectable from an artifact/budget/must-review change."""

    return _canonical_json_bytes_v2(profile.policies.model_dump(mode="json"))


def compute_policy_hash_v2(profile: TargetProfileV2) -> str:
    return hashlib.sha256(canonical_target_policy_bytes_v2(profile)).hexdigest()
