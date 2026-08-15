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


class _DuplicateKeyRejectingProfileLoaderV2(yaml.SafeLoader):
    """`yaml.SafeLoader` that refuses a key repeated **within one authored
    mapping**, while leaving every other PyYAML behaviour intact.

    Why it exists: PyYAML silently keeps the LAST of a duplicated key, so an
    ambiguous profile parses clean and a different YAML implementation -- or
    a human auditor -- reading the same bytes can see a different value than
    the one that was validated and recorded (PR #235 review round 3).

    Three separate regressions were introduced getting this right, all
    against callers that previously worked, so the boundaries are spelled
    out rather than left implicit:

    - **Merge keys must still resolve** (round 4). Replacing the mapping
      constructor outright skipped `flatten_mapping`, breaking every
      profile using `<<: *anchor`. Construction is delegated to `super()`,
      which flattens as usual.
    - **Merged defaults must stay overridable** (round 5). Flattening
      first and then scanning ALSO rejected YAML's standard override
      pattern -- `{<<: *defaults, timeout: 30}` where `defaults` already
      defines `timeout` -- because `flatten_mapping` prepends merged
      entries into `node.value`, making a legitimate override look like a
      duplicate. The scan therefore runs on the ORIGINAL node, before any
      flattening, and skips merge nodes entirely: only keys literally
      written twice in the same mapping are duplicates.
    - **Unhashable keys must fail closed, not crash** (round 4). A
      sequence or mapping used as a key (`? [a, b]`) is unhashable, so the
      membership test raised a bare `TypeError` that escaped the
      `yaml.YAMLError` normalisation boundary and surfaced as a traceback.
    """

    _MERGE_TAG_V2 = "tag:yaml.org,2002:merge"

    def construct_mapping(self, node, deep: bool = False):  # type: ignore[override]
        seen: set = set()
        for key_node, _value_node in node.value:
            # Skip `<<` itself: it is not an authored key, and constructing
            # it raises (no constructor for the merge tag). Multiple merge
            # keys in one mapping are legal PyYAML and stay legal here.
            if key_node.tag == self._MERGE_TAG_V2:
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicated = key in seen
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"unhashable key {key!r}",
                    key_node.start_mark,
                ) from exc
            if duplicated:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


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


def load_target_profile_text_v2(raw_text: str) -> TargetProfileV2:
    """Strictly validate profile text without writing it to a target.

    Install planning must validate the *prospective* seed or the currently
    observed TARGET_OWNED bytes before it can construct a receipt.  Routing
    that read-only path through this helper prevents preview from creating a
    temporary target file merely to reuse :func:`load_target_profile_v2`.
    """

    try:
        # Duplicate-key rejection, not `yaml.safe_load` (PR #235 review
        # round 3, confirmed): PyYAML silently keeps the LAST of a repeated
        # mapping key, so a profile declaring `identity.repo` twice parsed
        # clean -- and because the reconciliation writer uses this same
        # loader, it could mint a self-consistent receipt and raw-byte hash
        # for the ambiguous document. Another YAML implementation, or a
        # human auditor, reading the same bytes could see a different
        # identity than the one that was validated and recorded. Fixed in
        # the SHARED loader deliberately: putting it only in `validate`
        # would let the writer and the validator disagree about which
        # documents are well-formed, the exact drift class this slice
        # already had to fix once for the seed-identity placeholder.
        # Mirrors `authoritative_check_policy_v2`'s own precedent for
        # authorization YAML.
        raw = yaml.load(raw_text, Loader=_DuplicateKeyRejectingProfileLoaderV2)
    except yaml.YAMLError as exc:
        raise TargetProfileLoadErrorV2(TARGET_PROFILE_UNREADABLE_REASON_V2) from exc

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
