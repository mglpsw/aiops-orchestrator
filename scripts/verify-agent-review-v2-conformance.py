#!/usr/bin/env python3
"""Verify an AgentReview v2 dual-target conformance matrix artifact (#86).

Reads a JSON conformance matrix (the shape ``tests/agent_review/
test_v2_dual_target_e2e.py`` builds from real pipeline objects for both the
"AgentEscala" and "InterLeitos" synthetic fixtures) and checks it fail-closed
against the issue's own minimum-fields and cross-cutting-regression list.

This CLI is deliberately NOT a core engine component and makes no readiness
or gate decision of its own -- ``ReviewReadinessV2``/``compute_readiness_
decision_v2`` remain the sole authority for that. It only proves the AUDIT
ARTIFACT a conformance run produced is structurally complete, internally
consistent, and free of any secret-like or local-path content -- reusing
``app.agent_review.redaction``'s own canonical sanitizer for that last
check, never a bespoke heuristic.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.contracts_v2 import ReadinessReasonV2, ReadinessStateV2  # noqa: E402
from app.agent_review.redaction import redact_content  # noqa: E402

CONFORMANCE_SCHEMA_ID_V2 = "agent-review.v2-conformance-matrix"

INPUT_INVALID_REASON_V2 = "conformance_input_invalid"
SCHEMA_MISMATCH_REASON_V2 = "conformance_schema_mismatch"
MISSING_TARGETS_REASON_V2 = "conformance_missing_targets"
FIELD_MISSING_REASON_V2 = "conformance_target_field_missing"
FIELD_INVALID_REASON_V2 = "conformance_target_field_invalid"
NETWORK_OR_PROVIDER_EVIDENCE_MISSING_REASON_V2 = "conformance_network_or_provider_evidence_missing"
SANITIZATION_LEAK_REASON_V2 = "conformance_sanitization_leak_detected"
DUPLICATE_TARGET_REASON_V2 = "conformance_duplicate_target_id"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_TARGET_FIELDS = (
    "target_id",
    "repo",
    "profile_hash",
    "policy_hash",
    "manifest_hash",
    "evidence_hash",
    "expected_head_sha",
    "evaluated_head_sha",
    "expected_chunks",
    "processed_chunks",
    "expected_fragments",
    "processed_fragments",
    "coverage_status",
    "must_review_coverage_complete",
    "binding_result",
    "readiness_state",
    "reason_codes",
    "findings",
    "evidence_no_network",
    "evidence_no_provider",
    "evidence_no_github_write",
    "evidence_no_ct102",
    "canonical_output_digest",
)

_SHA256_FIELDS = ("profile_hash", "policy_hash", "manifest_hash", "evidence_hash", "canonical_output_digest")
_BOOL_FIELDS = (
    "must_review_coverage_complete",
    "evidence_no_network",
    "evidence_no_provider",
    "evidence_no_github_write",
    "evidence_no_ct102",
)
_NONNEGATIVE_INT_FIELDS = ("expected_chunks", "processed_chunks", "expected_fragments", "processed_fragments")

_VALID_READINESS_STATES = {state.value for state in ReadinessStateV2}
_VALID_REASON_CODES = {code.value for code in ReadinessReasonV2}


class ConformanceVerificationError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conformance", required=True, help="path to the conformance matrix JSON")
    return parser.parse_args(argv)


def _read_json(path: str) -> object:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConformanceVerificationError(INPUT_INVALID_REASON_V2, str(exc)) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConformanceVerificationError(INPUT_INVALID_REASON_V2, str(exc)) from exc


def _verify_target(entry: object) -> None:
    if not isinstance(entry, dict):
        raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, "target entry must be an object")

    missing = [field for field in _REQUIRED_TARGET_FIELDS if field not in entry]
    if missing:
        raise ConformanceVerificationError(FIELD_MISSING_REASON_V2, f"missing fields: {sorted(missing)}")

    for field in _SHA256_FIELDS:
        value = entry[field]
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, f"{field} must be a lowercase sha256 hex digest")

    for field in _BOOL_FIELDS:
        if not isinstance(entry[field], bool):
            raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, f"{field} must be a boolean")

    for field in _NONNEGATIVE_INT_FIELDS:
        value = entry[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, f"{field} must be a non-negative integer")

    if entry["readiness_state"] not in _VALID_READINESS_STATES:
        raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, "readiness_state is not a valid ReadinessStateV2")

    reason_codes = entry["reason_codes"]
    if not isinstance(reason_codes, list) or not all(code in _VALID_REASON_CODES for code in reason_codes):
        raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, "reason_codes must all be valid ReadinessReasonV2 values")
    if len(reason_codes) != len(set(reason_codes)):
        raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, "reason_codes must not contain duplicates")

    if not isinstance(entry["findings"], list):
        raise ConformanceVerificationError(FIELD_INVALID_REASON_V2, "findings must be a list")

    if not all(entry[field] is True for field in _BOOL_FIELDS if field.startswith("evidence_no_")):
        raise ConformanceVerificationError(
            NETWORK_OR_PROVIDER_EVIDENCE_MISSING_REASON_V2,
            "every evidence_no_* field must be true -- this suite proves the ABSENCE of network/"
            "provider/GitHub-write/CT102 activity, never merely omits reporting on it",
        )


def verify_conformance_matrix(matrix: object) -> None:
    if not isinstance(matrix, dict):
        raise ConformanceVerificationError(INPUT_INVALID_REASON_V2, "matrix must be a JSON object")
    if matrix.get("schema_id") != CONFORMANCE_SCHEMA_ID_V2:
        raise ConformanceVerificationError(SCHEMA_MISMATCH_REASON_V2)

    targets = matrix.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ConformanceVerificationError(MISSING_TARGETS_REASON_V2)

    target_ids: list[str] = []
    for entry in targets:
        _verify_target(entry)
        target_ids.append(entry["target_id"])
    if len(target_ids) != len(set(target_ids)):
        raise ConformanceVerificationError(DUPLICATE_TARGET_REASON_V2)

    # Reuse the SAME canonical sanitizer the pipeline itself trusts (never a
    # bespoke ad hoc PHI/secret heuristic here) to prove nothing sanitizable
    # slipped into the published artifact.
    sanitized, report = redact_content(matrix, source="agent-review-v2-conformance-verify")
    if sanitized != matrix or report.secret_like_values_found:
        raise ConformanceVerificationError(SANITIZATION_LEAK_REASON_V2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        matrix = _read_json(args.conformance)
        verify_conformance_matrix(matrix)
    except ConformanceVerificationError as exc:
        detail = f" ({exc.detail})" if exc.detail else ""
        print(f"error: {exc.reason_code}{detail}", file=sys.stderr)
        return 1
    print("ok: conformance matrix verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
