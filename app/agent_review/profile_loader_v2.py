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
    # The set below is enumerated, and its completeness is NOT proven.
    # `test_the_parse_boundary_is_total_over_the_whole_tag_space` gives
    # systematic evidence over the families it enumerates, and mutations
    # show it discriminates them -- that is strong support, not a
    # demonstration that no other PyYAML behaviour can escape. The guard
    # exists because the first types were added one at a time as each was
    # reported, while the corpus that then claimed totality only exercised
    # STRUCTURAL malformation and never reached this code path.
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

    EVERY key is constructed, not only scalars. Skipping non-scalar nodes
    assumed "complex key implies unhashable, so PyYAML refuses it anyway".
    That is false: an explicitly tagged node constructs to whatever its
    tag says, so

        ? !!str {=: repo}
        : attacker/evil
        repo: acme/svc

    yields the ordinary hashable string key `repo` twice, and the earlier
    scan skipped the first one on sight of its node type. What decides
    comparability is the CONSTRUCTED value's hashability -- the same
    property the consumed mapping uses -- never the node's shape.

    Construction is cached on the loader and reused by
    `construct_document`, so this adds no second reading.
    """

    try:
        key = loader.construct_object(key_node, deep=True)
    except _YAML_PARSE_FAILURES_V2:
        # Unconstructible key: PyYAML refuses the document downstream with
        # its own typed error, which this module normalises.
        return _UNCOMPARABLE_KEY_V2
    if not isinstance(key, Hashable):
        return _UNCOMPARABLE_KEY_V2
    return key


def _first_contract_violation_v2(loader: yaml.SafeLoader, root: yaml.Node) -> str | None:
    """Walk the COMPOSED node graph; return the reason code for the first
    refusal, or None.

    Two refusals live here, with different dispositions:

    - a key outside the contract's domain  -> `target_profile_invalid`
    - an ambiguous document                -> `target_profile_unreadable`


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

        # SCAN ACCORDING TO HOW THE NODE WILL BE CONSUMED.
        #
        # A mapping node carrying a non-default tag is not consumed as a
        # mapping at all: `!!str {=: repo, 123: ignored}` is handed to the
        # string constructor, which selects an entry BY TAG and never
        # constructs the integer sibling. Treating its pairs as ordinary
        # authored entries was wrong in both directions --
        #
        #   under-refusing: `!!str {!!value a: x, !!value b: y}` has two
        #     entries the constructor cannot tell apart (same tag), so the
        #     first silently wins; comparing `(tag, value)` saw them as
        #     distinct and accepted the document.
        #
        #   over-refusing: the integer sibling above is never consumed, yet
        #     the string-key domain rule rejected the whole profile for it.
        #
        # So: default-tagged mappings are consumed mappings and get the
        # full contract treatment; otherwise entries are compared by the
        # only thing their constructor discriminates on -- the key tag --
        # and the domain rule does not apply to keys that never reach a
        # consumed mapping.
        if node.tag != yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG:
            selected_tags: set[str] = set()
            for key_node, value_node in node.value:
                stack.append(value_node)
                stack.append(key_node)
                if key_node.tag in selected_tags:
                    return TARGET_PROFILE_UNREADABLE_REASON_V2
                selected_tags.add(key_node.tag)
            continue

        # A SET, not a list. Membership on a list is linear, so a mapping
        # of n unique keys cost O(n^2) to scan -- and the scan runs BEFORE
        # schema validation can reject the extra fields, so target-authored
        # input controlled the work. Measured 10.8ms -> 128.6ms from 200 to
        # 1600 keys before this change.
        constructed_keys: set[object] = set()
        # Keys that cannot be CONSTRUCTED are still authored keys.
        #
        # Skipping them assumed "PyYAML will refuse the document anyway".
        # False when an enclosing tag swallows them: in
        # `? !!str {=: repo, =: default_branch}` both inner keys carry
        # `tag:yaml.org,2002:value`, which SafeLoader has no constructor
        # for -- yet the `!!str` on the enclosing mapping constructs the
        # whole thing to `'repo'`, so the first `=` silently wins and a
        # different reader could equally produce `'default_branch'`.
        # Compared structurally instead, in their own namespace so a
        # surrogate can never collide with a real constructed value.
        unconstructible_keys: set[tuple[str, str]] = set()
        merge_keys_seen = 0
        for key_node, value_node in node.value:
            stack.append(value_node)
            # The KEY node is walked too. A key may itself be a mapping or
            # sequence carrying its own authored pairs -- e.g.
            # `? !!str {=: repo, =: default_branch}` holds a duplicate `=`
            # INSIDE the key -- and enqueueing only the value left that
            # subtree unscanned.
            #
            # Same class one level in again: round 4 fixed WHICH keys are
            # compared; this fixes what lives inside a key. The walk must
            # reach every authored mapping in the document, wherever it
            # sits.
            stack.append(key_node)
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
                    return TARGET_PROFILE_UNREADABLE_REASON_V2
                continue

            key = _constructed_key_v2(loader, key_node)
            if key is not _UNCOMPARABLE_KEY_V2 and not isinstance(key, str):
                # A key that CONSTRUCTS to a non-string is already outside
                # `TargetProfileV2`'s domain: the contract is JSON-shaped,
                # its models are strict and `extra="forbid"`, and canonical
                # JSON has no non-string object keys. Enforcing that here
                # is early validation of a property the contract already
                # has -- not a new restriction on the language.
                #
                # It also removes an attack rather than mitigating it.
                # Integer keys chosen as multiples of `sys.hash_info.
                # modulus` all hash to 0, so they degrade set membership
                # to a linear scan and restore the quadratic behaviour the
                # set was introduced to remove. Refusing on the FIRST such
                # key means they never enter the table and the mapping is
                # never fully constructed.
                #
                # Deliberately NOT a key-count ceiling: a cap would be a
                # genuinely new property ("a document with N+1 fields is
                # refused by quantity") that the contract does not have.
                #
                # This rule SUBSUMES the earlier JSON-projection check.
                # That check existed because `json.dumps` coerces
                # non-string keys, so `{"1": a, 1: b}` was re-serialised as
                # a literal duplicate-key document. With non-string keys
                # refused here, projection is the identity function on the
                # keys that survive, and a projection collision is just a
                # constructed-key collision. The check was removed rather
                # than kept as unreachable code -- but if this rule is ever
                # relaxed, the projection check must come back with it.
                return TARGET_PROFILE_INVALID_REASON_V2
            if key is _UNCOMPARABLE_KEY_V2:
                if isinstance(key_node, yaml.ScalarNode):
                    surrogate = (key_node.tag, key_node.value)
                    if surrogate in unconstructible_keys:
                        return TARGET_PROFILE_UNREADABLE_REASON_V2
                    unconstructible_keys.add(surrogate)
                continue
            if key in constructed_keys:
                return TARGET_PROFILE_UNREADABLE_REASON_V2
            constructed_keys.add(key)
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
    reason = TARGET_PROFILE_UNREADABLE_REASON_V2
    try:
        # The typed refusal is raised AFTER this block, never inside it.
        # `TargetProfileLoadErrorV2` is itself a `ValueError`, and the
        # boundary now catches `ValueError` -- raising it here would let
        # the handler swallow its own typed refusal and relabel it.
        try:
            loader = yaml.SafeLoader(raw_text)
            node = loader.get_single_node()
            reason = (
                TARGET_PROFILE_UNREADABLE_REASON_V2
                if node is None
                else _first_contract_violation_v2(loader, node)
            )
            if reason is None:
                return loader.construct_document(node)
        except _YAML_PARSE_FAILURES_V2 as exc:
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    finally:
        if loader is not None:
            loader.dispose()
    raise TargetProfileLoadErrorV2(reason)


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
