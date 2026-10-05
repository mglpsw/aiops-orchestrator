#!/usr/bin/env python3
"""Generate the durable V1-C2 legacy differential oracle from the FROZEN baseline.

Authority: commit 6bbd2f949989da3e90e1d9c527e37059c0b628ff (never master, never a
predecessor PR head, never current tests). The baseline's `contracts_context` is
loaded from `git show <commit>:<path>` as an isolated module and executed over the
corpus below; its OBSERVATIONS (inputs + expected outputs) are written to

    tests/agent_review/fixtures/v1_c2_legacy_differential_observations.json

CI is shallow (fetch-depth 2) and never runs this script: it consumes the versioned
fixture and verifies its provenance (commit + blob oid). The algorithm is NOT copied
into production or into the test: the fixture holds INPUT + EXPECTED OBSERVATION only.

Predicate accounting (MC/DC-style): every baseline predicate is mutated in an
isolated copy of the baseline source; the cases whose baseline observation changes
are the predicate's killers. Predicates with no killer must be declared
`redundant` (subsumed) or `dead` (structurally unreachable) with a proof note, and
the generator refuses to emit anything else (STOP_LEGACY_DIFFERENTIAL_BRANCH_GAP).

Usage (needs git history containing the baseline commit):
    PYTHONPATH=. python3 scripts/generate-v1-c2-legacy-differential-oracle.py [--check]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "agent_review" / "fixtures" / "v1_c2_legacy_differential_observations.json"

SCHEMA = "agent-review.v1-c2-legacy-differential-observations.v1"
BASELINE_COMMIT = "6bbd2f949989da3e90e1d9c527e37059c0b628ff"
BASELINE_PATH = "app/agent_review/payload_cost_model.py"
BASELINE_BLOB_OID = "41423394e69b4fa67785ac01088757bdff917001"

FILES_HIT = ["backend/api/shifts.py"]
FILES_ELSE = ["elsewhere/a.txt"]

SEMANTIC_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "primary_backend_logic": ("backend", "service", "domain", "api"),
    "api_schema_contract": ("schema", "contract", "api", "model"),
    "frontend_ui": ("frontend", "ui", "component"),
    "tests": ("test", "coverage", "assert"),
    "workflow_aiops": ("workflow", "aiops", "pipeline"),
    "docs_changelog": ("docs", "changelog", "readme"),
    "suspicious_out_of_scope": ("secret", "prod", "deploy", "runtime"),
}


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def _case(
    case_id: str,
    covers: list[str],
    *,
    rules: list[dict[str, Any]] | None = None,
    packs: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
    cc: list[str] | None = None,
    sel: str | None = None,
    group: str = "other",
    cm: str | None = None,
) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    if rules is not None:
        profile["domain_contracts"] = {"rules": rules}
    if packs is not None:
        profile["review_packs"] = {"packs": packs}
    return {
        "id": case_id,
        "countermodel": cm,
        "covers": covers,
        "input": {
            "target_profile": profile,
            "chunk_files": files if files is not None else list(FILES_ELSE),
            "chunk_contracts": cc or [],
            "selected_contract_pack": sel,
            "semantic_group": group,
            "chunk_id": "chunk-1",
        },
    }


def build_corpus() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    add = cases.append

    fe = {"id": "fe-rule", "description": "frontend only", "scope": "frontend", "paths": ["frontend/app.js"]}
    be = {"id": "be-rule", "description": "backend rule", "paths": ["backend/api/shifts.py"]}
    zz = {"id": "zz-rule", "description": "zzz"}

    # --- contracts --------------------------------------------------------
    add(_case("contract.include_all.true", ["C.include_all"], rules=[fe, be, zz], cc=["target_profile:domain_contracts"], cm="CM-C4-LEGACY-INCLUDE-ALL-CONTRACT"))
    add(_case("contract.include_all.false", ["C.include_all"], rules=[fe, zz]))
    add(_case("contract.explicit_ref.hit", ["C.explicit_ref"], rules=[fe, zz], cc=["contract:zz-rule"], cm="CM-C4-LEGACY-CONTRACT-EXPLICIT-REF"))
    add(_case("contract.explicit_ref.miss", ["C.explicit_ref"], rules=[fe, zz], cc=["contract:nope"]))
    add(_case("contract.paths.exact.hit", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["backend/api/shifts.py"]}], files=FILES_HIT))
    add(_case("contract.paths.canonical.hit", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["./backend//api/shifts.py"]}], files=FILES_HIT))
    add(_case("contract.paths.substring.miss", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["backend/api"]}], files=FILES_HIT))
    add(_case("contract.paths.glob.miss", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["backend/*"]}], files=FILES_HIT))
    add(_case("contract.paths.unresolvable.miss", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["/etc/passwd"]}], files=FILES_HIT))
    add(_case("contract.patterns.substring.hit", ["C.match.patterns", "PAT.substring"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/api"]}], files=FILES_HIT))
    add(_case("contract.patterns.star_prefix.hit", ["C.match.patterns", "PAT.suffix_star"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/*"]}], files=FILES_HIT))
    add(_case("contract.patterns.glob.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["*.py"]}], files=FILES_HIT))
    add(_case("contract.patterns.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["zzz"]}], files=FILES_HIT))
    add(_case("contract.global.scope.lower", ["C.match.global.scope"], rules=[{"id": "g", "description": "zzz", "scope": "global"}]))
    add(_case("contract.global.scope.upper_ws", ["C.match.global.scope"], rules=[{"id": "g", "description": "zzz", "scope": " GLOBAL "}]))
    add(_case("contract.global.flag", ["C.match.global.flag"], rules=[{"id": "g", "description": "zzz", "is_global": True}]))
    add(_case("contract.global.nonglobal_scope.miss", ["C.match.global.scope", "C.match.global.flag"], rules=[{"id": "g", "description": "zzz", "scope": "frontend", "is_global": False}]))
    for group, keywords in SEMANTIC_GROUP_KEYWORDS.items():
        for kw in keywords:
            add(_case(f"contract.relevance.{group}.{kw}", [f"KW.{group}.{kw}", "C.relevance"], rules=[{"id": "q", "description": f"zz {kw} zz"}], group=group, cm="CM-C4-LEGACY-CONTRACT-RELEVANCE"))
        add(_case(f"contract.relevance.{group}.miss", ["C.relevance"], rules=[{"id": "q", "description": "zz qq zz"}], group=group))
    add(_case("contract.relevance.id_only", ["C.relevance", "C.relevance.id_part"], rules=[{"id": "service-x"}], group="primary_backend_logic"))
    add(_case("contract.relevance.description_only", ["C.relevance", "C.relevance.desc_part"], rules=[{"description": "service layer"}], group="primary_backend_logic", cm="CM-C4-DESCRIPTION-ONLY-CONTRACT-PRESERVED"))
    add(_case("contract.relevance.id_and_description", ["C.relevance"], rules=[{"id": "q", "description": "zz service"}], group="primary_backend_logic"))
    add(_case("contract.relevance.group_without_keywords.miss", ["C.relevance"], rules=[{"id": "q", "description": "service"}], group="other"))
    add(_case("contract.carriers.include_all", ["C.include_all"], rules=[{"id": "only-id"}, {"description": "only description"}, {"id": "both", "description": "both described"}], cc=["target_profile:domain_contracts"]))

    # --- contract near-misses / carriers / interactions (adversarial review round) ----------
    for carrier in ("file_path", "path"):
        add(_case(f"contract.carrier.{carrier}.hit", [f"C.carrier.{carrier}", "C.match.paths"], rules=[{"id": "r", "description": "zzz", carrier: "backend/api/shifts.py"}], files=FILES_HIT))
    for carrier in ("files", "source_files", "related_files"):
        add(_case(f"contract.carrier.{carrier}.hit", [f"C.carrier.{carrier}", "C.match.paths"], rules=[{"id": "r", "description": "zzz", carrier: ["backend/api/shifts.py"]}], files=FILES_HIT))
    add(_case("contract.paths.case_differs.miss", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["Backend/API/shifts.py"]}], files=FILES_HIT))
    add(_case("contract.paths.basename.miss", ["C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["shifts.py"]}], files=FILES_HIT))
    add(_case("contract.patterns.case_differs.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["Backend/API"]}], files=FILES_HIT))
    add(_case("contract.patterns.middle_star.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/*/shifts.py"]}], files=FILES_HIT))
    add(_case("contract.patterns.trailing_slash.hit", ["C.match.patterns", "PAT.substring"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/api/"]}], files=FILES_HIT))
    for near in ("globalx", "all", "document"):
        add(_case(f"contract.global.near_miss.{near}", ["C.match.global.scope"], rules=[{"id": "g", "description": "zzz", "scope": near}]))
    add(_case("contract.explicit_ref.case_differs.miss", ["C.explicit_ref"], rules=[zz], cc=["contract:ZZ-RULE"]))
    add(_case("contract.explicit_ref.description_equal.miss", ["C.explicit_ref"], rules=[zz], cc=["contract:zzz"]))
    add(_case("contract.explicit_ref.substring.miss", ["C.explicit_ref"], rules=[zz], cc=["contract:zz"]))
    add(_case("contract.explicit_ref.scoped_rule.hit", ["C.explicit_ref"], rules=[{"id": "fe-rule", "description": "frontend only", "scope": "frontend", "paths": ["frontend/app.js"]}], cc=["contract:fe-rule"]))
    add(_case("contract.relevance.uppercase_text.hit", ["C.relevance"], rules=[{"id": "q", "description": "SERVICE Layer"}], group="primary_backend_logic"))
    add(_case("contract.relevance.inside_word.hit", ["C.relevance"], rules=[{"id": "q", "description": "microservices"}], group="primary_backend_logic"))
    add(_case("contract.relevance.scoped_rule.hit", ["C.relevance"], rules=[{"id": "q", "description": "service layer", "scope": "frontend", "paths": ["frontend/app.js"], "patterns": ["frontend"]}], group="primary_backend_logic"))
    # legacy rows carry only baseline-projected fields: unknown / nested / list-valued extras are ignored, not rejected
    for name, extra in (
        ("tags", {"tags": [1, 2]}),
        ("examples", {"examples": [{"a": 1}]}),
        ("rules_dicts", {"rules": [{"name": "n", "check": "c"}]}),
        ("owners_with_empty", {"owners": ["a", ""]}),
        ("unknown_scalar", {"owner": "team"}),
    ):
        add(_case(f"contract.unknown_fields.{name}", ["C.include_all"], rules=[{"id": "x", "description": "api", **extra}], cc=["target_profile:domain_contracts"], cm="CM-C4-LEGACY-UNKNOWN-FIELDS-IGNORED"))
    add(_case("contract.same_id.ordering", ["C.include_all"], rules=[{"id": "x"}, {"id": "x", "description": "b"}], cc=["target_profile:domain_contracts"]))

    # --- display normalization, token grammar, relevance join (adversarial review round 2) ----
    add(_case("contract.patterns.abs_redacted.miss", ["D.redact_abs", "C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["/app/"]}], files=["svc/app/x.py"]))
    add(_case("contract.patterns.tilde_redacted.miss", ["D.redact_abs", "C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["~/app/"]}], files=["svc/app/x.py"]))
    add(_case("contract.patterns.drive_redacted.miss", ["D.redact_drive", "C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["C:app/"]}], files=["svc/C:app/x.py"]))
    add(_case("contract.patterns.backslash.hit", ["D.backslash", "C.match.patterns", "PAT.substring"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend\\api"]}], files=FILES_HIT))
    add(_case("contract.patterns.dot_slash.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["./backend"]}], files=FILES_HIT))
    add(_case("contract.patterns.double_star.miss", ["C.match.patterns"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/**"]}], files=FILES_HIT))
    add(_case("contract.relevance.join_separator.miss", ["C.relevance"], rules=[{"id": "ap", "description": "i"}], group="primary_backend_logic"))
    for token in ("Contract:zz-rule", "contracts:zz-rule", "zz-rule"):
        add(_case(f"contract.token.{token.replace(':', '_')}.miss", ["C.explicit_ref"], rules=[zz], cc=[token]))
    add(_case("contract.token.include_all_padded.miss", ["C.include_all"], rules=[zz], cc=[" target_profile:domain_contracts"]))
    # --- projection / canonicalization / token grammar / row admission (adversarial review round 3) ----
    add(_case("contract.paths.unsorted_duplicates.hit", ["L.sorted_unique"], rules=[{"id": "r", "description": "zzz", "paths": ["b/z.py", "backend/api/shifts.py", "b/z.py", "a/y.py"], "files": ["b", "a", "b"], "patterns": ["zz", "aa", "zz"]}], files=FILES_HIT))
    add(_case("contract.paths.padded_members.hit", ["D.strip"], rules=[{"id": "r", "description": "zzz", "paths": [" backend/api/shifts.py "], "patterns": [" backend/api "]}], files=FILES_HIT))
    add(_case("contract.scalar_path.dot_slash.row", ["C.carrier.path"], rules=[{"id": "r", "description": "zzz", "path": "./backend/api/shifts.py", "file_path": "./a.py"}], files=FILES_HIT))
    add(_case("contract.exact_path.backslash.hit", ["D.exact_backslash", "C.match.paths"], rules=[{"id": "r", "description": "zzz", "paths": ["backend\\api\\shifts.py"]}], files=FILES_HIT))
    add(_case("contract.patterns.tilde_noslash.row", ["D.redact_tilde_slash"], rules=[{"id": "r", "description": "zzz", "patterns": ["~x/a", "~/b"]}], cc=["target_profile:domain_contracts"], files=FILES_HIT))
    add(_case("contract.star_prefix_not_substring.miss", ["PAT.star_prefix"], rules=[{"id": "r", "description": "zzz", "patterns": ["backend/*"]}], files=["x/backend/a.py"]))
    add(_case("contract.row.description_whitespace_and_case", ["L.sorted_unique"], rules=[{"id": "MixedCase", "description": "  two   spaces  inside "}], cc=["target_profile:domain_contracts"]))
    add(_case("contract.token.extra_colon.miss", ["T.split_first"], rules=[{"id": "x:y", "description": "zzz"}, {"id": "x", "description": "zzz"}], cc=["contract:x:y"]))
    add(_case("contract.token.include_all_case.miss", ["C.include_all"], rules=[zz], cc=["TARGET_PROFILE:DOMAIN_CONTRACTS"]))
    add(_case("contract.rows.without_id_or_description", ["C.include_all"], rules=[{"scope": "global", "paths": ["backend/api/shifts.py"]}, {"patterns": ["backend/*"]}, {}], cc=["target_profile:domain_contracts"], cm="CM-C4-LEGACY-CONTRACT-ROWS-ADMITTED"))
    add(_case("contract.rows.global_without_id.hit", ["C.match.global.scope"], rules=[{"scope": "global"}]))
    add(_case("contract.blank_strings.absent", ["C.include_all"], rules=[{"id": "r", "description": "", "scope": "", "path": ""}, {"id": "", "description": "named"}], cc=["target_profile:domain_contracts"]))
    add(_case("contract.rules_null.not_relevant", ["TERM.not_relevant"]))
    # --- packs ------------------------------------------------------------
    alpha = {"id": "alpha", "description": "Alpha pack", "recommended_review_preset": "review:deep"}
    beta = {"id": "beta", "description": "Beta pack"}
    add(_case("pack.include_all.true", ["P.include_all"], packs=[alpha, beta], cc=["target_profile:review_packs"], cm="CM-C4-LEGACY-PACK-INCLUDE-ALL"))
    add(_case("pack.include_all.false", ["P.include_all"], packs=[alpha, beta]))
    add(_case("pack.explicit_ref.hit", ["P.explicit_ref"], packs=[alpha, beta], cc=["contract:alpha"], cm="CM-C4-LEGACY-PACK-EXPLICIT-ID-REF"))
    add(_case("pack.explicit_ref.miss", ["P.explicit_ref"], packs=[alpha, beta], cc=["contract:nope"]))
    # descriptions deliberately do not contain the selector, so only the id half can select
    id_only_sel = [{"id": "alpha", "description": "first"}, {"id": "beta", "description": "second"}]
    add(_case("pack.selected.exact_id", ["P.selected.guard", "P.sel.id_eq", "P.sel.id_sub"], packs=id_only_sel, sel="alpha"))
    add(_case("pack.selected.fuzzy_id", ["P.selected.guard", "P.sel.id_sub"], packs=id_only_sel, sel="alp"))
    add(_case("pack.selected.exact_description", ["P.selected.guard", "P.sel.desc_eq", "P.sel.desc_sub"], packs=[{"id": "p1", "description": "calendar"}, {"id": "p2", "description": "zzz"}], sel="calendar"))
    add(_case("pack.selected.fuzzy_description", ["P.selected.guard", "P.sel.desc_sub"], packs=[{"id": "p1", "description": "calendar scheduling"}, {"id": "p2", "description": "zzz"}], sel="schedul"))
    add(_case("pack.selected.miss", ["P.selected.guard"], packs=[alpha, beta], sel="zzz"))
    add(_case("pack.selected.case_insensitive", ["P.selected.guard", "P.sel.id_sub"], packs=id_only_sel, sel="ALPHA"))
    add(_case("pack.description_only.selected", ["P.selected.guard", "P.sel.desc_eq", "P.sel.desc_sub"], packs=[{"description": "calendar"}], sel="calendar", cm="CM-C4-DESCRIPTION-ONLY-PACK-SELECTED"))
    add(_case("pack.description_only.preserved", ["P.include_all"], packs=[{"description": "calendar"}, {"id": "beta", "description": "Beta pack"}], cc=["target_profile:review_packs"], cm="CM-C4-DESCRIPTION-ONLY-PACK-PRESERVED"))
    add(_case("pack.id_only.include_all", ["P.include_all"], packs=[{"id": "only-id"}], cc=["target_profile:review_packs"]))
    add(_case("pack.id_only.explicit_ref", ["P.explicit_ref"], packs=[{"id": "only-id"}], cc=["contract:only-id"]))
    add(_case("pack.modern_fields_ignored", ["P.match_chunk"], packs=[{"id": "m", "description": "zzz", "paths": ["backend/api/shifts.py"], "patterns": ["backend/*"], "scope": "global", "is_global": True, "domain_contract": "c1"}], files=FILES_HIT, cm="CM-C4-LEGACY-PACK-PROJECTION"))
    add(_case("pack.relevance.id_part", ["P.relevance", "P.relevance.id_part"], packs=[{"id": "service-pack", "description": "zzz"}], group="primary_backend_logic", cm="CM-C4-LEGACY-PACK-RELEVANCE"))
    add(_case("pack.relevance.desc_part", ["P.relevance", "P.relevance.desc_part"], packs=[{"id": "qq", "description": "service layer"}], group="primary_backend_logic", cm="CM-C4-LEGACY-PACK-RELEVANCE"))
    add(_case("pack.relevance.miss", ["P.relevance"], packs=[{"id": "qq", "description": "zzz"}], group="primary_backend_logic"))
    add(_case("pack.relevance.group_without_keywords.miss", ["P.relevance"], packs=[{"id": "qq", "description": "service"}], group="other"))
    for group, keywords in SEMANTIC_GROUP_KEYWORDS.items():
        kw = keywords[0]
        add(_case(f"pack.relevance.{group}", ["P.relevance"], packs=[{"id": f"zz-{kw}", "description": f"zz {kw} zz"}], group=group, cm="CM-C4-LEGACY-PACK-RELEVANCE"))
    add(_case("pack.explicit_ref.case_differs.miss", ["P.explicit_ref"], packs=[alpha], cc=["contract:ALPHA"]))
    add(_case("pack.explicit_ref.description_equal.miss", ["P.explicit_ref"], packs=[alpha], cc=["contract:Alpha pack"]))
    add(_case("pack.explicit_ref.substring.miss", ["P.explicit_ref"], packs=[alpha], cc=["contract:alph"]))
    add(_case("pack.selected.reverse_alias.miss", ["P.selected.guard"], packs=id_only_sel, sel="foo-alpha"))
    add(_case("pack.selected.description_case_insensitive", ["P.selected.guard", "P.sel.desc_sub"], packs=[{"id": "p1", "description": "Calendar Scheduling"}, {"id": "p2", "description": "zzz"}], sel="CALENDAR"))
    add(_case("pack.relevance.uppercase_text.hit", ["P.relevance"], packs=[{"id": "ZZ-Q", "description": "SERVICE Layer"}], group="primary_backend_logic"))
    add(_case("pack.token.include_all_padded.miss", ["P.include_all"], packs=[alpha], cc=[" target_profile:review_packs"]))
    add(_case("pack.token.bare_id.miss", ["P.explicit_ref"], packs=[alpha], cc=["alpha"]))
    add(_case("pack.relevance.join_separator.miss", ["P.relevance"], packs=[{"id": "ap", "description": "i"}], group="primary_backend_logic"))
    add(_case("pack.modern_fields.emitted_row", ["P.include_all"], packs=[{"id": "m", "description": "zzz", "recommended_review_preset": "review:deep", "paths": ["backend/api/shifts.py"], "patterns": ["backend/*"], "scope": "global", "is_global": True, "domain_contract": "c1", "notes": "n", "critical": True}], cc=["target_profile:review_packs"], cm="CM-C4-LEGACY-PACK-PROJECTION"))
    add(_case("pack.target_metadata.null_and_blank", ["P.include_all"], packs=[{"id": "m", "description": "zzz", "paths": None, "scope": None, "notes": 5}], cc=["target_profile:review_packs"]))
    add(_case("pack.rows.null_keys_preserved", ["P.include_all"], packs=[{"id": "p"}, {"description": "d only"}, {"id": "q", "description": "dq", "recommended_review_preset": "review:deep"}], cc=["target_profile:review_packs"], cm="CM-C4-LEGACY-PACK-ROWS-EXACT"))
    add(_case("pack.duplicates_not_deduped", ["P.include_all"], packs=[alpha, alpha], cc=["target_profile:review_packs"]))
    add(_case("pack.row.case_and_whitespace_preserved", ["P.include_all"], packs=[{"id": "MixedCase", "description": "  A  B ", "recommended_review_preset": "Review:DEEP"}], cc=["target_profile:review_packs"]))
    add(_case("pack.blank_strings.absent", ["P.include_all"], packs=[{"id": "", "description": "named"}, {"id": "p", "description": ""}], cc=["target_profile:review_packs"]))
    # baseline TypeError (None + str) when relevance is evaluated for a pack lacking id or description
    add(_case("pack.relevance.baseline_raises.id_only", ["P.relevance"], packs=[{"id": "qq"}], group="primary_backend_logic"))
    add(_case("pack.relevance.baseline_raises.description_only", ["P.relevance"], packs=[{"description": "zzz"}], group="frontend_ui"))

    # --- terminal ---------------------------------------------------------
    add(_case("terminal.zero_context.not_relevant", ["TERM.not_relevant"], rules=[fe], packs=[beta], cm="CM-C4-LEGACY-NOT-RELEVANT"))
    add(_case("terminal.contract_only", ["TERM.not_relevant"], rules=[be], packs=[beta], files=FILES_HIT))
    add(_case("terminal.pack_only", ["TERM.not_relevant"], rules=[fe], packs=[alpha], sel="alpha"))
    add(_case("terminal.sources_absent.not_relevant", ["TERM.not_relevant"]))
    add(_case("terminal.sources_empty.not_relevant", ["TERM.not_relevant"], rules=[], packs=[]))
    return cases


# ---------------------------------------------------------------------------
# Baseline predicates + mutation operators (text edits of the baseline source)
# ---------------------------------------------------------------------------

REL_EXPR = '(item.get("id", "") + " " + item.get("description", "")).lower() for keyword in relevance_keywords'


def _predicates() -> list[dict[str, Any]]:
    live = "live"
    rows = [
        ("C.include_all", live, "include_all_contracts (unconditional: scoped and unscoped rules)"),
        ("C.explicit_ref", live, 'item.get("id") in referenced_contracts  (contracts)'),
        ("C.match.paths", live, "canonical(paths) intersects chunk files (exact identity only; no substring/glob)"),
        ("C.carrier.file_path", live, "file_path is a path-bearing carrier"),
        ("C.carrier.path", live, "path is a path-bearing carrier"),
        ("C.carrier.files", live, "files is a path-bearing carrier"),
        ("C.carrier.source_files", live, "source_files is a path-bearing carrier"),
        ("C.carrier.related_files", live, "related_files is a path-bearing carrier"),
        ("C.match.patterns", live, "_matches_pattern(path, patterns)"),
        ("D.redact_abs", live, "sanitize_display_path redacts absolute / ~/ patterns and paths to a literal"),
        ("D.redact_drive", live, "sanitize_display_path redacts drive-letter patterns and paths"),
        ("D.backslash", live, "sanitize_display_path converts backslashes to slashes"),
        ("D.strip", live, "sanitize_display_path strips surrounding whitespace"),
        ("D.redact_tilde_slash", live, "sanitize_display_path redacts only ~/ (not ~name)"),
        ("D.exact_backslash", "redundant", "canonical_repo_path converts backslashes in exact paths -- subsumed: legacy rows are display-sanitized (backslash -> slash) before the exact-path join"),
        ("L.sorted_unique", live, "path and pattern lists are sorted and de-duplicated"),
        ("PAT.star_prefix", live, "trailing-star pattern matches as a path PREFIX, not as a substring"),
        ("T.split_first", live, "contract:<id> token takes everything after the FIRST colon"),
        ("PAT.suffix_star", live, 'pattern.endswith("*") and path.startswith(pattern[:-1])'),
        ("PAT.substring", live, "pattern in path"),
        ("C.match.global.scope", live, 'scope.lower() == "global"'),
        ("C.match.global.flag", live, "is_global is True"),
        ("C.relevance", live, "semantic-group keywords over id + description (contracts)"),
        ("C.relevance.id_part", live, "id half of the contract relevance text"),
        ("C.relevance.desc_part", live, "description half of the contract relevance text"),
        ("P.include_all", live, "include_all_packs"),
        ("P.explicit_ref", live, 'item.get("id") in referenced_contracts  (packs)'),
        ("P.selected.guard", live, "selected_pack and _review_pack_matches_selected"),
        ("P.sel.id_eq", "redundant", "id.lower() == selected -- subsumed by `selected in id.lower()` (equality implies containment)"),
        ("P.sel.id_sub", live, "selected in id.lower()"),
        ("P.sel.desc_eq", "redundant", "description.lower() == selected -- subsumed by `selected in description.lower()`"),
        ("P.sel.desc_sub", live, "selected in description.lower()"),
        ("P.match_chunk", "dead", "_contract_matches_chunk(pack): the baseline pack projection {id, description, recommended_review_preset} carries no path/pattern/scope/is_global field, so the disjunct can never be true"),
        ("P.relevance", live, "semantic-group keywords over id + description (packs)"),
        ("P.relevance.id_part", live, "id half of the pack relevance text"),
        ("P.relevance.desc_part", live, "description half of the pack relevance text"),
        ("TERM.not_relevant", live, "no contract and no pack -> contracts_context_not_relevant:<chunk_id>"),
    ]
    for group, keywords in SEMANTIC_GROUP_KEYWORDS.items():
        for kw in keywords:
            rows.append((f"KW.{group}.{kw}", live, f"relevance keyword {kw!r} of group {group}"))
    return [{"id": pid, "kind": kind, "expression": expr} for pid, kind, expr in rows]


def _mutants() -> dict[str, list[tuple[str, str, int]]]:
    m: dict[str, list[tuple[str, str, int]]] = {
        "C.include_all": [("            include_all_contracts\n", "            False\n", 0)],
        "C.explicit_ref": [('or item.get("id") in referenced_contracts', "or False", 0)],
        "C.match.paths": [("if contract_paths and contract_paths.intersection(chunk_files):", "if False:", 0)],
        "C.carrier.file_path": [('for key in ("file_path", "file", "original_file", "path"):', 'for key in ("file", "original_file", "path"):', 0)],
        "C.carrier.path": [('for key in ("file_path", "file", "original_file", "path"):', 'for key in ("file_path", "file", "original_file"):', 0)],
        "C.carrier.files": [('for key in ("files", "paths", "source_files", "related_files"):', 'for key in ("paths", "source_files", "related_files"):', 0)],
        "C.carrier.source_files": [('for key in ("files", "paths", "source_files", "related_files"):', 'for key in ("files", "paths", "related_files"):', 0)],
        "C.carrier.related_files": [('for key in ("files", "paths", "source_files", "related_files"):', 'for key in ("files", "paths", "source_files"):', 0)],
        "D.redact_abs": [('if normalized.startswith("/") or normalized.startswith("~/"):', "if False:", 0)],
        "D.redact_drive": [('if len(normalized) >= 2 and normalized[1] == ":":', "if False:", 1)],
        "D.strip": [('normalized = path.replace("\\\\", "/").strip()', 'normalized = path.replace("\\\\", "/")', 1)],
        "D.redact_tilde_slash": [('normalized.startswith("~/"):', 'normalized.startswith("~"):', 1)],
        "D.exact_backslash": [('normalized = path.replace("\\\\", "/").strip()', "normalized = path.strip()", 0)],
        "L.sorted_unique": [("return sorted({item for item in paths if item})", "return [item for item in paths if item]", 0)],
        "PAT.star_prefix": [("if normalized.endswith(\"*\") and path.startswith(normalized[:-1]):", "if normalized.endswith(\"*\") and normalized[:-1] in path:", 0)],
        "T.split_first": [('item.split(":", 1)[1] for item in chunk_contracts', 'item.rsplit(":", 1)[1] for item in chunk_contracts', 0)],
        "D.backslash": [('normalized = path.replace("\\\\", "/").strip()', "normalized = path.strip()", 1)],
        "C.match.patterns": [("if patterns and any(_matches_pattern(path, patterns) for path in chunk_files):", "if False:", 0)],
        "PAT.suffix_star": [('if normalized.endswith("*") and path.startswith(normalized[:-1]):', "if False:", 0)],
        "PAT.substring": [("if normalized in path:", "if False:", 0)],
        "C.match.global.scope": [('if scope and scope.lower() == "global":', "if False:", 0)],
        "C.match.global.flag": [('return item.get("is_global") is True', "return False", 0)],
        "C.relevance": [("relevance_keywords\n                and any(keyword in ", "False\n                and any(keyword in ", 0)],
        "C.relevance.id_part": [(REL_EXPR, '(" " + item.get("description", "")).lower() for keyword in relevance_keywords', 0)],
        "C.relevance.desc_part": [(REL_EXPR, '(item.get("id", "") + " ").lower() for keyword in relevance_keywords', 0)],
        "P.include_all": [("            include_all_packs\n", "            False\n", 0)],
        "P.explicit_ref": [('or item.get("id") in referenced_contracts', "or False", 1)],
        "P.selected.guard": [("selected_pack and _review_pack_matches_selected(item, selected_pack)", "False", 0)],
        "P.sel.id_eq": [("id_lower == selected", "False", 0)],
        "P.sel.id_sub": [("selected in id_lower", "False", 0)],
        "P.sel.desc_eq": [("description_lower == selected", "False", 0)],
        "P.sel.desc_sub": [("selected in description_lower", "False", 0)],
        "P.match_chunk": [("_contract_matches_chunk(item, chunk_files=chunk_file_set)", "False", 1)],
        "P.relevance": [("relevance_keywords\n                and any(keyword in ", "False\n                and any(keyword in ", 1)],
        "P.relevance.id_part": [(REL_EXPR, '(" " + item.get("description", "")).lower() for keyword in relevance_keywords', 1)],
        "P.relevance.desc_part": [(REL_EXPR, '(item.get("id", "") + " ").lower() for keyword in relevance_keywords', 1)],
        "TERM.not_relevant": [("if not filtered_contracts and not filtered_packs:", "if False:", 0)],
    }
    for group, keywords in SEMANTIC_GROUP_KEYWORDS.items():
        original = f'"{group}": ({", ".join(chr(34) + k + chr(34) for k in keywords)}),'
        for kw in keywords:
            rest = tuple(k for k in keywords if k != kw)
            replaced = f'"{group}": ({", ".join(chr(34) + k + chr(34) for k in rest)}{"," if len(rest) == 1 else ""}),' if rest else f'"{group}": tuple(),'
            m[f"KW.{group}.{kw}"] = [(original, replaced, 0)]
    return m


def _apply(source: str, ops: list[tuple[str, str, int]]) -> str:
    for old, new, occurrence in ops:
        positions = []
        start = 0
        while (idx := source.find(old, start)) != -1:
            positions.append(idx)
            start = idx + len(old)
        if occurrence >= len(positions):
            raise SystemExit(f"STOP_LEGACY_ORACLE_INCOMPLETE: mutation anchor not found: {old!r} #{occurrence}")
        idx = positions[occurrence]
        source = source[:idx] + new + source[idx + len(old):]
    return source


# ---------------------------------------------------------------------------
# Baseline loading / execution
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout


def baseline_available() -> bool:
    try:
        return _git("cat-file", "-t", BASELINE_COMMIT).strip() == "commit"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_baseline_source() -> str:
    oid = _git("rev-parse", f"{BASELINE_COMMIT}:{BASELINE_PATH}").strip()
    if oid != BASELINE_BLOB_OID:
        raise SystemExit(f"STOP_LEGACY_BASELINE_DOMAIN_UNRESOLVED: blob oid {oid} != {BASELINE_BLOB_OID}")
    return _git("show", f"{BASELINE_COMMIT}:{BASELINE_PATH}")


def _module_from(source: str, name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"<baseline:{BASELINE_COMMIT[:12]}>"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - frozen, pinned baseline source
    return module


def _intake(target_profile: dict[str, Any]) -> Any:
    from app.agent_review.schemas import ReviewIntake

    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "example/target",
            "target_profile": target_profile,
            "artifacts": {},
            "artifact_status": [],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-10-05T00:00:00Z",
            "status": "complete",
        }
    )


ANNOTATION_KEYS = ("required", "required_reasons", "effective_contracts")


def observe(ctx: dict[str, Any], limitations: list[str]) -> dict[str, Any]:
    """Frozen-legacy observable projection (shared, by definition, with the test): the WHOLE emitted row of
    every contract and pack (None/empty = absent), minus the Gate-A internal annotations. A row that gains
    or loses a field relative to the baseline is an observable divergence."""

    def row(value: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in value.items() if k not in ANNOTATION_KEYS}

    return {
        "contracts": [row(r) for r in ctx.get("domain_contracts", [])],
        "packs": [row(r) for r in ctx.get("review_packs", [])],
        "not_relevant": any(lim.startswith("contracts_context_not_relevant:") for lim in limitations),
    }


def run_case(module: types.ModuleType, case_input: dict[str, Any]) -> dict[str, Any]:
    try:
        ctx, limits = module.contracts_context(
            _intake(case_input["target_profile"]),
            chunk_files=list(case_input["chunk_files"]),
            chunk_contracts=list(case_input["chunk_contracts"]),
            chunk_id=case_input["chunk_id"],
            selected_contract_pack=case_input["selected_contract_pack"],
            semantic_group=case_input["semantic_group"],
        )
    except TypeError as exc:
        return {"outcome": "raises", "error": type(exc).__name__}
    return {"outcome": "ok", **observe(ctx, limits)}


def _totalized(source: str) -> str:
    """Baseline with the one defect that crashes it (None + str on pack relevance text) closed:
    pack id/description None are read as empty. Used ONLY to define the expected observation
    for inputs on which the baseline itself raises."""
    patched = source.replace(
        '            {\n                "id": _clean_text(item.get("id")),\n                "description": _clean_text(item.get("description")),\n                "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),\n            }',
        '            {\n                "id": _clean_text(item.get("id")) or "",\n                "description": _clean_text(item.get("description")) or "",\n                "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),\n            }',
    )
    if patched == source:
        raise SystemExit("STOP_LEGACY_ORACLE_INCOMPLETE: totalization anchor not found")
    return patched


def generate() -> dict[str, Any]:
    source = load_baseline_source()
    corpus = build_corpus()
    ids = [c["id"] for c in corpus]
    if len(ids) != len(set(ids)):
        raise SystemExit("STOP_LEGACY_ORACLE_INCOMPLETE: duplicate case ids")

    baseline = _module_from(source, "_v1_c2_baseline_6bbd2f9")
    totalized = _module_from(_totalized(source), "_v1_c2_baseline_6bbd2f9_totalized")

    cases = []
    for case in corpus:
        observed = run_case(baseline, case["input"])
        row = dict(case)
        row["baseline_observation"] = observed
        if observed["outcome"] == "raises":
            row["totalized_baseline_observation"] = run_case(totalized, case["input"])
        cases.append(row)

    base_by_id = {c["id"]: c["baseline_observation"] for c in cases}
    kill_map: dict[str, list[str]] = {}
    mutants = _mutants()
    predicates = _predicates()
    for predicate in predicates:
        pid = predicate["id"]
        mutated = _module_from(_apply(source, mutants[pid]), f"_v1_c2_mutant_{len(kill_map)}")
        killers = [c["id"] for c in corpus if run_case(mutated, c["input"]) != base_by_id[c["id"]]]
        kill_map[pid] = killers

    gaps = [p["id"] for p in predicates if p["kind"] == "live" and not kill_map[p["id"]]]
    if gaps:
        raise SystemExit(f"STOP_LEGACY_DIFFERENTIAL_BRANCH_GAP: live predicates without a discriminating case: {gaps}")
    unexpected_survivors = [p["id"] for p in predicates if p["kind"] != "live" and kill_map[p["id"]]]
    if unexpected_survivors:
        raise SystemExit(f"STOP_LEGACY_ORACLE_INCOMPLETE: declared redundant/dead predicates were killed: {unexpected_survivors}")

    declared_cover_ids = {p["id"] for p in predicates}
    for case in corpus:
        unknown = set(case["covers"]) - declared_cover_ids
        if unknown:
            raise SystemExit(f"STOP_LEGACY_ORACLE_INCOMPLETE: case {case['id']} covers unknown predicates {sorted(unknown)}")

    return {
        "schema": SCHEMA,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_payload_cost_model_blob_oid": BASELINE_BLOB_OID,
        "generation_method": (
            "scripts/generate-v1-c2-legacy-differential-oracle.py loads `git show <baseline_commit>:app/agent_review/"
            "payload_cost_model.py` (blob oid verified) as an isolated module and records contracts_context() "
            "observations over the corpus; predicate_kill_map is computed by mutating each baseline predicate in an "
            "isolated copy of that source and recording the cases whose observation changes. The fixture stores "
            "INPUT + EXPECTED OBSERVATION only; no baseline algorithm is copied into production or tests."
        ),
        "observable_projection": {
            "contracts": "ordered whole rows emitted for domain_contracts, exactly (explicit nulls included), minus internal annotations",
            "packs": "ordered whole rows emitted for review_packs, exactly (explicit nulls included), minus internal annotations",
            "not_relevant": "true iff a contracts_context_not_relevant:<chunk_id> limitation is emitted",
            "normalization": "none: whole-row exact equality, explicit nulls included (a legacy row never gains, loses or nulls a field)",
            "excluded": "internal required/required_reasons/effective_contracts annotations and Gate-A typed limitations (compared separately as INTENTIONAL divergences)",
            "baseline_raises": "inputs on which the baseline raises TypeError carry totalized_baseline_observation (baseline with None id/description read as empty)",
        },
        "admitted_baseline_shapes": [
            "target_profile.domain_contracts = {\"rules\": [dict, ...]}",
            "target_profile.review_packs = {\"packs\": [dict, ...]}",
            "chunk_contracts items: target_profile:domain_contracts | target_profile:review_packs | contract:<id>",
        ],
        "additive_compatibility_shapes": [
            "domain_contracts as a top-level list of dicts (baseline read it as no contracts)",
            "review_packs as a top-level list of dicts (baseline read it as no packs)",
            "legacy PACK rows with neither id nor description are not admitted (SUBTRACTIVE but lossless: the pack projection has no other field; legacy CONTRACT rows are all admitted)",
        ],
        "baseline_predicates": predicates,
        "mutation_operators": {pid: [list(op) for op in ops] for pid, ops in mutants.items()},
        "predicate_kill_map": kill_map,
        "cases": cases,
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if the committed fixture differs from a fresh generation")
    args = parser.parse_args()
    if not baseline_available():
        print(f"baseline commit {BASELINE_COMMIT} not available in this checkout (shallow clone?)", file=sys.stderr)
        return 2
    rendered = render(generate())
    if args.check:
        if not FIXTURE.exists() or FIXTURE.read_text(encoding="utf-8") != rendered:
            print("fixture differs from a fresh generation from the frozen baseline", file=sys.stderr)
            return 1
        print("fixture matches a fresh generation from the frozen baseline")
        return 0
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(rendered, encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
