"""Transport wiring and end-to-end synthesis for AgentReview v2 (#200-C,
third and final slice of #200, child of the distribution epic #199).

Wires together, for the first time, the full chain the #199 execution
plan's D2/D-series fixed as the one legal order of authority:

```text
ReviewContentV2 (#200-A/#200-B)
  -> ChunkReviewRequestV2                 (review_transport_contract_v2, #200-A)
  -> transport                            (THIS module)
  -> ChunkReviewTransportEnvelopeV1        (review_transport_contract_v2, #200-A)
  -> verify_transport_echo_v1              (review_transport_contract_v2, #200-A)
  -> consumer_v2.bind_chunk_response_v2    (already existed)
  -> parser_v2.parse_bound_chunk_response_v2
  -> synthesis_v2.synthesize_chunk_results_v2
  -> lifecycle_v2 (via synthesis) / readiness_decision_v2.compute_readiness_decision_v2
  -> review_readiness_emission_v2.emit_review_readiness_v2
```

No finding is EVER reachable before the echo check and the binding both
succeed -- ``execute_chunk_review_v2`` is the single choke point every
caller in this module goes through, and it never returns a
``ParsedChunkResultV2`` without both having passed.

## Transport is an injected callable, not a hardcoded HTTP client

``ChunkReviewTransportV2`` is a ``Protocol``: given a request and the
content it describes, return a ``ChunkReviewTransportEnvelopeV1`` or raise
``ChunkTransportError``. Two implementations ship in this module:

- ``offline_file_transport_v2`` -- reads a pre-placed JSON file per
  ``chunk_id`` from a directory. This is the DEFAULT transport in tests
  and the one dual-target synthetic E2E in this slice actually exercises;
- ``agent_router_transport_v2`` -- the real Agent Router, gated by an
  explicit, non-empty ``api_key`` (mirrors ``scripts/github_agent_review.
  py``'s own ``AgentRouterDisabledError`` discipline) and locked to
  EXACTLY ``{base_url}/v1/chat/completions`` -- no provider-direct call,
  no second endpoint. Covered by unit tests against a mocked HTTP layer
  only; this slice does not make a live network call to any Router.

## Failure taxonomy: never a silent approval

A transport failure, timeout, malformed response, or a tampered echo
degrades EXACTLY that chunk to ``manual_required`` -- ``execute_chunk_
review_v2`` returns ``None`` for it, never raises, and never fabricates a
``ParsedChunkResultV2``. ``run_synthetic_review_v2`` (the orchestrator)
then hands ``synthesis_v2.synthesize_chunk_results_v2`` only the chunks
that DID bind; that function's own documented behavior -- "an incomplete
run still produces a valid result whose coverage report honestly reports
what is missing" -- carries the missing chunk through to
``readiness_decision_v2`` as a real coverage gap, which its own precedence
already refuses to call ``ready``. This module adds no separate
"is everything covered" check of its own; the existing coverage/readiness
authority is reused exactly as-is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from app.agent_review.chunk_result_scope_v2 import ChunkResultScopeError
from app.agent_review.consumer_v2 import ResponseBindingError, bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    PullRequestStateV2,
    RequiredCheckResultV2,
    ReviewReadinessV2,
    TargetPoliciesV2,
)
from app.agent_review.lifecycle_v2 import FindingLifecycleRecordV2
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.parser_v2 import ParsedChunkResultV2, parse_bound_chunk_response_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.review_content_v2 import ChunkContentV2, ReviewContentV2
from app.agent_review.review_readiness_emission_v2 import emit_review_readiness_v2
from app.agent_review.review_transport_contract_v2 import (
    ChunkReviewRequestV2,
    ChunkReviewTransportEnvelopeV1,
    TransportEchoError,
    compute_request_sha256_v2,
    verify_transport_echo_v1,
)
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

CHUNK_TRANSPORT_FAILURE_REASON_V2 = "transport_failure"
CHUNK_TRANSPORT_TIMEOUT_REASON_V2 = "transport_timeout"
CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2 = "transport_unavailable"
CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2 = "transport_invalid_response"
ROUTER_DISABLED_REASON_V2 = "router_disabled"

AGENT_ROUTER_CHAT_COMPLETIONS_PATH_V2 = "/v1/chat/completions"


class ChunkTransportError(ValueError):
    """Raised by a ``ChunkReviewTransportV2`` implementation. Carries a
    stable ``reason_code`` only -- never request or response content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ChunkReviewTransportV2(Protocol):
    def __call__(
        self, request: ChunkReviewRequestV2, chunk_content: ChunkContentV2
    ) -> ChunkReviewTransportEnvelopeV1: ...


def build_chunk_review_request_v2(
    chunk_content: ChunkContentV2, *, run_id: str, head_sha: str
) -> ChunkReviewRequestV2:
    """The identity of one outbound request -- never the raw reviewable
    content itself (that stays in ``chunk_content``, resolved by
    ``content_sha256`` reference only, per ``ChunkReviewRequestV2``'s own
    docstring)."""

    request_sha256 = compute_request_sha256_v2(
        run_id=run_id, chunk_id=chunk_content.chunk_id, head_sha=head_sha,
        payload_sha256=chunk_content.payload_sha256, content_sha256=chunk_content.content_sha256,
    )
    return ChunkReviewRequestV2(
        run_id=run_id, chunk_id=chunk_content.chunk_id, head_sha=head_sha,
        payload_sha256=chunk_content.payload_sha256, content_sha256=chunk_content.content_sha256,
        request_sha256=request_sha256,
    )


@dataclass(frozen=True)
class ChunkReviewOutcomeV2:
    """One chunk's outcome: EITHER a bound, parsed result, OR a typed
    reason it has none -- never both, never neither."""

    chunk_id: str
    state: Literal["bound", "manual_required"]
    result: ParsedChunkResultV2 | None
    reason_code: str | None


def execute_chunk_review_v2(
    chunk_content: ChunkContentV2,
    *,
    run_id: str,
    head_sha: str,
    payload: ChunkPayloadV2,
    transport: ChunkReviewTransportV2,
) -> ChunkReviewOutcomeV2:
    """The single choke point: no finding is reachable from this chunk
    unless the transport succeeds, the echo verifies, AND the response
    binds -- in that exact order, matching the #199 execution plan's fixed
    order of authority. Never raises; every failure mode becomes a typed
    ``ChunkReviewOutcomeV2(state="manual_required", ...)``."""

    request = build_chunk_review_request_v2(chunk_content, run_id=run_id, head_sha=head_sha)

    try:
        envelope = transport(request, chunk_content)
    except ChunkTransportError as exc:
        return ChunkReviewOutcomeV2(chunk_content.chunk_id, "manual_required", None, exc.reason_code)

    try:
        response = verify_transport_echo_v1(envelope, request=request)
    except TransportEchoError as exc:
        return ChunkReviewOutcomeV2(chunk_content.chunk_id, "manual_required", None, exc.reason_code)

    try:
        bound = bind_chunk_response_v2(envelope=response, payload=payload)
    except ResponseBindingError as exc:
        return ChunkReviewOutcomeV2(chunk_content.chunk_id, "manual_required", None, exc.reason_code)

    parsed = parse_bound_chunk_response_v2(bound)
    return ChunkReviewOutcomeV2(chunk_content.chunk_id, "bound", parsed, None)


@dataclass(frozen=True)
class SyntheticReviewOutcomeV2:
    readiness: ReviewReadinessV2
    chunk_outcomes: tuple[ChunkReviewOutcomeV2, ...]


def run_synthetic_review_v2(
    *,
    content: ReviewContentV2,
    manifest: ManifestV2,
    payload_by_chunk_id: dict[str, ChunkPayloadV2],
    transport: ChunkReviewTransportV2,
    policies: TargetPoliciesV2,
    pr_state: PullRequestStateV2,
    checks: Sequence[RequiredCheckResultV2] = (),
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> SyntheticReviewOutcomeV2:
    """The full E2E: every chunk in ``content`` goes through ``execute_
    chunk_review_v2``; only the ones that bind feed ``synthesize_chunk_
    results_v2``; the result feeds ``compute_readiness_decision_v2``; the
    decision is emitted as a real ``ReviewReadinessV2`` via ``emit_review_
    readiness_v2``. ``identity`` and ``evaluated_identity`` are the same
    object (``manifest.identity``) -- this function does not itself decide
    staleness; a caller with an independent staleness determination should
    call the lower-level pieces directly instead, exactly as ``review_
    readiness_emission_v2``'s own docstring documents.
    """

    outcomes = tuple(
        execute_chunk_review_v2(
            chunk_content, run_id=content.run_id, head_sha=manifest.identity.head_sha,
            payload=payload_by_chunk_id[chunk_content.chunk_id], transport=transport,
        )
        for chunk_content in content.chunks
    )
    bound_results = tuple(outcome.result for outcome in outcomes if outcome.result is not None)

    try:
        synthesis = synthesize_chunk_results_v2(
            manifest=manifest, chunk_results=bound_results,
            evaluated_head_sha=manifest.identity.head_sha, prior_lifecycle=prior_lifecycle,
        )
    except ChunkResultScopeError:
        raise

    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=policies)
    readiness = emit_review_readiness_v2(
        decision=decision, findings=synthesis.findings, identity=manifest.identity,
        evaluated_identity=manifest.identity, pr_state=pr_state, checks=checks,
    )
    return SyntheticReviewOutcomeV2(readiness=readiness, chunk_outcomes=outcomes)


# -- offline file transport (default in tests) -------------------------------


def offline_file_transport_v2(responses_dir: Path) -> ChunkReviewTransportV2:
    """A transport that reads one pre-placed
    ``{responses_dir}/{chunk_id}.json`` file per chunk -- a full
    ``ChunkReviewTransportEnvelopeV1`` document. Missing file or invalid
    JSON/schema is ``transport_failure``/``transport_invalid_response``,
    never a crash, never an approval."""

    def _transport(request: ChunkReviewRequestV2, chunk_content: ChunkContentV2) -> ChunkReviewTransportEnvelopeV1:
        response_path = responses_dir / f"{request.chunk_id}.json"
        try:
            raw_text = response_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ChunkTransportError(CHUNK_TRANSPORT_FAILURE_REASON_V2) from exc
        try:
            # model_validate_json, not json.loads + model_validate: a JSON
            # array must decode into this model's tuple-typed fields (e.g.
            # ChunkCoverageV2's), which strict Python-object validation
            # rejects for a plain list -- consumer_v2.bind_chunk_response_v2
            # draws the exact same JSON-text-vs-Python-object line for the
            # identical reason.
            return ChunkReviewTransportEnvelopeV1.model_validate_json(raw_text)
        except Exception as exc:  # pydantic.ValidationError, malformed JSON, ...
            raise ChunkTransportError(CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2) from exc

    return _transport


# -- real Agent Router transport (explicit flag only, never provider-direct) --


def agent_router_transport_v2(
    *, base_url: str, api_key: str, model: str, timeout_seconds: float = 60.0
) -> ChunkReviewTransportV2:
    """The real Agent Router. Locked to EXACTLY
    ``{base_url}/v1/chat/completions`` -- no provider-direct call, no
    second endpoint (mirrors ``scripts/github_agent_review.py``'s own
    ``call_agent_router_review`` discipline). Raises ``ChunkTransportError
    (ROUTER_DISABLED_REASON_V2)`` immediately, before any network attempt,
    if ``api_key`` is empty -- there is no ambient/implicit enablement
    path. A caller must pass a real key to get a real transport; there is
    no other way to reach the network from this function."""

    if not api_key:
        raise ChunkTransportError(ROUTER_DISABLED_REASON_V2)

    endpoint = base_url.rstrip("/") + AGENT_ROUTER_CHAT_COMPLETIONS_PATH_V2

    def _transport(request: ChunkReviewRequestV2, chunk_content: ChunkContentV2) -> ChunkReviewTransportEnvelopeV1:
        import urllib.error
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "aiops_review_request": request.model_dump(mode="json"),
                "aiops_review_content": chunk_content.model_dump(mode="json"),
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise ChunkTransportError(CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2) from exc
            raise ChunkTransportError(CHUNK_TRANSPORT_FAILURE_REASON_V2) from exc
        except TimeoutError as exc:
            raise ChunkTransportError(CHUNK_TRANSPORT_TIMEOUT_REASON_V2) from exc
        except urllib.error.URLError as exc:
            raise ChunkTransportError(CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2) from exc

        try:
            return ChunkReviewTransportEnvelopeV1.model_validate_json(raw_text)
        except Exception as exc:  # pydantic.ValidationError, malformed JSON, ...
            raise ChunkTransportError(CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2) from exc

    return _transport
