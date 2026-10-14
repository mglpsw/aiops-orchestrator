"""Composition of the two C2 carriers: an unparsable contract source (this branch) and MethodGuidance (PR #373).

Each fault must keep its own typed critical limitation and must not poison, mask or be masked by the other:

    PresentButUnparsable(domain-contracts / review-packs) -> invalid_source_contract / invalid_source_review_packs
    PresentButUnparsable(method-guidance sidecar)         -> invalid_source_method_guidance
    valid MethodGuidance sidecar + source                 -> CURRENT / BOUND, carried unchanged

Everything starts from real files through the canonical producer and the shipped CLI chain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent_review.repo_profile import load_repo_profile
from tests.agent_review.test_v1_method_guidance_carrier import (  # noqa: F401  (autouse offline-env fixture)
    DOMAINS,
    _offline_dev_environment,
    canonical_run,
    cli_chain,
    make_target,
    read_json,
    sidecar,
)

DOMAIN = DOMAINS["scheduling"]
VALID_CONTRACTS = {"c1": {"description": "d1", "paths": ["backend/api/*"], "rules": ["r1"]}}
UNPARSABLE = "rules: [unterminated\n"
SOURCE_FILE = {"domain_contracts": "domain-contracts.yaml", "review_packs": "review-packs.yaml"}
SOURCE_LIMITATION = {
    "domain_contracts": "invalid_source_contract:MALFORMED_SHAPE",
    "review_packs": "invalid_source_review_packs:MALFORMED_SHAPE",
}
METHOD_INVALID = "invalid_source_method_guidance:MALFORMED_SHAPE"


def _target(tmp_path: Path, *, name: str, kind: str | None = None, method_guidance: Any = "default") -> Any:
    target = make_target(
        tmp_path,
        DOMAIN,
        name=name,
        method_guidance=method_guidance,
        contracts=VALID_CONTRACTS,
        packs={"packs": {"p1": {"domain_contract": "c1"}}} if kind == "review_packs" else None,
    )
    if kind is not None:
        (target.repo / ".aiops" / SOURCE_FILE[kind]).write_text(UNPARSABLE, encoding="utf-8")
    return target


@pytest.mark.parametrize("kind", ["domain_contracts", "review_packs"])
def test_unparsable_contract_source_is_critical_and_leaves_valid_method_guidance_intact(tmp_path: Path, kind: str) -> None:
    target = _target(tmp_path, name="a", kind=kind)
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert f"{kind}_yaml_invalid" in loaded.limitations
    assert loaded.profile.method_guidance == sidecar(DOMAIN)
    assert loaded.profile.method_guidance_verification == {
        "aocm": "CURRENT",
        "AOCM/closure-obligations": "BOUND",
        "AOCM/evidence-vocabulary": "BOUND",
    }
    run = canonical_run(target)
    assert SOURCE_LIMITATION[kind] in run.plan.limitations and run.plan.status == "degraded"
    assert METHOD_INVALID not in run.plan.limitations
    assert not any(item.startswith("method_guidance_source_") for item in run.plan.limitations), run.plan.limitations
    assert run.intake.target_profile["method_guidance"] == sidecar(DOMAIN)


def test_unparsable_method_sidecar_does_not_poison_valid_contract_sources(tmp_path: Path) -> None:
    target = _target(tmp_path, name="b", method_guidance="version: 1\nsources: [unterminated\n")
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert "method_guidance_yaml_invalid" in loaded.limitations
    assert loaded.profile.domain_contracts == VALID_CONTRACTS
    run = canonical_run(target)
    assert METHOD_INVALID in run.plan.limitations and run.plan.status == "degraded"
    assert not any(item.startswith(("invalid_source_contract:", "invalid_source_review_packs:")) for item in run.plan.limitations)


@pytest.mark.parametrize("kind", ["domain_contracts", "review_packs"])
def test_both_unparsable_keep_both_typed_limitations_and_the_gate_does_not_conclude(tmp_path: Path, kind: str) -> None:
    target = _target(tmp_path, name="c", kind=kind, method_guidance="version: 1\nsources: [unterminated\n")
    run = canonical_run(target)
    assert SOURCE_LIMITATION[kind] in run.plan.limitations and METHOD_INVALID in run.plan.limitations
    assert run.plan.status == "degraded"
    paths = cli_chain(target, tmp_path / "chain")
    assert read_json(paths["gate"])["status"] != "passed"
    assert not any("__source_yaml_unparsable__" in item.read_text(encoding="utf-8") for item in (tmp_path / "chain").rglob("*.json") if "intake" not in item.name)


def test_valid_contracts_and_valid_method_guidance_stay_conclusive(tmp_path: Path) -> None:
    """Positive control: neither carrier's repair rejects the legitimate pair (no vacuous refusal)."""
    target = _target(tmp_path, name="d")
    run = canonical_run(target)
    assert not any(item.startswith(("invalid_source_", "required_")) for item in run.plan.limitations), run.plan.limitations
    assert json.dumps(run.intake.target_profile["domain_contracts"], sort_keys=True) == json.dumps(VALID_CONTRACTS, sort_keys=True)
