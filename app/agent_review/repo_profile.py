"""Target repository profile loading for offline AgentReview intake."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.agent_review.schemas import TARGET_PROFILE_SCHEMA, TargetProfile


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

    profile.domain_contracts = domain_contracts
    profile.review_packs = review_packs
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

