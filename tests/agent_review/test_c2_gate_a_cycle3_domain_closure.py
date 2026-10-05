"""Gate A cycle-3 domain closure (PR #372, review 5409041457).

Seven findings, four causal classes, one transversal property:

    Mode -> Grammar -> Normalization -> Identity -> Applicability -> Relation
         -> RequiredSource -> RequiredContext -> ObservableResult

C1_MODERN_ADMISSION_TOTALITY      4179993590 4179993594 4179993602
C2_EXPLICIT_RELATION_REQUIRED_SOURCE 4179993600
C3_GENERIC_IDENTITY_AUTHORITY     4179993596
C4_LEGACY_DIFFERENTIAL_FIREWALL   4179993605 4179993607  (+ hidden siblings,
                                  see test_c2_legacy_differential_oracle.py)

Every countermodel below is a witness against the production path
(`normalize_*` / `contracts_context`), never a re-implementation of it.
Ablations mutate one production seam, prove the SAME witness goes RED, then
restore and prove it GREEN again (monkeypatch teardown).
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from app.agent_review import chunk_payload_builder, payload_cost_model as pcm
from app.agent_review.schemas import ReviewIntake

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = REPO_ROOT / "app" / "agent_review"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intake(profile: dict[str, Any]) -> ReviewIntake:
    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "example/target",
            "target_profile": profile,
            "artifacts": {},
            "artifact_status": [],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-10-05T00:00:00Z",
            "status": "complete",
        }
    )


def _ctx(
    profile: dict[str, Any],
    *,
    files: tuple[str, ...] = ("backend/api/shifts.py",),
    chunk_contracts: tuple[str, ...] = (),
    selected: str | None = None,
    group: str = "other",
) -> tuple[dict[str, Any], list[str]]:
    return pcm.contracts_context(
        _intake(profile),
        chunk_files=list(files),
        chunk_contracts=list(chunk_contracts),
        chunk_id="chunk-1",
        selected_contract_pack=selected,
        semantic_group=group,
    )


def _critical(limits: list[str], prefix: str) -> list[str]:
    return [lim for lim in limits if lim.startswith(prefix)]


def _no_not_relevant(limits: list[str]) -> bool:
    return not any(lim.startswith("contracts_context_not_relevant:") for lim in limits)


INVALID_PATH_IDENTITIES = [
    "/etc/passwd",
    "../foo",
    "foo/../../bar",
    "~/secret",
    "C:\\Windows\\system.ini",
]

CONTRACT_EXACT_PATH_FIELDS = ("path", "file_path", "files", "paths", "source_files", "related_files")
PACK_EXACT_PATH_FIELDS = ("paths",)


def _contract_with(field: str, value: str) -> dict[str, Any]:
    if field in ("path", "file_path"):
        return {"api": {"description": "api rules", field: value}}
    return {"api": {"description": "api rules", field: [value]}}


# ---------------------------------------------------------------------------
# C1 -- ModernSemanticPath -> ValidRepositoryRelativeIdentity (4179993590)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", CONTRACT_EXACT_PATH_FIELDS)
@pytest.mark.parametrize("value", INVALID_PATH_IDENTITIES)
def test_cm_c3_path_invalid_identity_contract(field: str, value: str) -> None:
    """CM-C3-PATH-ABSOLUTE / CM-C3-PATH-TRAVERSAL (4179993590): every census field x every
    non-repository-relative shape invalidates the whole modern domain_contracts SOURCE."""
    _, state, subtype, limits = pcm.normalize_domain_contracts(_contract_with(field, value))
    assert state == pcm.SOURCE_STATE_INVALID, (field, value)
    assert subtype == pcm.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_contract:INVALID_IDENTITY" in limits

    ctx, ctx_limits = _ctx({"domain_contracts": _contract_with(field, value)})
    assert _critical(ctx_limits, "invalid_source_contract:")
    assert _no_not_relevant(ctx_limits), "an invalid source must never collapse into not_relevant"
    assert ctx["domain_contracts"] == []


def test_cm_c3_path_mixed_valid_invalid_invalidates_whole_source() -> None:
    """CM-C3-PATH-MIXED-VALID-INVALID: never discard-invalid + retain-valid + PRESENT_VALID."""
    doc = {
        "api": {"description": "api", "paths": ["backend/api/a.py", "../escape.py"]},
        "ok": {"description": "ok", "paths": ["backend/ok.py"]},
    }
    contracts, state, subtype, limits = pcm.normalize_domain_contracts(doc)
    assert state == pcm.SOURCE_STATE_INVALID
    assert contracts == []
    assert "invalid_source_contract:INVALID_IDENTITY" in limits


@pytest.mark.parametrize("value", INVALID_PATH_IDENTITIES)
def test_cm_c3_pattern_invalid_identity_contract(value: str) -> None:
    """CM-C3-PATTERN-ABSOLUTE / CM-C3-PATTERN-TRAVERSAL: patterns keep GlobSyntax AND RepositoryBoundary."""
    doc = {"api": {"description": "api", "patterns": [value]}}
    _, state, subtype, limits = pcm.normalize_domain_contracts(doc)
    assert state == pcm.SOURCE_STATE_INVALID
    assert subtype == pcm.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_contract:INVALID_IDENTITY" in limits


@pytest.mark.parametrize("field", PACK_EXACT_PATH_FIELDS + ("patterns",))
@pytest.mark.parametrize("value", INVALID_PATH_IDENTITIES)
def test_cm_c3_pack_path_and_pattern_invalid_identity(field: str, value: str) -> None:
    """CM-C3-PACK-PATH-ABSOLUTE / -TRAVERSAL / CM-C3-PACK-PATTERN-TRAVERSAL."""
    doc = {"packs": {"p1": {"description": "p1", field: [value]}}}
    packs, bindings, state, subtype, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_INVALID, (field, value)
    assert subtype == pcm.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_review_packs:INVALID_IDENTITY" in limits
    assert packs == []

    ctx, ctx_limits = _ctx({"review_packs": doc})
    assert _critical(ctx_limits, "invalid_source_review_packs:")
    assert _no_not_relevant(ctx_limits)


def test_cm_c3_pack_mixed_valid_invalid_invalidates_whole_source() -> None:
    doc = {"packs": {"p1": {"paths": ["backend/api/a.py", "/etc/passwd"]}, "p2": {"paths": ["docs/x.md"]}}}
    packs, _, state, _, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_INVALID
    assert packs == []


def test_pc_c3_exact_repo_path_and_normalized_relative_path() -> None:
    """PC-C3-EXACT-REPO-PATH / PC-C3-NORMALIZED-RELATIVE-PATH."""
    doc = {
        "api": {"description": "api", "paths": ["backend/api/shifts.py"]},
        "norm": {"description": "norm", "paths": ["./backend//api/./shifts.py"], "patterns": ["./backend/**"]},
    }
    contracts, state, _, limits = pcm.normalize_domain_contracts(doc)
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and limits == []
    norm = next(c for c in contracts if c["id"] == "norm")
    assert norm["paths"] == ["backend/api/shifts.py"], "identity is the canonical repository-relative path"
    assert norm["patterns"] == ["backend/**"]
    ctx, ctx_limits = _ctx({"domain_contracts": doc})
    assert {c["id"] for c in ctx["domain_contracts"]} == {"api", "norm"}
    assert ctx_limits == []


@pytest.mark.parametrize("pattern", ["backend/api/*.py", "backend/**", "**/*_test.py", "frontend/src/components/operational_slot_*"])
def test_pc_c3_safe_glob(pattern: str) -> None:
    """PC-C3-SAFE-GLOB: glob syntax stays admitted inside the repository boundary (contract + pack)."""
    _, state, _, _ = pcm.normalize_domain_contracts({"api": {"description": "d", "patterns": [pattern]}})
    assert state == pcm.SOURCE_STATE_PRESENT_VALID
    _, _, p_state, _, _ = pcm.normalize_review_packs({"packs": {"p": {"patterns": [pattern], "paths": [pattern]}}})
    assert p_state == pcm.SOURCE_STATE_PRESENT_VALID


def test_c3_path_and_pattern_identity_are_distinct_authorities() -> None:
    """RepositoryPathIdentity / RepositoryPatternIdentity / DisplayPath are three authorities;
    DisplayPath never decides validity."""
    assert pcm.canonical_repo_path("./a//b.py") == "a/b.py"
    assert pcm.canonical_repo_pattern("./a//**/*.py") == "a/**/*.py"
    for bad in INVALID_PATH_IDENTITIES:
        with pytest.raises(pcm.PathIdentityError):
            pcm.canonical_repo_pattern(bad)
        with pytest.raises(pcm.PathIdentityError):
            pcm.canonical_repo_path(bad)
    # sanitize_display_path accepts (redacts) exactly what identity authorities reject:
    # proof that display cannot be used as the admission authority.
    assert pcm.sanitize_display_path("/etc/passwd") == "[LOCAL_PATH_REDACTED]"
    assert pcm.sanitize_display_path("../foo") == "../foo"


def test_c3_path_bearing_census_is_exact() -> None:
    """The C1 path-bearing census is a closed, declared set; a production consumer reading another
    path-bearing field of C2 must be added here."""
    assert set(pcm.MODERN_CONTRACT_EXACT_PATH_FIELDS) == set(CONTRACT_EXACT_PATH_FIELDS)
    assert set(pcm.MODERN_CONTRACT_PATTERN_FIELDS) == {"patterns"}
    assert set(pcm.MODERN_PACK_EXACT_PATH_FIELDS) == {"paths"}
    assert set(pcm.MODERN_PACK_PATTERN_FIELDS) == {"patterns"}
    # every field `_item_scope_paths` reads for contract/pack rows is inside the census
    src = (PRODUCTION_ROOT / "payload_cost_model.py").read_text(encoding="utf-8")
    scope_fn = src.split("def _item_scope_paths", 1)[1].split("def _paths_from_item", 1)[0]
    read_fields = set(re.findall(r'"([a-z_]+)"', scope_fn.split("paths: set[str]", 1)[1]))
    row_fields = read_fields & {"file_path", "path", "files", "paths", "source_files", "related_files"}
    assert row_fields == set(CONTRACT_EXACT_PATH_FIELDS)


def test_c3_legacy_path_semantics_are_not_modern() -> None:
    """CM-C4-LEGACY-PATH-IDENTITY-NOT-MODERN: frozen legacy flat rows keep the baseline's lenient
    path handling (unresolvable path silently non-matching); modern strictness must not leak."""
    contracts, state, _, limits = pcm.normalize_domain_contracts(
        {"rules": [{"id": "legacy", "description": "d", "paths": ["/etc/passwd"]}]}
    )
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and limits == []
    assert [c["id"] for c in contracts] == ["legacy"]


# -- ablation --------------------------------------------------------------


def test_ab_c1_modern_path_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C1-MODERN-PATH-IDENTITY (4179993590): same witness GREEN -> mutate the single path-bearing authority
    -> RED -> restore -> GREEN."""
    doc_contract = {"api": {"description": "d", "paths": ["/etc/passwd"]}}
    doc_pack = {"packs": {"p": {"patterns": ["../x/*"]}}}

    def witness() -> tuple[str, str]:
        def observe(call: Callable[[], str]) -> str:
            try:
                return call()
            except pcm.PathIdentityError:  # defence in depth: normalization itself refuses the identity
                return "RAISED"

        return (
            observe(lambda: pcm.normalize_domain_contracts(doc_contract)[1]),
            observe(lambda: pcm.normalize_review_packs(doc_pack)[2]),
        )

    assert witness() == (pcm.SOURCE_STATE_INVALID, pcm.SOURCE_STATE_INVALID)
    with monkeypatch.context() as m:
        m.setattr(pcm, "_modern_path_bearing_invalid", lambda *a, **k: False)
        # without the admission authority the invalid identity is no longer a typed INVALID source
        assert witness() == ("RAISED", "RAISED"), "mutant must go RED"
    assert witness() == (pcm.SOURCE_STATE_INVALID, pcm.SOURCE_STATE_INVALID)


# ---------------------------------------------------------------------------
# C1 -- ReviewPackDocumentEnvelopeGrammar (4179993594)
# ---------------------------------------------------------------------------


def _real_target_review_packs_mirror() -> dict[str, Any]:
    """fixture_role: structural_mirror -- shape of AgentEscala@b281ca5d2872117b1c128cabb1735e264f024eaf
    .aiops/review-packs.yaml (top-level keys: version, updated, packs; pack value keys: description,
    critical, allow_external_review, recommended_review_preset, require_full_diff,
    require_final_files_when_available, paths, domain_contract, notes). authority_effect: none."""
    return {
        "version": "1.2",
        "updated": "2026-05-07",
        "packs": {
            "calendar": {
                "description": "Calendario de plantoes",
                "critical": True,
                "allow_external_review": True,
                "recommended_review_preset": "review:deep",
                "require_full_diff": True,
                "require_final_files_when_available": True,
                "paths": [
                    "frontend/src/pages/calendar_page.jsx",
                    "frontend/src/components/operational_slot_*",
                    "backend/api/shifts.py",
                    "backend/domain/shift_*.py",
                ],
                "domain_contract": "calendar",
            },
            "notifications": {
                "description": "Notificacoes",
                "critical": False,
                "allow_external_review": False,
                "recommended_review_preset": "review:code",
                "paths": ["backend/services/notification*.py"],
                "domain_contract": "notifications",
                "notes": "metadata only",
            },
            "docs_only": {
                "description": "Docs",
                "critical": False,
                "allow_external_review": False,
                "recommended_review_preset": "review:code-fast",
                "paths": ["docs/**"],
            },
        },
    }


def test_cm_c3_envelope_unknown_key() -> None:
    """CM-C3-ENVELOPE-UNKNOWN-KEY (4179993594)."""
    doc = {"packs": {"p1": {"paths": ["backend/**"]}}, "mystery": {"x": 1}}
    packs, bindings, state, subtype, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_INVALID
    assert subtype == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits
    assert packs == []


def test_cm_c3_envelope_contract_binding_typo_never_becomes_empty_bindings() -> None:
    """CM-C3-ENVELOPE-CONTRACT_BINDING-TYPO: ignore typo -> bindings={} -> PRESENT_VALID is the defect."""
    profile = {
        "review_packs": {
            "packs": {"p1": {"paths": ["backend/**"]}},
            "contract_binding": {"p1": ["c1"]},
        },
        "domain_contracts": {"c1": {"description": "c1", "rules": ["r"]}},
    }
    packs, bindings, state, subtype, limits = pcm.normalize_review_packs(profile["review_packs"])
    assert state == pcm.SOURCE_STATE_INVALID
    assert subtype == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits
    ctx, ctx_limits = _ctx(profile, files=("backend/api/x.py",))
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in ctx_limits
    assert _no_not_relevant(ctx_limits)


@pytest.mark.parametrize(
    "key,value",
    [("version", ["1"]), ("version", {"a": 1}), ("version", True), ("updated", ["2026"]), ("updated", {"d": "x"}), ("version", None)],
)
def test_cm_c3_envelope_known_metadata_wrong_type(key: str, value: Any) -> None:
    """CM-C3-ENVELOPE-KNOWN-METADATA-WRONG-TYPE."""
    doc = {"packs": {"p1": {"paths": ["backend/**"]}}, key: value}
    _, _, state, subtype, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_INVALID, (key, value)
    assert subtype == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits


def test_pc_c3_envelope_real_target_shape() -> None:
    """PC-C3-ENVELOPE-REAL-TARGET-SHAPE: the real target envelope normalizes cleanly."""
    doc = _real_target_review_packs_mirror()
    packs, _, state, subtype, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and subtype is None and limits == []
    assert {p["id"] for p in packs} == {"calendar", "notifications", "docs_only"}


def test_c3_envelope_grammar_is_modern_only() -> None:
    """The frozen legacy envelope `{"packs": [...]}` keeps baseline leniency (unknown siblings ignored)."""
    doc = {"packs": [{"id": "legacy", "description": "calendar"}], "anything": 1}
    packs, _, state, _, limits = pcm.normalize_review_packs(doc)
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and limits == []
    assert [p["id"] for p in packs] == ["legacy"]


def test_c3_envelope_key_set_is_owner_evidenced() -> None:
    """Only keys with owner/source evidence: real target {version, updated, packs} + engine-owned contract_bindings."""
    assert set(pcm.REVIEW_PACK_ENVELOPE_KEYS) == {"packs", "contract_bindings", "version", "updated"}


def test_c3_modern_pack_value_grammar_preserved() -> None:
    """The already-built value grammar must not regress: nested id, collision, malformed field."""
    _, _, s1, _, l1 = pcm.normalize_review_packs({"packs": {"p": {"id": "p"}}})
    assert s1 == pcm.SOURCE_STATE_INVALID and "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in l1
    _, _, s2, _, l2 = pcm.normalize_review_packs({"packs": {"p": {"id": "q"}}})
    assert s2 == pcm.SOURCE_STATE_INVALID and "invalid_source_review_packs:INVALID_IDENTITY" in l2
    _, _, s3, _, l3 = pcm.normalize_review_packs({"packs": {"p": {"paths": "backend"}}})
    assert s3 == pcm.SOURCE_STATE_INVALID and "invalid_source_review_packs:MALFORMED_SHAPE" in l3
    _, _, s4, _, l4 = pcm.normalize_review_packs({"packs": {"p": {"bogus": 1}}})
    assert s4 == pcm.SOURCE_STATE_INVALID and "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in l4
    _, _, s5, _, l5 = pcm.normalize_review_packs({"packs": {"p": {}, " p ": {}}})
    assert s5 == pcm.SOURCE_STATE_INVALID and "invalid_source_review_packs:INVALID_IDENTITY" in l5


def test_ab_c1_pack_envelope_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C1-PACK-ENVELOPE-GRAMMAR (4179993594)."""
    doc = {"packs": {"p1": {"paths": ["backend/**"]}}, "contract_binding": {"p1": ["c1"]}}

    def witness() -> str:
        return pcm.normalize_review_packs(doc)[2]

    assert witness() == pcm.SOURCE_STATE_INVALID
    with monkeypatch.context() as m:
        m.setattr(pcm, "_review_pack_envelope_invalid", lambda *a, **k: False)
        assert witness() == pcm.SOURCE_STATE_PRESENT_VALID, "mutant must go RED"
    assert witness() == pcm.SOURCE_STATE_INVALID


# ---------------------------------------------------------------------------
# C1 -- Scope value domain, single authority (4179993602)
# ---------------------------------------------------------------------------

GLOBAL_SPELLINGS = ["global", "GLOBAL", " Global ", "gLoBaL\t"]
UNSUPPORTED_SCOPES = ["local", "repo", "backend", "all", "document"]


@pytest.mark.parametrize("scope", GLOBAL_SPELLINGS)
def test_cm_c3_scope_global_spellings_are_global_everywhere(scope: str) -> None:
    """CM-C3-SCOPE-GLOBAL (4179993602): pack, domain contract and _is_global_item agree (extensional identity)."""
    packs, _, state, _, _ = pcm.normalize_review_packs({"packs": {"p": {"description": "p", "scope": scope}}})
    assert state == pcm.SOURCE_STATE_PRESENT_VALID
    assert pcm._is_global_item(packs[0]) is True
    contracts, c_state, _, _ = pcm.normalize_domain_contracts({"c": {"description": "c", "scope": scope}})
    assert c_state == pcm.SOURCE_STATE_PRESENT_VALID
    assert pcm._is_global_item(contracts[0]) is True
    assert pcm._is_global_item({"scope": scope}) is True
    # and the pack therefore applies to an unrelated chunk (not silently "not relevant")
    ctx, limits = _ctx({"review_packs": {"packs": {"p": {"description": "p", "scope": scope}}}}, files=("zzz/other.py",))
    assert [p["id"] for p in ctx["review_packs"]] == ["p"]
    assert _no_not_relevant(limits)


@pytest.mark.parametrize("scope", UNSUPPORTED_SCOPES)
def test_cm_c3_scope_unsupported_value_is_invalid_not_discarded(scope: str) -> None:
    """CM-C3-SCOPE-UNSUPPORTED (4179993602): accept-then-discard is the defect."""
    _, _, state, subtype, limits = pcm.normalize_review_packs({"packs": {"p": {"description": "p", "scope": scope}}})
    assert state == pcm.SOURCE_STATE_INVALID
    assert subtype == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits
    _, c_state, c_sub, c_limits = pcm.normalize_domain_contracts({"c": {"description": "c", "scope": scope}})
    assert c_state == pcm.SOURCE_STATE_INVALID
    assert c_sub == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_contract:UNSUPPORTED_NONEMPTY" in c_limits


def test_pc_c3_scope_domain_is_single_authority() -> None:
    """Scope value domain is derived once: `global` is the only value any consumer gives semantics."""
    assert pcm.MODERN_SCOPE_DOMAIN == frozenset({"global"})
    for value in GLOBAL_SPELLINGS + UNSUPPORTED_SCOPES + ["", "  ", None]:
        is_global, supported = pcm.classify_scope(value)
        assert is_global == (isinstance(value, str) and value.strip().lower() == "global")
        assert supported == (value is None or (isinstance(value, str) and (not value.strip() or value.strip().lower() in pcm.MODERN_SCOPE_DOMAIN)))
    # extensional identity of every consumer with the authority
    for value in GLOBAL_SPELLINGS + UNSUPPORTED_SCOPES + ["", None]:
        expected = pcm.classify_scope(value)[0]
        assert pcm._is_global_item({"scope": value}) is expected
    # no consumer re-implements the comparison
    src = (PRODUCTION_ROOT / "payload_cost_model.py").read_text(encoding="utf-8")
    assert len(re.findall(r"""scope\.lower\(\)\s*==\s*["']global["']""", src)) == 0
    assert len(re.findall(r"""_clean_text\([^)]*scope[^)]*\)\s*==\s*["']global["']""", src)) == 0


def test_pc_c3_scope_absent_or_empty_is_not_global_and_admitted() -> None:
    _, _, state, _, _ = pcm.normalize_review_packs({"packs": {"p": {"description": "p", "scope": ""}}})
    assert state == pcm.SOURCE_STATE_PRESENT_VALID
    packs, _, _, _, _ = pcm.normalize_review_packs({"packs": {"p": {"description": "p", "paths": ["a/b.py"]}}})
    assert pcm._is_global_item(packs[0]) is False


def test_ab_c1_global_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C1-GLOBAL-SCOPE (4179993602): reintroduce the case-sensitive pre-repair comparison at the single authority."""
    profile = {"review_packs": {"packs": {"p": {"description": "p", "scope": "GLOBAL"}}}}

    def witness() -> list[str]:
        ctx, _ = _ctx(profile, files=("zzz/other.py",))
        return [p["id"] for p in ctx["review_packs"]]

    assert witness() == ["p"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "classify_scope", lambda v: (v == "global", True))
        assert witness() == [], "mutant must go RED"
    assert witness() == ["p"]


# ---------------------------------------------------------------------------
# C2 -- ExplicitContractIdentityReference => domain_contracts source required (4179993600)
# ---------------------------------------------------------------------------

_C1_CONTRACTS = {"c1": {"description": "c1", "rules": ["must hold"]}}


def test_cm_c3_inline_domain_contract_source_absent() -> None:
    """CM-C3-INLINE-DOMAIN-CONTRACT-SOURCE-ABSENT (4179993600): SourceAbsent != RelationUnresolved; keep both."""
    profile = {"review_packs": {"packs": {"p1": {"paths": ["backend/api/*"], "domain_contract": "c1"}}}}
    _, limits = _ctx(profile)
    assert "required_source_absent:domain_contracts" in limits
    assert "unresolved_contract_binding:p1:c1" in limits


def test_cm_c3_binding_source_absent() -> None:
    """CM-C3-BINDING-SOURCE-ABSENT."""
    profile = {
        "review_packs": {"packs": {"p1": {"paths": ["backend/api/*"]}}, "contract_bindings": {"p1": ["c1"]}},
    }
    _, limits = _ctx(profile)
    assert "required_source_absent:domain_contracts" in limits
    assert "unresolved_contract_binding:p1:c1" in limits


def test_cm_c3_explicit_contract_ref_source_absent() -> None:
    """CM-C3-EXPLICIT-CONTRACT-REF-SOURCE-ABSENT."""
    _, limits = _ctx({}, chunk_contracts=("contract:c1",))
    assert "required_source_absent:domain_contracts" in limits
    assert "unresolved_contract_reference:c1" in limits


def test_pc_c3_declared_ref_source_present() -> None:
    """PC-C3-DECLARED-REF-SOURCE-PRESENT: every declared origin, source present and resolving -> no limitation."""
    inline = {"review_packs": {"packs": {"p1": {"paths": ["backend/api/*"], "domain_contract": "c1"}}}, "domain_contracts": _C1_CONTRACTS}
    bound = {
        "review_packs": {"packs": {"p1": {"paths": ["backend/api/*"]}}, "contract_bindings": {"p1": ["c1"]}},
        "domain_contracts": _C1_CONTRACTS,
    }
    for profile in (inline, bound):
        ctx, limits = _ctx(profile)
        assert limits == [], limits
        assert [c["id"] for c in ctx["domain_contracts"]] == ["c1"]
    ctx, limits = _ctx({"domain_contracts": _C1_CONTRACTS}, chunk_contracts=("contract:c1",))
    assert limits == [] and [c["id"] for c in ctx["domain_contracts"]] == ["c1"]


def test_c3_required_source_not_demanded_without_declared_refs() -> None:
    """No declared contract reference -> an absent domain_contracts source stays a non-event."""
    _, limits = _ctx({"review_packs": {"packs": {"p1": {"paths": ["backend/api/*"]}}}})
    assert not _critical(limits, "required_source_absent:")
    _, limits = _ctx({})
    assert not _critical(limits, "required_source_absent:")


def test_c3_legacy_flat_pack_domain_contract_is_not_a_declared_ref() -> None:
    """Legacy flat pack.domain_contract stays NON-AUTHORITATIVE: it is not in DeclaredContractRefs."""
    profile = {"review_packs": {"packs": [{"id": "p1", "description": "d", "domain_contract": "c1"}]}}
    _, limits = _ctx(profile, chunk_contracts=("target_profile:review_packs",))
    assert not _critical(limits, "required_source_absent:")
    assert not _critical(limits, "unresolved_contract_binding:")


@pytest.mark.parametrize("origin", ["pack_inline", "binding", "explicit"])
def test_ab_c2_declared_ref_required_source(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    """AB-C2-DECLARED-REF-REQUIRED-SOURCE: remove each reference origin from DeclaredContractRefs
    -> the corresponding witness goes RED."""
    witnesses: dict[str, Callable[[], list[str]]] = {
        "pack_inline": lambda: _ctx({"review_packs": {"packs": {"p1": {"paths": ["backend/api/*"], "domain_contract": "c1"}}}})[1],
        "binding": lambda: _ctx({"review_packs": {"packs": {"p1": {"paths": ["backend/api/*"]}}, "contract_bindings": {"p1": ["c1"]}}})[1],
        "explicit": lambda: _ctx({}, chunk_contracts=("contract:c1",))[1],
    }
    witness = witnesses[origin]
    assert "required_source_absent:domain_contracts" in witness()
    original = pcm._declared_contract_ref_origins

    def dropped(*args: Any, **kwargs: Any) -> dict[str, set[str]]:
        origins = original(*args, **kwargs)
        origins[origin] = set()
        return origins

    with monkeypatch.context() as m:
        m.setattr(pcm, "_declared_contract_ref_origins", dropped)
        assert "required_source_absent:domain_contracts" not in witness(), "mutant must go RED"
    assert "required_source_absent:domain_contracts" in witness()


# ---------------------------------------------------------------------------
# C3 -- Generic identity authority (4179993596)
# ---------------------------------------------------------------------------

_CAL_PROFILE = {"review_packs": {"packs": {"agentescala-calendar": {"description": "Calendar", "paths": ["backend/cal/*"]}}}}


def test_cm_c3_modern_selection_has_no_target_alias() -> None:
    """CM-C3-MODERN-NO-ALIAS (4179993596): selected `calendar` does not resolve `agentescala-calendar`."""
    ctx, limits = _ctx(_CAL_PROFILE, selected="calendar")
    assert "selected_contract_pack_missing:calendar" in limits
    assert ctx["review_packs"] == []
    # symmetric direction of the removed alias
    profile = {"review_packs": {"packs": {"calendar": {"description": "Calendar"}}}}
    ctx, limits = _ctx(profile, selected="agentescala-calendar")
    assert "selected_contract_pack_missing:agentescala-calendar" in limits
    assert ctx["review_packs"] == []


def test_cm_c3_modern_selection_is_case_sensitive_exact() -> None:
    profile = {"review_packs": {"packs": {"calendar": {"description": "Calendar"}}}}
    _, limits = _ctx(profile, selected="Calendar")
    assert "selected_contract_pack_missing:Calendar" in limits
    _, limits = _ctx(profile, selected="cal")
    assert "selected_contract_pack_missing:cal" in limits


def test_pc_c3_modern_exact_selection() -> None:
    profile = {"review_packs": {"packs": {"calendar": {"description": "Calendar"}}}}
    ctx, limits = _ctx(profile, selected="calendar")
    assert [p["id"] for p in ctx["review_packs"]] == ["calendar"]
    assert not _critical(limits, "selected_contract_pack_missing:")
    assert ctx["review_packs"][0]["required"] is True


def test_pc_c3_legacy_selection_is_generic_not_target_aware() -> None:
    """The legacy alias-like behaviour emerges generically (selected is a substring of an id),
    without the engine knowing any target."""
    for pack_id in ("agentescala-calendar", "foo-calendar-bar", "calendar"):
        profile = {"review_packs": {"packs": [{"id": pack_id, "description": "x"}]}}
        ctx, limits = _ctx(profile, selected="calendar")
        assert [p["id"] for p in ctx["review_packs"]] == [pack_id]
        assert not _critical(limits, "selected_contract_pack_missing:")


def _production_string_constants(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


TARGET_SPECIFIC_LITERALS = re.compile(
    r"agentescala|interleitos|auth_admin|backend_schema_contract|response_model_rules", re.IGNORECASE
)
C2_RUNTIME_MODULES = (
    "payload_cost_model.py",
    "chunk_payload_builder.py",
    "semantic_chunker.py",
    "quality_gate.py",
    "final_synthesizer.py",
)


def test_cm_c3_genericity_census_zero_target_specific_branches() -> None:
    """Genericity census: no C2 runtime string constant names a target (comments/docstrings excluded;
    fixtures may use target-like names, production branching may not)."""
    offenders = []
    for path in sorted(PRODUCTION_ROOT.glob("*.py")):
        for lineno, value in _production_string_constants(path):
            if TARGET_SPECIFIC_LITERALS.search(value):
                offenders.append(f"{path.name}:{lineno}:{value[:60]!r}")
    assert offenders == []
    for name in C2_RUNTIME_MODULES:
        for lineno, value in _production_string_constants(PRODUCTION_ROOT / name):
            assert value not in {"calendar", "security"}, f"{name}:{lineno}: target-specific literal {value!r}"


def test_ab_c3_modern_exact_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C3-MODERN-EXACT-SELECTION: swap the exact modern matcher for the legacy fuzzy one."""
    profile = {"review_packs": {"packs": {"calendar": {"description": "Calendar"}}}}

    def witness() -> list[str]:
        return _critical(_ctx(profile, selected="cal")[1], "selected_contract_pack_missing:")

    assert witness() == ["selected_contract_pack_missing:cal"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "modern_pack_matches_selected", pcm.legacy_pack_matches_selected)
        assert witness() == [], "mutant must go RED"
    assert witness() == ["selected_contract_pack_missing:cal"]


def test_ab_c3_target_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C3-TARGET-ALIAS: reintroduce the target-specific alias in the modern matcher."""
    original = pcm.modern_pack_matches_selected

    def aliased(pack: dict[str, Any], selected: str) -> bool:
        return original(pack, selected) or (selected == "calendar" and pack.get("id") == "agentescala-calendar")

    def witness() -> list[str]:
        return _critical(_ctx(_CAL_PROFILE, selected="calendar")[1], "selected_contract_pack_missing:")

    assert witness() == ["selected_contract_pack_missing:calendar"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "modern_pack_matches_selected", aliased)
        assert witness() == [], "mutant must go RED"
    assert witness() == ["selected_contract_pack_missing:calendar"]


# ---------------------------------------------------------------------------
# C4 -- Legacy firewall (4179993605, 4179993607 and hidden siblings)
# ---------------------------------------------------------------------------

_LEGACY_RULES = {
    "rules": [
        {"id": "fe-rule", "description": "frontend only", "scope": "frontend", "paths": ["frontend/app.js"]},
        {"id": "be-rule", "description": "backend rule", "paths": ["backend/api/shifts.py"]},
    ]
}


def test_cm_c4_legacy_include_all_contract_is_unconditional() -> None:
    """CM-C4-LEGACY-INCLUDE-ALL-CONTRACT (4179993605): target_profile:domain_contracts includes every
    legacy rule, scoped or not, whatever the chunk's files."""
    ctx, limits = _ctx({"domain_contracts": _LEGACY_RULES}, chunk_contracts=("target_profile:domain_contracts",))
    assert [c["id"] for c in ctx["domain_contracts"]] == ["be-rule", "fe-rule"]
    assert _no_not_relevant(limits)


def test_cm_c4_legacy_include_all_pack() -> None:
    """CM-C4-LEGACY-PACK-INCLUDE-ALL: every legacy pack, including packs carrying modern-only fields."""
    profile = {
        "review_packs": {
            "packs": [
                {"id": "a", "description": "alpha", "paths": ["frontend/x.js"], "scope": "frontend"},
                {"id": "b", "description": "beta"},
            ]
        }
    }
    ctx, _ = _ctx(profile, chunk_contracts=("target_profile:review_packs",))
    assert [p["id"] for p in ctx["review_packs"]] == ["a", "b"]


def test_cm_c4_legacy_pack_explicit_id_ref() -> None:
    """CM-C4-LEGACY-PACK-EXPLICIT-ID-REF: `contract:<pack id>` includes the legacy pack (baseline predicate)."""
    profile = {"review_packs": {"packs": [{"id": "alpha", "description": "x"}, {"id": "beta", "description": "y"}]}}
    ctx, limits = _ctx(profile, chunk_contracts=("contract:alpha",))
    assert [p["id"] for p in ctx["review_packs"]] == ["alpha"]
    assert not _critical(limits, "unresolved_contract_reference:"), "a ref that hits a legacy pack is resolved"
    assert not _critical(limits, "required_source_absent:"), "...and is not a contract identity reference"


def test_cm_c4_legacy_contract_relevance() -> None:
    """CM-C4-LEGACY-CONTRACT-RELEVANCE: semantic-group keywords over id + description."""
    profile = {"domain_contracts": {"rules": [{"id": "r1", "description": "service layer rule"}, {"id": "r2", "description": "zzz"}]}}
    ctx, _ = _ctx(profile, files=("elsewhere/a.txt",), group="primary_backend_logic")
    assert [c["id"] for c in ctx["domain_contracts"]] == ["r1"]


def test_cm_c4_legacy_pack_relevance() -> None:
    """CM-C4-LEGACY-PACK-RELEVANCE."""
    profile = {"review_packs": {"packs": [{"id": "p1", "description": "frontend component pack"}, {"id": "p2", "description": "zzz"}]}}
    ctx, _ = _ctx(profile, files=("elsewhere/a.txt",), group="frontend_ui")
    assert [p["id"] for p in ctx["review_packs"]] == ["p1"]


def test_cm_c4_modern_has_no_fuzzy_relevance() -> None:
    """CM-C4-MODERN-NO-FUZZY-RELEVANCE: keyword recovery never applies to modern sources."""
    profile = {
        "domain_contracts": {"service_rules": {"description": "service layer", "rules": ["x"]}},
        "review_packs": {"packs": {"frontend_pack": {"description": "frontend component pack"}}},
    }
    ctx, limits = _ctx(profile, files=("elsewhere/a.txt",), group="primary_backend_logic")
    assert ctx["domain_contracts"] == [] and ctx["review_packs"] == []
    ctx, _ = _ctx(profile, files=("elsewhere/a.txt",), group="frontend_ui")
    assert ctx["review_packs"] == []


def test_cm_c4_description_only_pack_preserved_and_selected() -> None:
    """CM-C4-DESCRIPTION-ONLY-PACK-PRESERVED / -SELECTED (4179993607)."""
    profile = {"review_packs": {"packs": [{"description": "calendar"}]}}
    packs, _, state, _, limits = pcm.normalize_review_packs(profile["review_packs"])
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and limits == []
    assert len(packs) == 1 and packs[0].get("description") == "calendar"
    assert not packs[0].get("id"), "identity must not be fabricated from the description"
    ctx, limits = _ctx(profile, selected="calendar")
    assert len(ctx["review_packs"]) == 1 and ctx["review_packs"][0].get("description") == "calendar"
    assert not ctx["review_packs"][0].get("id")
    assert not _critical(limits, "selected_contract_pack_missing:")


def test_cm_c4_legacy_pack_projection_is_the_baseline_projection() -> None:
    """Modern-only fields (paths, patterns, scope, is_global, domain_contract) must not acquire
    semantics in a LEGACY pack: LegacyPackProjection == {id, description, recommended_review_preset}."""
    profile = {
        "review_packs": {
            "packs": [
                {
                    "id": "p1",
                    "description": "d",
                    "recommended_review_preset": "review:deep",
                    "paths": ["backend/api/shifts.py"],
                    "patterns": ["backend/*"],
                    "scope": "global",
                    "is_global": True,
                    "domain_contract": "c1",
                }
            ]
        }
    }
    packs, _, state, _, _ = pcm.normalize_review_packs(profile["review_packs"])
    assert state == pcm.SOURCE_STATE_PRESENT_VALID
    assert set(packs[0]) <= {"id", "description", "recommended_review_preset"}
    # path hit / pattern hit / global must NOT make the legacy pack applicable
    for files in (("backend/api/shifts.py",), ("zzz/q.py",)):
        ctx, limits = _ctx(profile, files=files)
        assert ctx["review_packs"] == [], files
        assert any(lim.startswith("contracts_context_not_relevant:") for lim in limits)


def test_cm_c4_legacy_not_relevant_consequence_preserved() -> None:
    """Zero baseline-applicable contracts + zero baseline-applicable packs -> contracts_context_not_relevant."""
    profile = {
        "domain_contracts": {"rules": [{"id": "r", "description": "zzz", "paths": ["frontend/a.js"]}]},
        "review_packs": {"packs": [{"id": "p", "description": "zzz"}]},
    }
    ctx, limits = _ctx(profile)
    assert ctx == {"domain_contracts": [], "review_packs": []}
    assert limits == ["contracts_context_not_relevant:chunk-1"]


def test_cm_c4_legacy_contract_paths_are_exact_not_substring() -> None:
    """Baseline `paths` predicate: canonical exact intersection only (no substring recovery on paths)."""
    profile = {"domain_contracts": {"rules": [{"id": "r", "description": "zzz", "paths": ["backend/api"]}]}}
    ctx, _ = _ctx(profile, files=("backend/api/shifts.py",))
    assert ctx["domain_contracts"] == []


# -- idless legacy pack x required floor ----------------------------------


def _shrink_until_stable(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    payload: dict[str, Any] = {"chunk_context": {"contracts_context": ctx}, "limitations": []}
    snapshots: list[dict[str, Any]] = []
    for _ in range(20):
        if not chunk_payload_builder._shrink_contracts_context(payload):
            break
        snapshots.append(copy.deepcopy(payload["chunk_context"]["contracts_context"]))
    return snapshots, payload["limitations"]


def test_cm_c4_idless_legacy_pack_floor_never_fabricates_identity() -> None:
    """Item 28: description-only legacy pack x required floor. No `id: ""`, no description->id."""
    profile = {"review_packs": {"packs": [{"description": "calendar", "recommended_review_preset": "review:deep"}]}}
    ctx, _ = _ctx(profile, selected="calendar")
    assert ctx["review_packs"][0]["required"] is True
    assert not ctx["review_packs"][0].get("id")
    assert not ctx["review_packs"][0].get("effective_contracts")

    minimal = pcm.minimal_contracts_context(ctx)
    assert minimal["review_packs"] == [{"description": "calendar", "required": True}]
    assert all("id" not in p for p in minimal["review_packs"]), "no fabricated identity"

    snapshots, limitations = _shrink_until_stable(pcm.clean_contracts_context_for_payload(ctx))
    floor = next(s for s in snapshots if s["review_packs"] and set(s["review_packs"][0]) == {"description", "required"})
    assert floor["review_packs"] == [{"description": "calendar", "required": True}]
    assert all("id" not in p for s in snapshots for p in s["review_packs"])
    assert "required_contract_pack_context_lost:unidentified_legacy_pack" in limitations


def test_cm_c4_description_only_pack_flows_through_real_plan_and_payload() -> None:
    """Production carriers (planner -> builder), not just contracts_context: a description-only legacy
    pack is transported as required context without a fabricated identity and without a limitation."""
    from app.agent_review.chunk_payload_builder import build_chunk_payloads
    from app.agent_review.semantic_chunker import build_semantic_chunk_plan
    from tests.agent_review.test_c2_executable_contract_gate_a import _base_intake, _brief

    intake = _base_intake()
    intake.target_profile = {"review_packs": {"packs": [{"description": "calendar"}]}}
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status == "complete"
    brief = _brief(intake, plan)
    brief.review["contract_pack"] = "calendar"
    _, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    assert payloads
    for payload in payloads.values():
        assert payload.chunk_context["contracts_context"]["review_packs"] == [{"description": "calendar", "required": True}]
        assert not [lim for lim in payload.limitations if "contract" in lim]


def test_c4_idless_floor_does_not_disturb_identified_pack_floor() -> None:
    profile = {"review_packs": {"packs": {"calendar": {"description": "Calendar", "domain_contract": "c1"}}}, "domain_contracts": _C1_CONTRACTS}
    ctx, _ = _ctx(profile, selected="calendar")
    assert pcm.minimal_contracts_context(ctx)["review_packs"] == [
        {"id": "calendar", "required": True, "effective_contracts": ["c1"]}
    ]


# -- C4 ablations -----------------------------------------------------------


def test_ab_c4_legacy_include_all_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-INCLUDE-ALL-CONTRACT: reintroduce `AND not has_explicit_scope`."""
    def witness() -> list[str]:
        ctx, _ = _ctx({"domain_contracts": _LEGACY_RULES}, chunk_contracts=("target_profile:domain_contracts",))
        return [c["id"] for c in ctx["domain_contracts"]]

    assert witness() == ["be-rule", "fe-rule"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_contract_include_all", lambda contract, include_all: include_all and not pcm._paths_from_item(contract) and not contract.get("scope"))
        assert witness() == ["be-rule"], "mutant must go RED (scoped legacy rule dropped from include-all)"
    assert witness() == ["be-rule", "fe-rule"]


def test_ab_c4_legacy_include_all_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-INCLUDE-ALL-PACK."""
    profile = {"review_packs": {"packs": [{"id": "a", "description": "alpha"}, {"id": "b", "description": "beta"}]}}

    def witness() -> list[str]:
        ctx, _ = _ctx(profile, chunk_contracts=("target_profile:review_packs",))
        return [p["id"] for p in ctx["review_packs"]]

    assert witness() == ["a", "b"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_pack_include_all", lambda pack, include_all: False)
        assert witness() == [], "mutant must go RED"
    assert witness() == ["a", "b"]


def test_ab_c4_legacy_explicit_pack_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-EXPLICIT-PACK-REF."""
    profile = {"review_packs": {"packs": [{"id": "alpha", "description": "x"}, {"id": "beta", "description": "y"}]}}

    def witness() -> list[str]:
        ctx, _ = _ctx(profile, chunk_contracts=("contract:alpha",))
        return [p["id"] for p in ctx["review_packs"]]

    assert witness() == ["alpha"]
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_pack_explicit_ref", lambda pack, referenced: False)
        assert witness() == [], "mutant must go RED"
    assert witness() == ["alpha"]


def test_ab_c4_legacy_relevance(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-RELEVANCE: contracts and packs."""
    profile = {
        "domain_contracts": {"rules": [{"id": "r1", "description": "service layer rule"}]},
        "review_packs": {"packs": [{"id": "p1", "description": "service pack"}]},
    }

    def witness() -> tuple[list[str], list[str]]:
        ctx, _ = _ctx(profile, files=("elsewhere/a.txt",), group="primary_backend_logic")
        return [c["id"] for c in ctx["domain_contracts"]], [p["id"] for p in ctx["review_packs"]]

    assert witness() == (["r1"], ["p1"])
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_relevance_match", lambda item, keywords: False)
        assert witness() == ([], []), "mutant must go RED"
    assert witness() == (["r1"], ["p1"])


def test_ab_c4_legacy_description_only_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-DESCRIPTION-ONLY-PACK: reintroduce `if pid:` row admission."""
    profile = {"review_packs": {"packs": [{"description": "calendar"}]}}

    def witness() -> int:
        return len(_ctx(profile, selected="calendar")[0]["review_packs"])

    assert witness() == 1
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_pack_row_admitted", lambda row: bool(row.get("id")))
        assert witness() == 0, "mutant must go RED"
    assert witness() == 1


# ---------------------------------------------------------------------------
# ModeSemanticMatrix -- every LEGACY/MODERN difference is INTENTIONAL (with authority) or a BUG;
# never accidental. Each row is executable: the witness proves the two cells.
# ---------------------------------------------------------------------------

_AUTH_FROZEN = "frozen legacy baseline 6bbd2f9 (app/agent_review/AGENTS.md:96-100 Compatibility)"
_AUTH_FAIL_CLOSED = "AGENTS.md:37-39 fail-closed for identity/schema/coverage; OBL-CL2-02/-03"


def _w_source_shapes() -> bool:
    return (
        pcm.detect_domain_contracts_format({"rules": []}) == pcm.FORMAT_LEGACY_FLAT
        and pcm.detect_domain_contracts_format({"c": {}}) == pcm.FORMAT_MODERN_MAPPING
        and pcm.detect_review_packs_format({"packs": []}) == pcm.FORMAT_LEGACY_FLAT
        and pcm.detect_review_packs_format({"packs": {}}) == pcm.FORMAT_MODERN_MAPPING
    )


def _w_identity() -> bool:
    legacy, _, ls, _, _ = pcm.normalize_review_packs({"packs": [{"description": "calendar"}]})
    _, _, ms, _, ml = pcm.normalize_review_packs({"packs": {"p": {"id": "p"}}})
    return ls == pcm.SOURCE_STATE_PRESENT_VALID and not legacy[0].get("id") and ms == pcm.SOURCE_STATE_INVALID


def _w_normalization() -> bool:
    legacy, _, _, _ = pcm.normalize_domain_contracts({"rules": [{"id": "r", "paths": ["./a//b.py"]}]})
    modern, _, _, _ = pcm.normalize_domain_contracts({"r": {"description": "r", "paths": ["./a//b.py"]}})
    return legacy[0]["paths"] == ["./a//b.py"] and modern[0]["paths"] == ["a/b.py"]


def _w_exact_paths() -> bool:
    _, ls, _, _ = pcm.normalize_domain_contracts({"rules": [{"id": "r", "paths": ["/etc/passwd"]}]})
    _, ms, _, _ = pcm.normalize_domain_contracts({"r": {"description": "r", "paths": ["/etc/passwd"]}})
    legacy_glob = pcm._contract_matches_chunk({"paths": ["backend/*"]}, chunk_files={"backend/a.py"}, format=pcm.FORMAT_LEGACY_FLAT)
    modern_glob = pcm._contract_matches_chunk({"paths": ["backend/*"]}, chunk_files={"backend/a.py"}, format=pcm.FORMAT_MODERN_MAPPING)
    return ls == pcm.SOURCE_STATE_PRESENT_VALID and ms == pcm.SOURCE_STATE_INVALID and legacy_glob is False and modern_glob is True


def _w_patterns() -> bool:
    files = {"backend/api/shifts.py"}
    return (
        pcm._contract_matches_chunk({"patterns": ["backend/api"]}, chunk_files=files, format=pcm.FORMAT_LEGACY_FLAT) is True
        and pcm._contract_matches_chunk({"patterns": ["backend/api"]}, chunk_files=files, format=pcm.FORMAT_MODERN_MAPPING) is False
        and pcm._contract_matches_chunk({"patterns": ["Backend/*"]}, chunk_files=files, format=pcm.FORMAT_MODERN_MAPPING) is False
    )


def _w_global() -> bool:
    legacy, _, _, _ = pcm.normalize_domain_contracts({"rules": [{"id": "r", "scope": "frontend"}]})
    _, ms, _, _ = pcm.normalize_domain_contracts({"r": {"description": "r", "scope": "frontend"}})
    return legacy[0]["scope"] == "frontend" and ms == pcm.SOURCE_STATE_INVALID and pcm.classify_scope("GLOBAL")[0]


def _w_include_all() -> bool:
    legacy, _ = _ctx({"domain_contracts": {"rules": [{"id": "r", "description": "zzz"}]}}, chunk_contracts=("target_profile:domain_contracts",))
    modern, _ = _ctx({"domain_contracts": {"r": {"description": "zzz", "rules": ["x"]}}}, chunk_contracts=("target_profile:domain_contracts",))
    return [c["id"] for c in legacy["domain_contracts"]] == ["r"] and modern["domain_contracts"] == []


def _w_explicit_refs() -> bool:
    legacy, ll = _ctx({"review_packs": {"packs": [{"id": "alpha", "description": "x"}]}}, chunk_contracts=("contract:alpha",))
    modern, ml = _ctx({"review_packs": {"packs": {"alpha": {"description": "x"}}}}, chunk_contracts=("contract:alpha",))
    return (
        [p["id"] for p in legacy["review_packs"]] == ["alpha"]
        and not _critical(ll, "unresolved_contract_reference:")
        and modern["review_packs"] == []
        and "unresolved_contract_reference:alpha" in ml
    )


def _w_selection() -> bool:
    legacy, _ = _ctx({"review_packs": {"packs": [{"id": "Alpha-Pack", "description": "d"}]}}, selected="alpha")
    _, modern_limits = _ctx({"review_packs": {"packs": {"Alpha-Pack": {"description": "d"}}}}, selected="alpha")
    return len(legacy["review_packs"]) == 1 and "selected_contract_pack_missing:alpha" in modern_limits


def _w_relevance() -> bool:
    legacy, _ = _ctx({"domain_contracts": {"rules": [{"id": "r", "description": "service"}]}}, group="primary_backend_logic")
    modern, _ = _ctx({"domain_contracts": {"r": {"description": "service", "rules": ["x"]}}}, group="primary_backend_logic")
    return [c["id"] for c in legacy["domain_contracts"]] == ["r"] and modern["domain_contracts"] == []


def _w_relation_resolution() -> bool:
    _, legacy_limits = _ctx({"review_packs": {"packs": [{"id": "p", "description": "d", "domain_contract": "c1"}]}}, chunk_contracts=("target_profile:review_packs",))
    _, modern_limits = _ctx({"review_packs": {"packs": {"p": {"description": "d", "domain_contract": "c1"}}}})
    return not _critical(legacy_limits, "unresolved_contract_binding:") and "unresolved_contract_binding:p:c1" in modern_limits


def _w_required_source() -> bool:
    _, legacy_limits = _ctx({"review_packs": {"packs": [{"id": "p", "description": "d", "domain_contract": "c1"}]}}, chunk_contracts=("target_profile:review_packs",))
    _, modern_limits = _ctx({"review_packs": {"packs": {"p": {"description": "d", "domain_contract": "c1"}}}})
    _, explicit_limits = _ctx({}, chunk_contracts=("contract:c1",))
    return (
        "required_source_absent:domain_contracts" not in legacy_limits
        and "required_source_absent:domain_contracts" in modern_limits
        and "required_source_absent:domain_contracts" in explicit_limits
    )


def _w_required_floor() -> bool:
    legacy_ctx, _ = _ctx({"review_packs": {"packs": [{"description": "calendar"}]}}, selected="calendar")
    modern_ctx, _ = _ctx({"review_packs": {"packs": {"calendar": {"description": "d"}}}}, selected="calendar")
    return (
        pcm.minimal_contracts_context(legacy_ctx)["review_packs"] == [{"description": "calendar", "required": True}]
        and pcm.minimal_contracts_context(modern_ctx)["review_packs"] == [{"id": "calendar", "required": True}]
    )


MODE_SEMANTIC_MATRIX: list[dict[str, Any]] = [
    {"axis": "source_shapes", "legacy": '{"rules": [...]} / {"packs": [...]} (+ top-level lists: ADDITIVE_COMPATIBILITY)', "modern": "mapping keyed by identity", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN, "witness": _w_source_shapes},
    {"axis": "identity", "legacy": "id optional; a description-only row is valid and its identity is never fabricated", "modern": "mapping key is the sole identity; nested id unsupported", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; OBL-CL2-02", "witness": _w_identity},
    {"axis": "normalization", "legacy": "baseline display projection (sanitize_display_path)", "modern": "canonical repository-relative identities (canonical_repo_path / canonical_repo_pattern)", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; " + _AUTH_FAIL_CLOSED, "witness": _w_normalization},
    {"axis": "exact_paths", "legacy": "canonical exact intersection; unresolvable path silently non-matching", "modern": "any non-repository-relative member invalidates the source; glob-bearing paths match", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; " + _AUTH_FAIL_CLOSED, "witness": _w_exact_paths},
    {"axis": "patterns", "legacy": "substring / trailing-star prefix", "modern": "fnmatchcase inside the repository boundary", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN, "witness": _w_patterns},
    {"axis": "global", "legacy": "scope.lower()==global or is_global; other scope strings retained", "modern": "scope domain {global}; any other nonempty value is INVALID", "classification": "INTENTIONAL", "authority": _AUTH_FAIL_CLOSED, "witness": _w_global},
    {"axis": "include_all", "legacy": "target_profile:* is an unconditional include-all", "modern": "availability is not applicability", "classification": "INTENTIONAL", "authority": "CM-A3-AVAILABILITY-IS-NOT-APPLICABILITY; " + _AUTH_FROZEN, "witness": _w_include_all},
    {"axis": "explicit_refs", "legacy": "contract:<id> selects contracts AND packs by id (unresolved ref fails closed)", "modern": "contract:<id> resolves contracts only", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; OBL-CL2-02", "witness": _w_explicit_refs},
    {"axis": "selection", "legacy": "case-insensitive exact/substring over id and description", "modern": "exact case-sensitive mapping key; no alias", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; app/agent_review/AGENTS.md:119-125 Genericity", "witness": _w_selection},
    {"axis": "relevance_keywords", "legacy": "semantic-group keyword recovery over id+description", "modern": "none", "classification": "INTENTIONAL", "authority": _AUTH_FROZEN + "; CM-A3-AVAILABILITY-IS-NOT-APPLICABILITY", "witness": _w_relevance},
    {"axis": "relation_resolution", "legacy": "pack.domain_contract NON-AUTHORITATIVE (not even projected)", "modern": "pack.domain_contract + contract_bindings establish EffectiveContractRefs", "classification": "INTENTIONAL", "authority": "REVIEW_PACK_LEGACY_INPUT_POLICY; OBL-CL2-02", "witness": _w_relation_resolution},
    {"axis": "required_source", "legacy": "only an explicit contract:<id> is a declared contract reference", "modern": "inline domain_contract U contract_bindings U explicit refs", "classification": "INTENTIONAL", "authority": "OBL-CL2-03; cycle-3 4179993600", "witness": _w_required_source},
    {"axis": "required_floor", "legacy": "identity-less pack floors to its description carrier (no fabricated id)", "modern": "pack floors to its identity (+ effective_contracts)", "classification": "INTENTIONAL", "authority": "OBL-CL2-05; item 28 (no fabricated identity)", "witness": _w_required_floor},
]


@pytest.mark.parametrize("row", MODE_SEMANTIC_MATRIX, ids=[r["axis"] for r in MODE_SEMANTIC_MATRIX])
def test_mode_semantic_matrix_row_is_executable_and_intentional(row: dict[str, Any]) -> None:
    assert row["classification"] in {"INTENTIONAL"}, "an unexplained difference is a BUG, never accidental"
    assert row["authority"]
    assert row["witness"]() is True, row["axis"]


def test_mode_semantic_matrix_covers_every_declared_axis() -> None:
    axes = {r["axis"] for r in MODE_SEMANTIC_MATRIX}
    assert axes == {
        "source_shapes", "identity", "normalization", "exact_paths", "patterns", "global", "include_all",
        "explicit_refs", "selection", "relevance_keywords", "relation_resolution", "required_source", "required_floor",
    }


# ---------------------------------------------------------------------------
# Finding regression census: every known material finding keeps at least one named witness.
# ---------------------------------------------------------------------------

KNOWN_FINDINGS: dict[str, list[str]] = {
    "cycle_1": ["4178603183", "4178603188", "4178603193", "4178603197", "4178603200", "4178603205", "4178603209", "4178603213", "4178603218"],
    "cycle_2": ["4179272317", "4179272322", "4179272324", "4179272326", "4179272328", "4179272330", "4179272331"],
    "cycle_3": ["4179993590", "4179993594", "4179993596", "4179993600", "4179993602", "4179993605", "4179993607"],
}


def _witness_tests_by_finding() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {fid: [] for ids in KNOWN_FINDINGS.values() for fid in ids}
    for module in ("test_c2_executable_contract_gate_a.py", "test_c2_gate_a_cycle3_domain_closure.py"):
        source = (Path(__file__).parent / module).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                body = ast.get_source_segment(source, node) or ""
                for fid in found:
                    if fid in body:
                        found[fid].append(f"{module}::{node.name}")
    return found


def test_finding_regression_census_is_total() -> None:
    """cycle_1: 9, cycle_2: 7, cycle_3: 7 -> 23 known material findings, each with a named witness."""
    assert {cycle: len(ids) for cycle, ids in KNOWN_FINDINGS.items()} == {"cycle_1": 9, "cycle_2": 7, "cycle_3": 7}
    witnesses = _witness_tests_by_finding()
    assert sum(len(ids) for ids in KNOWN_FINDINGS.values()) == 23
    assert [fid for fid, tests in witnesses.items() if not tests] == []
