from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

import app.agent_review.review_content_extraction_v2 as extraction_module
import app.agent_review.review_content_v2 as content_contract_module
from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
from app.agent_review.payload_builder_v2 import build_chunk_payload_v2
from app.agent_review.review_content_extraction_v2 import extract_review_content_v2
from app.agent_review.review_transport_v2 import agent_router_transport_v2, execute_chunk_review_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from evals.agent_review_v2.outbound_safety_oracle_spike import (
    OutboundSafetyBlockedV2,
    guard_exact_outbound_body_v2,
    inspect_outbound_body_v2,
)
from tests.agent_review.test_review_transport_v2 import (
    _commit_all,
    _grouping_policy,
    _init_repo,
    _profile,
)


def _build_real_content_with_forward_detector_blinded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed_text: str,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("before = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "app.py").write_text(changed_text + "\n", encoding="utf-8")
    head_sha = _commit_all(repo, "head")

    profile = _profile()
    file_diffs = acquire_authoritative_diff_v2(
        repo, base_sha=base_sha, head_sha=head_sha
    )
    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=_grouping_policy(),
        repo="example/repo",
        pr_number=1,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=head_sha,
        toolrepo_sha="b" * 40,
        evidence_hash="c" * 64,
        max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    manifest = outcome.manifest
    assert manifest is not None
    payload_by_chunk_id = {
        chunk.chunk_id: build_chunk_payload_v2(manifest, chunk)
        for chunk in manifest.chunks
    }

    # Deliberately blind BOTH forward checks. This is the mutation the spike
    # exists to survive: if the outbound oracle were another call to the same
    # sanitizer, the secret would now be invisible to both sides.
    monkeypatch.setattr(extraction_module, "redact_text", lambda text, state: text)
    monkeypatch.setattr(extraction_module, "sanitize_artifact_value", lambda value: value)
    monkeypatch.setattr(content_contract_module, "sanitize_artifact_value", lambda value: value)

    content = extract_review_content_v2(
        repo_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        manifest=manifest,
        payload_sha256_by_chunk_id={
            chunk_id: payload.payload_sha256
            for chunk_id, payload in payload_by_chunk_id.items()
        },
        target_profile=profile,
    )
    return manifest, content, payload_by_chunk_id


def _mandatory_secret_cases() -> list[tuple[str, str]]:
    # Provider-shaped values are assembled at runtime so repository secret
    # scanning never sees a committed token-shaped test literal.
    slack = "xoxb-" + "123456789012" + "-" + "ABCDEFGHIJKLMNO"
    aws = "AKIA" + "1234567890ABCDEF"
    return [
        ("password=13572468", "13572468"),
        ("DBPASSWORD=Ab9Cd7Ef5Gh3Jk1Lm", "Ab9Cd7Ef5Gh3Jk1Lm"),
        ("master_key=mk-Secret9274", "mk-Secret9274"),
        ("csrf_token=csrf-927461", "csrf-927461"),
        (
            "session=eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNA",
            "eyJhbGciOiJIUzI1NiJ9",
        ),
        ("pin=12", "pin=12"),
        ('password="unterminated927461', "unterminated927461"),
        ("API_KEY=[REDACTED]\npassword=927461", "password=927461"),
        ("aws=" + aws, aws),
        ("slack=" + slack, "xoxb-123456789012"),
        ("blob=aB3dE5fG7hJ9kL2mN4pQ6rS8tV0xY1zC", "aB3dE5fG7hJ9kL2mN4pQ6rS8tV0xY1zC"),
    ]


def _round2_closed_world_false_safe_cases() -> list[tuple[str, str]]:
    """Concrete false-SAFE shapes reproduced by independent review round 1
    against frozen head 4ad18ecb96ec2bd0c22a6fe7b48f2da2d1cd2365 (PR #293
    comment). Each of these previously returned OUTBOUND_SAFE from
    ``inspect_outbound_body_v2`` despite carrying an exact secret value, via
    six distinct mechanisms:

    1. dict-literal value nested inside a non-sensitive-keyed assignment
       (``config = {"password": "Hunter2"}``) was never recursively
       inspected for an embedded sensitive key.
    2. a bare quoted-key dict-literal line (``"password": "Hunter2"``) did
       not match the old line-anchored assignment regex at all, because the
       anchor required the key to be a bare identifier at line start.
    3. kwarg-call shape (``connect(password="Hunter2")``) was invisible to
       the same line-start anchor.
    4. ``password=$ecret123`` exploited a *prefix*-based placeholder
       carve-out (``startswith("$")``) instead of a bounded grammar.
    5. ``password=[REDACTED]Hunter2`` exploited the same prefix carve-out
       (``startswith("[redacted")``).
    6. ``API_TOKEN=<64-hex secret>`` exploited two compounding bugs: the key
       normaliser did not classify a bare ``...token`` suffix as sensitive,
       and — even if it had reached the entropy path — a global "40/64 hex
       chars is probably a SHA" exemption would have cleared it regardless
       of key context.
    """

    hex_token = __import__("hashlib").sha256(b"round2-api-token-fixture").hexdigest()
    assert len(hex_token) == 64
    return [
        ('config = {"password": "Hunter2"}', "Hunter2"),
        ('"password": "Hunter2"', "Hunter2"),
        ('connect(password="Hunter2")', "Hunter2"),
        ("password=$ecret123", "$ecret123"),
        ("password=[REDACTED]Hunter2", "Hunter2"),
        (f"API_TOKEN={hex_token}", hex_token),
    ]


@pytest.mark.parametrize(("changed_text", "needle"), _round2_closed_world_false_safe_cases())
def test_round2_closed_world_false_safes_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_text: str,
    needle: str,
) -> None:
    """RED before the round-2 fix, GREEN after: absence of a positive match
    in an incomplete open grammar must never be promoted to OUTBOUND_SAFE.
    """

    manifest, content, payload_by_chunk_id = _build_real_content_with_forward_detector_blinded(
        tmp_path, monkeypatch, changed_text=changed_text
    )
    chunk_content = content.chunks[0]
    payload = payload_by_chunk_id[chunk_content.chunk_id]

    captured_body: list[bytes] = []
    real_http_delegate_called = False

    def _real_http_delegate():
        nonlocal real_http_delegate_called
        real_http_delegate_called = True
        raise AssertionError("real HTTP delegate must never run for unsafe outbound material")

    def _guarded_http_open(http_request, timeout_seconds):
        del timeout_seconds
        body = http_request.data
        assert isinstance(body, bytes)
        captured_body.append(body)
        return guard_exact_outbound_body_v2(body, _real_http_delegate)

    transport = agent_router_transport_v2(
        base_url="https://router.example/",
        api_key="fixture-api-key",
        model="review:code",
    )
    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_guarded_http_open,
    ):
        with pytest.raises(OutboundSafetyBlockedV2) as excinfo:
            execute_chunk_review_v2(
                chunk_content,
                run_id=content.run_id,
                head_sha=manifest.identity.head_sha,
                payload=payload,
                transport=transport,
            )

    assert len(captured_body) == 1
    assert needle.encode("utf-8") in captured_body[0]
    assert excinfo.value.report.verdict == "OUTBOUND_NOT_PROVEN_SAFE"
    assert excinfo.value.report.findings
    assert real_http_delegate_called is False


@pytest.mark.parametrize(("changed_text", "needle"), _mandatory_secret_cases())
def test_independent_oracle_blocks_exact_pre_http_bytes_when_forward_detector_is_blind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_text: str,
    needle: str,
) -> None:
    manifest, content, payload_by_chunk_id = _build_real_content_with_forward_detector_blinded(
        tmp_path, monkeypatch, changed_text=changed_text
    )
    chunk_content = content.chunks[0]
    payload = payload_by_chunk_id[chunk_content.chunk_id]

    captured_body: list[bytes] = []
    real_http_delegate_called = False

    def _real_http_delegate():
        nonlocal real_http_delegate_called
        real_http_delegate_called = True
        raise AssertionError("real HTTP delegate must never run for unsafe outbound material")

    def _guarded_http_open(http_request, timeout_seconds):
        del timeout_seconds
        body = http_request.data
        assert isinstance(body, bytes)
        captured_body.append(body)
        # The EXACT bytes produced by agent_router_transport_v2 are handed to
        # the independent oracle; there is no re-serialization here.
        return guard_exact_outbound_body_v2(body, _real_http_delegate)

    transport = agent_router_transport_v2(
        base_url="https://router.example/",
        api_key="fixture-api-key",
        model="review:code",
    )
    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_guarded_http_open,
    ):
        with pytest.raises(OutboundSafetyBlockedV2) as excinfo:
            execute_chunk_review_v2(
                chunk_content,
                run_id=content.run_id,
                head_sha=manifest.identity.head_sha,
                payload=payload,
                transport=transport,
            )

    assert len(captured_body) == 1
    assert needle.encode("utf-8") in captured_body[0]
    assert excinfo.value.report.verdict == "OUTBOUND_NOT_PROVEN_SAFE"
    assert excinfo.value.report.findings
    assert real_http_delegate_called is False


def test_normal_real_router_request_is_not_false_blocked(tmp_path: Path) -> None:
    # Uses the existing real extraction/payload fixture WITHOUT blinding any
    # sanitizer. The request includes the full output JSON Schema and Router
    # metadata, so this is stronger than a hand-built-body negative control.
    from tests.agent_review.test_review_transport_v2 import _build_repo_manifest_and_content

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    chunk_content = content.chunks[0]
    payload = payload_by_chunk_id[chunk_content.chunk_id]
    reports = []

    def _inspect_then_stop(http_request, timeout_seconds):
        del timeout_seconds
        body = http_request.data
        assert isinstance(body, bytes)
        reports.append(inspect_outbound_body_v2(body))
        raise urllib.error.URLError("fixture stop after pre-http inspection")

    transport = agent_router_transport_v2(
        base_url="https://router.example/",
        api_key="fixture-api-key",
        model="review:code",
    )
    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_inspect_then_stop,
    ):
        outcome = execute_chunk_review_v2(
            chunk_content,
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"  # stopped only at mocked HTTP boundary
    assert len(reports) == 1
    assert reports[0].verdict == "OUTBOUND_SAFE", reports[0].findings


@pytest.mark.parametrize(
    "source_path",
    [
        "app/agent_review/manifest_v2.py",
        "app/agent_review/run_assembly_v2.py",
        "app/agent_review/semantic_grouping_policy_v2.py",
        "app/agent_review/review_readiness_emission_v2.py",
    ],
)
def test_independent_oracle_does_not_false_block_representative_real_source(
    source_path: str,
) -> None:
    source = Path(source_path).read_text(encoding="utf-8")
    # Put real source in the same structural location where reviewed material
    # travels: a user-message content string inside a Router JSON body.
    nested = json.dumps(
        {"chunk_content": {"fragments": [{"content": source}]}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    body = json.dumps(
        {
            "model": "review:code",
            "messages": [
                {"role": "system", "content": "fixture"},
                {"role": "user", "content": nested},
            ],
            "metadata": {
                "request_sha256": "0" * 64,
                "payload_sha256": "1" * 64,
                "content_sha256": "2" * 64,
                "head_sha": "3" * 40,
            },
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    report = inspect_outbound_body_v2(body)
    assert report.verdict == "OUTBOUND_SAFE", (source_path, report.findings)
