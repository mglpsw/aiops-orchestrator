"""Target repository profile loading for offline AgentReview intake."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.agent_review.payload_cost_model import METHOD_SOURCE_CURRENT, METHOD_SOURCE_STALE, METHOD_SOURCE_UNVERIFIABLE
from app.agent_review.schemas import TARGET_PROFILE_SCHEMA, TargetProfile


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
_METHOD_SOURCE_MAX_BYTES = 4 * 1024 * 1024


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

    if not profile_path.exists():
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

    raw = _load_yaml_file(profile_path)
    if raw.error_class:
        profile = TargetProfile(target_repo=target_repo, artifacts=[])
        return RepoProfileLoadResult(
            profile=profile,
            status="failed",
            limitations=["repo_profile_yaml_invalid"],
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
    method_guidance = _load_optional_yaml(root / ".aiops" / METHOD_GUIDANCE_SIDECAR, "method_guidance", limitations)
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
        root, method_guidance, profile.target_repo or target_repo
    )
    profile.limitations = [*profile.limitations, *limitations]
    status = "degraded" if limitations else "complete"
    return RepoProfileLoadResult(profile=profile, status=status, limitations=limitations)


class _YamlLoadResult:
    def __init__(self, *, data: Any = None, error_class: str | None = None) -> None:
        self.data = data
        self.error_class = error_class


def _load_yaml_file(path: Path) -> _YamlLoadResult:
    try:
        return _YamlLoadResult(data=yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except yaml.YAMLError:
        return _YamlLoadResult(error_class="yaml_invalid")


def _load_optional_yaml(path: Path, name: str, limitations: list[str]) -> Any:
    if not path.exists():
        return None
    loaded = _load_yaml_file(path)
    if loaded.error_class:
        limitations.append(f"{name}_yaml_invalid")
        return None
    return loaded.data


def _git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git object identity, not security


def _verify_method_guidance_sources(root: Path, document: Any, target_repo: str | None) -> dict[str, str] | None:
    """Binds each declared method source to the checkout the review runs on.

    CURRENT      the declared path is a regular file inside `root` whose git blob oid equals the declared `blob`.
    STALE        the file is there and its oid differs: the reference describes a document that no longer exists.
    UNVERIFIABLE anything else (other repository, file absent / unreadable / too large / outside the checkout).
    A declaration the sidecar grammar rejects is not verified here: normalization marks the source INVALID and the
    verification mapping is never consulted for it.
    """
    if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
        return None
    resolved_root = root.resolve()
    states: dict[str, str] = {}
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
        if same_repository:
            candidate = (resolved_root / path.replace("\\", "/")).resolve()
            try:
                inside = candidate.is_relative_to(resolved_root)
                data = candidate.read_bytes() if inside and candidate.is_file() else None
            except OSError:
                data = None
            if data is not None and len(data) <= _METHOD_SOURCE_MAX_BYTES:
                state = METHOD_SOURCE_CURRENT if _git_blob_oid(data) == blob.strip().lower() else METHOD_SOURCE_STALE
        states.setdefault(source_id, state)
    return dict(sorted(states.items())) or None


def _has_non_string_mapping_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(not isinstance(key, str) or _has_non_string_mapping_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_non_string_mapping_key(item) for item in value)
    return False

