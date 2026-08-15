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
        # holding invalid UTF-8 fails in `read_text`'s DECODE step, which
        # is a `ValueError`, not an `OSError`. Third instance of the same
        # class as the `ReaderError` finding -- a statement that touches
        # target-authored bytes sitting outside its own boundary -- found
        # by sweeping every such layer rather than waiting for a report.
        # The policy loader already did this correctly and is the control.
        raw_text = profile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc

    return load_target_profile_text_v2(raw_text)


_YAML_MERGE_TAG_V2 = "tag:yaml.org,2002:merge"

# Parse failures that target-authored YAML may legitimately produce. NOT
# `Exception`: an internal programmer error must not be relabelled as
# invalid target input.
_YAML_PARSE_FAILURES_V2: tuple[type[BaseException], ...] = (
    yaml.YAMLError,
    RecursionError,
    # PyYAML's scalar constructors do not validate the text they are
    # handed: they index it, slice it, look it up in a table and call
    # Python builtins on it. Against target-authored input each of those
    # fails in its own way, and NONE of them is a `YAMLError`:
    #
    #   !!int nope        int()          -> ValueError
    #   !!int ""          text[0]        -> IndexError
    #   !!bool nope       bool_values[]  -> KeyError
    #   !!timestamp nope  regex .group() -> AttributeError
    #
    # The set below is enumerated, but its COMPLETENESS is not asserted --
    # it is proven by `test_the_parse_boundary_is_total_over_the_whole_tag_space`,
    # which fuzzes every standard YAML tag against a malformed-payload
    # corpus and fails if anything escapes. That guard exists because the
    # first four types were added one at a time as each was reported,
    # while the corpus that "proved totality" only ever exercised
    # STRUCTURAL malformation and never reached this code path at all.
    ValueError,
    KeyError,
    IndexError,
    AttributeError,
)


_UNCOMPARABLE_KEY_V2 = object()


def _constructed_key_v2(loader: yaml.SafeLoader, key_node: yaml.Node) -> object:
    """The key value a consumer will actually see, or a sentinel.

    Ambiguity is a property of the CONSUMED mapping, not of the source
    text. `(tag, raw text)` -- what this compared before -- treats
    `yes:`/`true:`, `1:`/`1.0:`, `0x10:`/`16:`, `~:`/`null:` and
    `on:`/`On:`/`TRUE:` as distinct, because their tags and lexical forms
    genuinely differ. Every one of those pairs collapses to ONE key in the
    constructed mapping, which is exactly the "same bytes, two readings"
    condition this loader exists to refuse.

    Only SCALAR keys are constructed: a complex key is unhashable, so
    PyYAML's own constructor refuses it with a `ConstructorError` this
    module already normalises. Construction is cached on the loader and
    reused by `construct_document`, so this adds no second reading.
    """

    if not isinstance(key_node, yaml.ScalarNode):
        return _UNCOMPARABLE_KEY_V2
    return loader.construct_object(key_node, deep=False)


def _json_projected_key_v2(key: object) -> str | None:
    """How `json.dumps` will render this key, or None if it cannot.

    The profile is validated by round-tripping the constructed mapping
    through JSON text, and `json.dumps` COERCES non-string keys to
    strings. So `{"1": a, 1: b}` -- two genuinely distinct constructed
    keys -- becomes the literal duplicate-key document
    `{"1": "a", "1": "b"}`, which the JSON parser then resolves
    last-wins. Refusing ambiguity while MANUFACTURING an ambiguous
    document one step later would defeat the whole authority, so the
    projection is checked here too.
    """

    if isinstance(key, str):
        return key
    if isinstance(key, bool):  # before int: bool IS an int in Python
        return "true" if key else "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return str(key)
    return None


def _first_ambiguous_key_v2(loader: yaml.SafeLoader, root: yaml.Node) -> str | None:
    """Walk the COMPOSED node graph and return the first ambiguous key.

    Why the node graph, and why before construction:

    PyYAML resolves `<<:` by calling `flatten_mapping`, which MUTATES the
    merge target's `node.value` in place, splicing the source's pairs into
    it. It also defers nested constructions and drains them breadth-first,
    so a shallower mapping can flatten an anchor before that anchor's own
    mapping has been constructed. A scan running during construction
    therefore sees a different node list depending on nesting DEPTH --
    rejecting legal merge overrides in some documents while missing real
    duplicates in others.

    Composing first and scanning the untouched graph removes depth from
    the question entirely. Merge SOURCES are reached as ordinary mapping
    nodes through their value node, so a duplicate inside an inline
    `<<: {...}` is caught, while keys a merge legitimately CONTRIBUTES are
    never confused with keys the mapping declares itself.

    Two collisions are refused, because the profile is consumed through
    both representations:

    1. CONSTRUCTED-key collision -- two authored keys that become one key
       in the Python mapping.
    2. JSON-PROJECTED-key collision -- two distinct constructed keys that
       `json.dumps` renders as one, in the round-trip this module uses to
       validate.

    `visited` is keyed on node IDENTITY so a mapping reachable by several
    aliases is scanned once and a recursive alias terminates.
    """

    visited: set[int] = set()
    stack: list[yaml.Node] = [root]
    while stack:
        node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))

        if isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
            continue
        if not isinstance(node, yaml.MappingNode):
            continue

        constructed_keys: list[object] = []
        projected_keys: set[str] = set()
        merge_keys_seen = 0
        for key_node, value_node in node.value:
            stack.append(value_node)
            if key_node.tag == _YAML_MERGE_TAG_V2:
                # `<<` is not a key of this mapping; its source is scanned
                # on its own terms via `value_node` above.
                #
                # But at most ONE may be authored. YAML 1.1 permits a
                # single merge key, whose value may be a SEQUENCE of
                # sources; PyYAML tolerates several separate `<<` keys and
                # resolves them differently from the sequence spelling:
                #
                #     {<<: [*a, *b]}      -> first source wins
                #     {<<: *a, <<: *b}    -> last declaration wins
                #
                # So a mapping with two authored `<<` means different
                # things to two conforming readers -- the same ambiguity
                # class this authority exists to refuse, which skipping
                # merge keys unconditionally let through.
                merge_keys_seen += 1
                if merge_keys_seen > 1:
                    return "<<"
                continue

            key = _constructed_key_v2(loader, key_node)
            if key is _UNCOMPARABLE_KEY_V2:
                continue
            try:
                already_present = key in constructed_keys
            except TypeError:
                # Unhashable/uncomparable constructed key: SafeLoader
                # refuses it downstream with its own typed error.
                continue
            if already_present:
                return str(key_node.value)
            constructed_keys.append(key)

            projected = _json_projected_key_v2(key)
            if projected is None:
                continue
            if projected in projected_keys:
                return str(key_node.value)
            projected_keys.add(projected)
    return None


def _parse_unambiguous_yaml_v2(raw_text: str) -> object:
    """Compose, refuse ambiguity, then construct with a stock SafeLoader.

    A duplicated key makes the document mean two different things to two
    conforming readers (`yaml.safe_load` keeps the last; a first-wins
    reader or a human auditor sees the first). Because this loader also
    backs the install WRITER, an ambiguous profile would mint a receipt and
    a `target_profile_hash` for one of those readings while the other
    remains equally defensible.

    Construction is left entirely to `yaml.SafeLoader`, so anchors, merge
    keys and merge overrides keep stock semantics and explicitly tagged
    non-mapping nodes still hit PyYAML's own `isinstance` guard -- which
    raises a `ConstructorError` this function normalises, instead of the
    raw `TypeError`/`ValueError` an overridden `construct_mapping` produced
    by unpacking `node.value` before that guard could run.
    """

    # The loader is CONSTRUCTED inside the boundary, not before it.
    # `yaml.SafeLoader(raw_text)` scans the stream eagerly and raises
    # `ReaderError` for a forbidden character (NUL, BEL, a lone surrogate)
    # while the object is still being built. `ReaderError` IS a
    # `YAMLError` and was already in the failure set -- it escaped purely
    # because the statement sat one line above the `try`. The boundary has
    # to cover every statement that touches target-authored bytes, not
    # just the ones that obviously parse.
    loader = None
    try:
        # The typed refusal is raised AFTER this block, never inside it.
        # `TargetProfileLoadErrorV2` is itself a `ValueError`, and the
        # boundary now catches `ValueError` -- raising it here would let
        # the handler swallow its own typed refusal and relabel it.
        try:
            loader = yaml.SafeLoader(raw_text)
            node = loader.get_single_node()
            ambiguous = node is None or _first_ambiguous_key_v2(loader, node) is not None
            if not ambiguous:
                return loader.construct_document(node)
        except _YAML_PARSE_FAILURES_V2 as exc:
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    finally:
        if loader is not None:
            loader.dispose()
    raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)


def load_target_profile_text_v2(raw_text: str) -> TargetProfileV2:
    """Strictly validate profile text without writing it to a target.

    Install planning must validate the *prospective* seed or the currently
    observed TARGET_OWNED bytes before it can construct a receipt.  Routing
    that read-only path through this helper prevents preview from creating a
    temporary target file merely to reuse :func:`load_target_profile_v2`.
    """

    raw = _parse_unambiguous_yaml_v2(raw_text)

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
