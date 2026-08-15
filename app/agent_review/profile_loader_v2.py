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
        raw_text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc

    return load_target_profile_text_v2(raw_text)


_YAML_MERGE_TAG_V2 = "tag:yaml.org,2002:merge"

# Parse failures that target-authored YAML may legitimately produce. NOT
# `Exception`: an internal programmer error must not be relabelled as
# invalid target input.
_YAML_PARSE_FAILURES_V2: tuple[type[BaseException], ...] = (yaml.YAMLError, RecursionError)


def _comparable_key_v2(key_node: yaml.Node) -> tuple[str, str] | None:
    """A cheap identity for duplicate comparison, or None when the key is
    not a scalar.

    Complex (sequence/mapping) keys are deliberately not compared here:
    they are unhashable in Python, so PyYAML's own constructor refuses them
    with a `ConstructorError`, which this module already normalises.
    """

    if isinstance(key_node, yaml.ScalarNode):
        return (key_node.tag, key_node.value)
    return None


def _first_duplicate_key_v2(root: yaml.Node) -> str | None:
    """Walk the COMPOSED node graph and return the first duplicated key.

    Why the node graph, and why before construction:

    PyYAML resolves `<<:` by calling `flatten_mapping`, which MUTATES the
    merge target's `node.value` in place, splicing the source's pairs into
    it. It also defers nested constructions and drains them breadth-first,
    so a shallower mapping can flatten an anchor before that anchor's own
    mapping has been constructed. A duplicate scan running during
    construction therefore sees a different node list depending on nesting
    DEPTH -- rejecting legal merge overrides in some documents while
    missing real duplicates in others.

    Composing first and scanning the untouched graph removes depth from the
    question entirely. Merge SOURCES are reached as ordinary mapping nodes
    through their value node, so a duplicate inside an inline `<<: {...}`
    is caught, while keys a merge legitimately CONTRIBUTES are never
    confused with keys the mapping declares itself.
    """

    visited: set[int] = set()
    stack: list[yaml.Node] = [root]
    while stack:
        node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))

        if isinstance(node, yaml.MappingNode):
            seen: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                stack.append(value_node)
                if key_node.tag == _YAML_MERGE_TAG_V2:
                    # `<<` is not a key of this mapping; its source is
                    # scanned on its own terms via `value_node` above.
                    continue
                identity = _comparable_key_v2(key_node)
                if identity is None:
                    continue
                if identity in seen:
                    return str(key_node.value)
                seen.add(identity)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
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

    loader = yaml.SafeLoader(raw_text)
    try:
        node = loader.get_single_node()
        if node is None:
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)
        if _first_duplicate_key_v2(node) is not None:
            raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2)
        return loader.construct_document(node)
    except _YAML_PARSE_FAILURES_V2 as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc
    finally:
        loader.dispose()


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
