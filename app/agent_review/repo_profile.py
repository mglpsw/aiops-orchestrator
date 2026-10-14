"""Target repository profile loading for offline AgentReview intake."""

from __future__ import annotations

import copy
import hashlib
import os
import stat
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
    def __init__(self, *, data: Any = None, error_class: str | None = None) -> None:
        self.data = data
        self.error_class = error_class


def _load_yaml_file(path: Path) -> _YamlLoadResult:
    try:
        return _YamlLoadResult(data=yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except yaml.YAMLError:
        return _YamlLoadResult(error_class="yaml_invalid")


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
    if not path.exists():
        return None
    loaded = _load_yaml_file(path)
    if loaded.error_class:
        limitations.append(f"{name}_yaml_invalid")
        return copy.deepcopy(_UNPARSABLE_SOURCE_CARRIERS[name])
    return loaded.data


def _git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git object identity, not security


class _DuplicateKeyRefusingLoader(yaml.SafeLoader):
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
    component or on `.aiops`), a directory, a FIFO, an oversize file, undecodable, unparsable or key-ambiguous
    content. `Path.exists()` follows links and would report a dangling link as absent; the content of a link target
    is never read, so a host file can never become `TargetProfile.method_guidance`."""
    if not os.path.lexists(root / ".aiops" / METHOD_GUIDANCE_SIDECAR):
        return None
    try:
        data = _read_bounded_in_checkout(root, f".aiops/{METHOD_GUIDANCE_SIDECAR}")
        if data is None:
            raise ValueError("method guidance sidecar is not a bounded regular file inside the checkout")
        document = yaml.load(data.decode("utf-8"), Loader=_DuplicateKeyRefusingLoader) or {}  # noqa: S506 - SafeLoader subclass
    except Exception:  # noqa: BLE001 - any failure to parse the sidecar is INVALID, never ABSENT (PyYAML tags raise KeyError/AttributeError)
        limitations.append("method_guidance_yaml_invalid")
        return []
    return document


def _read_bounded_in_checkout(root: Path, relative: str) -> bytes | None:
    """Content of the regular file `relative` (a POSIX path below `root`) up to the bound, or None. Never reads past
    the bound and never follows a link.

    Containment is structural, not a post-hoc comparison: `root` is the trusted CLI anchor and is opened once; every
    further component is opened relative to the previous directory descriptor with `O_NOFOLLOW` (directories also
    `O_DIRECTORY`), so a link on ANY component (`.aiops` itself, an intermediate directory, the file) is refused
    (ELOOP/ENOTDIR) and the object that is size-checked (`fstat` on the opened descriptor) is the object that is read.
    There is no resolve-then-open window. `O_NONBLOCK` keeps a FIFO from blocking the open; it is then refused as a
    non-regular file. A platform without `O_NOFOLLOW` or descriptor-relative open fails closed (None)."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        return None
    parts = relative.split("/")
    if not parts or any(part in ("", ".", "..") or "\0" in part for part in parts):
        return None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.open(root, directory_flags)
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        status = os.fstat(file_fd)
        # A hard link is a second name for an inode that may live outside the checkout, and `O_NOFOLLOW` does not see
        # it: a multiply-linked file is not provably "inside the checkout", so it is refused (fail closed).
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_size > _METHOD_SOURCE_MAX_BYTES:
            return None
        chunks: list[bytes] = []
        remaining = _METHOD_SOURCE_MAX_BYTES + 1
        while remaining > 0:
            block = os.read(file_fd, min(remaining, 1024 * 1024))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b"".join(chunks)
    except (OSError, ValueError):
        return None
    finally:
        for descriptor in (file_fd, directory_fd):
            if descriptor >= 0:
                os.close(descriptor)
    return data if len(data) <= _METHOD_SOURCE_MAX_BYTES else None


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
    if isinstance(value, dict):
        return any(not isinstance(key, str) or _has_non_string_mapping_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_non_string_mapping_key(item) for item in value)
    return False

