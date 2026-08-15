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
    """

    profile_path = Path(repo_root) / relative_path
    if not profile_path.is_file():
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_MISSING_REASON_V2)

    try:
        # `UnicodeDecodeError` as well as `OSError`: a target-authored file
        # holding invalid UTF-8 fails in read_text's DECODE step, which is
        # a `ValueError`, not an `OSError`.
        raw_text = profile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc

    return load_target_profile_text_v2(raw_text)


_YAML_MERGE_TAG_V2 = "tag:yaml.org,2002:merge"

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


class _FirstWinsSafeLoaderV2(yaml.SafeLoader):
    """`yaml.SafeLoader`, differing ONLY in which of two colliding entries
    wins.

    This is the whole of the ambiguity authority. Everything that decides
    what a document MEANS -- scanner, parser, composer, resolver, every
    constructor, tags, anchors, aliases, contextual construction, error
    semantics -- is stock PyYAML and is not re-derived here. The two
    overrides below change nothing except the resolution POLICY at the two
    points where PyYAML silently picks a winner among colliding entries.

    Why this shape: an earlier design walked the composed node graph and
    re-derived the parser's rules to decide what "the same key" means.
    Seven adversarial rounds each found that re-derivation wrong at a
    different layer -- textual identity, node shape, unconstructible keys,
    ancestral consumption context -- in both directions, refusing legal
    documents as well as accepting ambiguous ones. Deriving the answer
    from the parser removes the whole class: there is nothing left to get
    wrong about tags, flattening or context, because none of it is
    restated.
    """

    def construct_mapping(self, node, deep: bool = False):  # type: ignore[override]
        # Byte-for-byte PyYAML's own construct_mapping, except the marked
        # line. `flatten_mapping` is still called, but merge keys are
        # refused before any document reaches this loader, so it has
        # nothing to splice.
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
            value = self.construct_object(value_node, deep=deep)
            if key not in mapping:  # <-- THE policy. Stock overwrites.
                mapping[key] = value
        return mapping

    def construct_scalar(self, node):  # type: ignore[override]
        # The SECOND place PyYAML resolves a collision: when a mapping node
        # is consumed as a scalar (`!!str {...}`), `construct_scalar` scans
        # for `tag:yaml.org,2002:value` entries and takes the FIRST. That
        # is the same policy axis as the assignment above -- which of
        # several colliding entries wins -- so the same flip applies. It is
        # NOT a `!!value` rule: nothing here interprets what the tag means.
        if isinstance(node, yaml.MappingNode):
            selected = None
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:value":
                    selected = value_node
            if selected is not None:
                return self.construct_scalar(selected)
        return super(yaml.SafeLoader, self).construct_scalar(node)


def _document_uses_merge_v2(raw_text: str) -> bool:
    """Whether the document authors a merge key anywhere.

    `<<:` is not part of the language `TargetProfileV2` accepts, matching
    `authoritative_check_policy_v2`, which has never supported it. The
    check is deliberately narrow: it observes the composed graph for the
    merge TAG and refuses. It does not interpret merge provenance, does not
    reproduce `flatten_mapping`, and does not walk merge semantics -- the
    ambiguity authority does not need to reason about merges at all once
    they cannot appear.
    """

    loader = None
    try:
        loader = yaml.SafeLoader(raw_text)
        root = loader.get_single_node()
    except _YAML_PARSE_FAILURES_V2:
        return False  # malformed input is refused by the readings themselves
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


def _canonical_reading_v2(value: object) -> str:
    """Compare the CONSUMED value, never text, node identity, node shape,
    a tag in isolation, or re-derived provenance."""

    def normalise(item: object) -> object:
        if isinstance(item, dict):
            return {str(k): normalise(v) for k, v in sorted(item.items(), key=lambda kv: str(kv[0]))}
        if isinstance(item, (list, tuple)):
            return [normalise(x) for x in item]
        return item

    return json.dumps(normalise(value), sort_keys=True, default=repr)


def _read_unambiguously_v2(raw_text: str) -> object:
    """Read the bytes under both duplicate-resolution policies and refuse
    when they disagree.

    Ambiguity is DEFINED as "two conforming readings of the same bytes
    differ", which is the property the contract actually cares about, and
    it is measured rather than predicted. Because this loader also backs
    the install WRITER, an ambiguous profile would otherwise mint a receipt
    and a `target_profile_hash` for one reading while the other stays
    equally defensible.
    """

    if _document_uses_merge_v2(raw_text):
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_INVALID_REASON_V2)

    readings = []
    failures = 0
    for loader_class in (yaml.SafeLoader, _FirstWinsSafeLoaderV2):
        try:
            readings.append(yaml.load(raw_text, Loader=loader_class))
        except _YAML_PARSE_FAILURES_V2:
            failures += 1

    if failures:
        # Either both readings refused the document, or exactly one did --
        # which is itself a disagreement about whether the bytes are
        # readable at all. Both are refusals.
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)

    try:
        if _canonical_reading_v2(readings[0]) != _canonical_reading_v2(readings[1]):
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)
    except _YAML_PARSE_FAILURES_V2 as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc

    return readings[0]


def load_target_profile_text_v2(raw_text: str) -> TargetProfileV2:
    """Strictly validate profile text without writing it to a target.

    Install planning must validate the *prospective* seed or the currently
    observed TARGET_OWNED bytes before it can construct a receipt.  Routing
    that read-only path through this helper prevents preview from creating a
    temporary target file merely to reuse :func:`load_target_profile_v2`.
    """

    raw = _read_unambiguously_v2(raw_text)

    if not isinstance(raw, dict):
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)

    try:
        # Round-trip through JSON text (not model_validate on the raw dict)
        # so strict mode applies uniformly to nested enums, matching the
        # revalidation pattern contracts_v2.py itself relies on.
        return TargetProfileV2.model_validate_json(
            json.dumps(raw, ensure_ascii=False), strict=True
        )
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
