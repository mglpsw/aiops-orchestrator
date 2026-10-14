"""Target repository profile loading for offline AgentReview intake."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.agent_review.payload_cost_model import (
    METHOD_SOURCE_CURRENT,
    METHOD_SOURCE_STALE,
    METHOD_SOURCE_UNVERIFIABLE,
    METHOD_TEXT_BOUND,
    METHOD_TEXT_BOUND_REDACTED,
    METHOD_TEXT_UNBOUND,
    canonical_method_source_path,
    normalize_method_text,
)
from app.agent_review.redaction import RedactionState, redact_value, sanitize_artifact_value
from app.agent_review.schemas import TARGET_PROFILE_SCHEMA, TargetProfile
from app.agent_review.source_boundary import (
    METHOD_DOCUMENT_MAX_BYTES,
    SOURCE_STATE_ABSENT,
    SOURCE_STATE_PRESENT,
    YAML_SOURCE_MAX_BYTES,
    acquire_source,
    SafeSourceLoader,
    parse_yaml_text,
    scan_structure,
)


# R5-A: C2 declarations that `.aiops/repo-profile.yaml` cannot carry. Two classes, one fail-closed family:
# - consumer-only relations (`contracts`, `contract_refs`, `contract_bindings`): the consumer reads them from a
#   hand-built intake, but they are NOT repo-profile v1 grammar and `TargetProfile` ignores unknown keys;
# - inline C2 sources (`domain_contracts`, `review_packs`): the canonical carriers are the files
#   `.aiops/domain-contracts.yaml` / `.aiops/review-packs.yaml`, and the loader overwrites the profile fields from
#   those files, so an inline copy would be dropped (or silently lose to the file).
# Without this census a declaration disappears and the review can stay conclusive without the declared relation.
# The canonical relation carriers are `review_packs.packs[*].domain_contract` and the review-packs envelope's
# `contract_bindings`. Deliberately NOT `extra="forbid"` (that would widen the behavior of every other key).
REPO_PROFILE_UNSUPPORTED_C2_FIELDS: tuple[str, ...] = (
    "contract_bindings",
    "contract_refs",
    "contracts",
    "domain_contracts",
    "method_guidance",
    "method_guidance_verification",
    "review_packs",
)
# An inline source declares nothing when it is null (e.g. a `TargetProfile.model_dump()` round trip), so a null
# inline source is tolerated; any non-null inline value would be overwritten by the file loader and is rejected.
# `method_guidance_verification` is loader-owned: an author-supplied value is never a verification.
REPO_PROFILE_INLINE_C2_SOURCE_FIELDS: frozenset[str] = frozenset(
    {"domain_contracts", "method_guidance", "method_guidance_verification", "review_packs"}
)

METHOD_GUIDANCE_SIDECAR = "method-guidance.yaml"
# A method source larger than this is not read by the loader: it cannot be bound to its declared blob.
_METHOD_SOURCE_MAX_BYTES = METHOD_DOCUMENT_MAX_BYTES


class RepoProfileLoadResult:
    def __init__(
        self,
        *,
        profile: TargetProfile,
        status: str,
        limitations: list[str] | None = None,
        error_class: str | None = None,
    ) -> None:
        self.profile = profile
        self.status = status
        self.limitations = limitations or []
        self.error_class = error_class


def load_repo_profile(repo_root: Path | str, *, target_repo: str | None = None) -> RepoProfileLoadResult:
    root = Path(repo_root)
    profile_path = root / ".aiops" / "repo-profile.yaml"

    raw = _load_yaml_file(profile_path)
    if raw.absent:
        profile = TargetProfile(
            target_repo=target_repo,
            artifacts=[],
            limitations=["repo_profile_missing"],
        )
        return RepoProfileLoadResult(
            profile=profile,
            status="degraded",
            limitations=["repo_profile_missing"],
        )

    if raw.error_class:
        # Present but unusable (a link, a hard link, a FIFO, oversize, unreadable, undecodable, unparsable or beyond
        # the structural bounds) is a failed profile, never "profile missing".
        profile = TargetProfile(target_repo=target_repo, artifacts=[])
        return RepoProfileLoadResult(
            profile=profile,
            status="failed",
            limitations=[_source_limitation("repo_profile", raw.error_class)],
            error_class=raw.error_class,
        )

    profile_data = raw.data if isinstance(raw.data, dict) else {}
    profile_data.setdefault("schema_version", TARGET_PROFILE_SCHEMA)
    if target_repo and not profile_data.get("target_repo"):
        profile_data["target_repo"] = target_repo

    unsupported = [
        field
        for field in REPO_PROFILE_UNSUPPORTED_C2_FIELDS
        if field in profile_data and (field not in REPO_PROFILE_INLINE_C2_SOURCE_FIELDS or profile_data[field] is not None)
    ]
    if unsupported:
        profile = TargetProfile(target_repo=target_repo, artifacts=[])
        return RepoProfileLoadResult(
            profile=profile,
            status="failed",
            limitations=[f"repo_profile_unsupported_c2_field:{field}" for field in unsupported],
            error_class="profile_invalid",
        )

    limitations: list[str] = []
    try:
        profile = TargetProfile.model_validate(profile_data)
    except ValidationError:
        profile = TargetProfile(target_repo=target_repo, artifacts=[])
        return RepoProfileLoadResult(
            profile=profile,
            status="failed",
            limitations=["repo_profile_invalid"],
            error_class="profile_invalid",
        )

    domain_contracts = _load_optional_yaml(root / ".aiops" / "domain-contracts.yaml", "domain_contracts", limitations)
    review_packs = _load_optional_yaml(root / ".aiops" / "review-packs.yaml", "review_packs", limitations)
    method_guidance = _load_method_guidance_sidecar(root, limitations)
    if "method_guidance_yaml_invalid" in limitations:
        # A sidecar that exists but cannot be parsed is INVALID, never ABSENT: its requiredness is unknowable, so
        # it is carried as a non-mapping value which normalization reports as invalid_source_method_guidance:
        # MALFORMED_SHAPE (critical) instead of silently reading as "no guidance declared".
        method_guidance = []

    # R5-A: the typed carrier stringifies mapping keys on serialization (`on:` -> "true"; `1` and "1" collide and the
    # first declaration is lost), so a C2 source with a non-string key is NOT carried faithfully: fail closed with the
    # same profile-invalid semantics as the other undeliverable C2 declarations.
    non_string_sources = [
        name
        for name, data in (
            ("domain_contracts", domain_contracts),
            ("method_guidance", method_guidance),
            ("review_packs", review_packs),
        )
        if _has_non_string_mapping_key(data)
    ]
    if non_string_sources:
        return RepoProfileLoadResult(
            profile=TargetProfile(target_repo=target_repo, artifacts=[]),
            status="failed",
            limitations=[f"{name}_non_string_mapping_key" for name in non_string_sources],
            error_class="profile_invalid",
        )

    profile.domain_contracts = domain_contracts
    profile.review_packs = review_packs
    profile.method_guidance = method_guidance
    # Loader-owned: the only place that has the checkout. The author document never carries its own verdict.
    profile.method_guidance_verification = _verify_method_guidance_sources(
        root, method_guidance, target_repo  # trusted CLI anchor only: the profile label is author-editable
    )
    profile.limitations = [*profile.limitations, *limitations]
    status = "degraded" if limitations else "complete"
    return RepoProfileLoadResult(profile=profile, status=status, limitations=limitations)


class _YamlLoadResult:
    def __init__(
        self,
        *,
        data: Any = None,
        error_class: str | None = None,
        absent: bool = False,
        has_non_string_key: bool = False,
    ) -> None:
        self.data = data
        self.error_class = error_class
        self.absent = absent
        self.has_non_string_key = has_non_string_key


def _relative_to_root(path: Path) -> tuple[Path, str]:
    """`<root>/.aiops/<file>` -> (trusted repository root, `.aiops/<file>`): the loaders keep their path-based seam."""
    return path.parent.parent, f"{path.parent.name}/{path.name}"


def _source_limitation(name: str, error_class: str) -> str:
    # `yaml_invalid` keeps the historical, receipt-pinned code; every other typed state names its own cause.
    return f"{name}_{error_class}"


def _load_yaml_file(path: Path) -> _YamlLoadResult:
    """One acquisition (no link followed, bounded, regular, single link), then decode, parse, structural admission.

    ABSENT only when the entry does not exist; every other state is a typed `error_class`:
    source_unsafe / source_unreadable / source_oversize / source_unsupported (acquisition), decode_invalid,
    yaml_invalid, resource_limit (parser recursion) and structure_limit (cycle / depth / expanded nodes).
    """
    anchor, relative = _relative_to_root(path)
    acquisition = acquire_source(anchor, relative, max_bytes=YAML_SOURCE_MAX_BYTES)
    if acquisition.state == SOURCE_STATE_ABSENT:
        return _YamlLoadResult(absent=True)
    if acquisition.state != SOURCE_STATE_PRESENT or acquisition.data is None:
        return _YamlLoadResult(error_class=f"source_{acquisition.state.lower()}")
    try:
        text = acquisition.data.decode("utf-8")
    except UnicodeDecodeError:
        return _YamlLoadResult(error_class="decode_invalid")
    try:
        document = parse_yaml_text(text) or {}
    except RecursionError:
        return _YamlLoadResult(error_class="resource_limit")
    except Exception:  # noqa: BLE001 - YAMLError plus ValueError/KeyError/AttributeError raised while constructing tags
        return _YamlLoadResult(error_class="yaml_invalid")
    verdict = scan_structure(document)
    if verdict.limit is not None:
        return _YamlLoadResult(error_class="structure_limit")
    return _YamlLoadResult(data=document, has_non_string_key=verdict.has_non_string_key)


# A C2 source file that exists but cannot be parsed is INVALID, never ABSENT (OBL-CL2-03): carrying `None` would read
# as "no contract declared" and let the review stay conclusive. The typed carrier admits only a mapping/list, so the
# unparsable source travels as a type-valid document that the existing grammar rejects as MALFORMED_SHAPE
# (`invalid_source_contract:` / `invalid_source_review_packs:`, both already critical). It never reaches a consumer
# as a declaration: normalization returns no rows for it.
_UNPARSABLE_SOURCE = "__source_yaml_unparsable__"
_UNPARSABLE_SOURCE_CARRIERS: dict[str, dict[str, Any]] = {
    "domain_contracts": {"rules": _UNPARSABLE_SOURCE},  # `rules` must be a list: not a list -> MALFORMED_SHAPE
    "review_packs": {"packs": {"unparsable": _UNPARSABLE_SOURCE}},  # a pack value must be a mapping -> MALFORMED_SHAPE
}


def _load_optional_yaml(path: Path, name: str, limitations: list[str]) -> Any:
    loaded = _load_yaml_file(path)
    if loaded.absent:
        return None
    if loaded.error_class:
        limitations.append(_source_limitation(name, loaded.error_class))
        return copy.deepcopy(_UNPARSABLE_SOURCE_CARRIERS[name])
    return loaded.data


def _git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git object identity, not security


class _DuplicateKeyRefusingLoader(SafeSourceLoader):
    """Stock SafeLoader resolves a duplicate mapping key last-wins and silently; for the MethodGuidance sidecar a
    repeated key (e.g. a second `guidance:` or `requiredness:`) would change semantics unnoticed, so it is invalid."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
                seen.add(key)
            except TypeError:
                continue
        return super().construct_mapping(node, deep)


def _load_method_guidance_sidecar(root: Path, limitations: list[str]) -> Any:
    """None only when the directory entry itself does not exist; a typed `method_guidance_yaml_invalid` (carried as a
    non-mapping so it normalizes to INVALID, never ABSENT) for anything else that is not a readable, bounded, regular
    UTF-8 YAML document INSIDE the checkout: any link (external, in-checkout, dangling or looping, on the final
    component or on `.aiops`), a hard link, a directory, a FIFO, an oversize file, undecodable, unparsable,
    key-ambiguous or structurally unbounded content. Presence is decided by the same open that reads the bytes."""
    acquisition = acquire_source(root, f".aiops/{METHOD_GUIDANCE_SIDECAR}", max_bytes=YAML_SOURCE_MAX_BYTES)
    if acquisition.state == SOURCE_STATE_ABSENT:
        return None
    try:
        if acquisition.state != SOURCE_STATE_PRESENT or acquisition.data is None:
            raise ValueError("method guidance sidecar is not a bounded regular file inside the checkout")
        document = yaml.load(acquisition.data.decode("utf-8"), Loader=_DuplicateKeyRefusingLoader) or {}  # noqa: S506 - SafeLoader subclass
        if scan_structure(document).limit is not None:
            raise ValueError("method guidance sidecar exceeds the structural bounds")
    except Exception:  # noqa: BLE001 - any failure to parse the sidecar is INVALID, never ABSENT (PyYAML tags raise KeyError/AttributeError)
        limitations.append("method_guidance_yaml_invalid")
        return []
    return document


def _read_bounded_in_checkout(root: Path, relative: str) -> bytes | None:
    """Content of the regular file `relative` below `root` up to the method-document bound, or None.

    The implementation is `source_boundary.acquire_source` (descriptor-relative, `O_NOFOLLOW` per component,
    `st_nlink == 1`, `O_NONBLOCK`, `fstat` on the opened descriptor, bounded read): one authority for every v1 source
    reader. A caller that must tell absent from unsafe uses `acquire_source` directly."""
    acquisition = acquire_source(root, relative, max_bytes=_METHOD_SOURCE_MAX_BYTES)
    return acquisition.data if acquisition.state == SOURCE_STATE_PRESENT else None


def _verify_method_guidance_sources(root: Path, document: Any, target_repo: str | None) -> dict[str, str] | None:
    """Binds each declared method source (and each selected_text) to the checkout the review runs on.

    Source verdicts, keyed by source_id:
      CURRENT      the declared path is a regular file inside `root`, reached without following a link, whose git blob
                   oid equals the declared `blob`.
      STALE        the file is there and its oid differs: the reference describes a document that no longer exists.
      UNVERIFIABLE anything else (other repository, absent / unreadable / too large / outside the checkout / a link on
                   the path / a path the normalizer would not accept). It never raises.
    Item verdicts, keyed "<method_id>/<section_id>" (only for a CURRENT source):
      BOUND / BOUND_REDACTED / UNBOUND  whether the item's selected_text is a literal (whitespace-insensitive) excerpt of that document.
    The path is canonicalized by the SAME function normalization uses, so the file verified is the file emitted.
    A declaration the sidecar grammar rejects is normalized INVALID and this mapping is never consulted for it.
    """
    if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
        return None
    states: dict[str, str] = {}
    texts: dict[str, str] = {}
    for source in document["sources"]:
        if not isinstance(source, dict):
            continue
        source_id, path, blob, repository = (source.get(key) for key in ("source_id", "path", "blob", "repository"))
        if not all(isinstance(value, str) and value for value in (source_id, path, blob)):
            continue
        state = METHOD_SOURCE_UNVERIFIABLE
        same_repository = (
            isinstance(repository, str)
            and bool(target_repo)
            and repository.strip().casefold() == str(target_repo).strip().casefold()
        )
        canonical = canonical_method_source_path(path)
        if same_repository and canonical:
            data = _read_bounded_in_checkout(root, canonical)
            if data is not None:
                state = METHOD_SOURCE_CURRENT if _git_blob_oid(data) == blob.strip() else METHOD_SOURCE_STALE
                if state == METHOD_SOURCE_CURRENT:
                    texts[source_id] = normalize_method_text(data.decode("utf-8", errors="replace"))
        states.setdefault(source_id, state)
    verdicts: dict[str, str] = dict(states)
    for item in document.get("guidance") if isinstance(document.get("guidance"), list) else []:
        if not isinstance(item, dict):
            continue
        method_id, section_id, source_id, text = (item.get(k) for k in ("method_id", "section_id", "source_id", "selected_text"))
        if not all(isinstance(v, str) for v in (method_id, section_id, source_id, text)) or source_id not in texts:
            continue
        needle = normalize_method_text(text)
        if not needle or needle not in texts[source_id]:
            verdict = METHOD_TEXT_UNBOUND
        elif redact_value(text, RedactionState()) != text or sanitize_artifact_value(text) != text:
            verdict = METHOD_TEXT_BOUND_REDACTED
        else:
            verdict = METHOD_TEXT_BOUND
        verdicts[f"{method_id}/{section_id}"] = verdict
    return dict(sorted(verdicts.items())) or None


def _has_non_string_mapping_key(value: Any) -> bool:
    """Iterative and bounded (no recursion on depth); a document the scan rejects was already INVALID upstream."""
    return scan_structure(value).has_non_string_key
