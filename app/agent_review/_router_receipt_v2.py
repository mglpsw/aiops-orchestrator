"""Private Agent Router receipt-v2 consumer for AgentReview v2.

The Router owns the receipt grammar and the two public canonicalizations.
This module is deliberately a consumer, not a second receipt producer: it
accepts the Router's additive receipt, verifies it against the exact messages
and assistant content carried by the HTTP exchange, and releases only a sealed
``ChunkReviewResultV2``.  Raw Router output never reaches ``consumer_v2``.

Authority snapshot: mglpsw/agent-router-api
``80e921dfc28436bd4fed8a4e1fa72ffaa168d10c``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agent_review.contracts_v2 import ChunkReviewResultV2
from app.agent_review.review_transport_contract_v2 import ChunkReviewRequestV2

ROUTER_RECEIPT_INVALID_REASON_V2 = "router_receipt_invalid"
ROUTER_INPUT_MISMATCH_REASON_V2 = "router_input_mismatch"
ROUTER_OUTPUT_MISMATCH_REASON_V2 = "router_output_mismatch"
ROUTER_CALLER_BINDING_MISMATCH_REASON_V2 = "router_caller_binding_mismatch"
ROUTER_REQUESTED_MODEL_MISMATCH_REASON_V2 = "router_requested_model_mismatch"
ROUTER_FINISH_REASON_INCONCLUSIVE_REASON_V2 = "router_finish_reason_inconclusive"
ROUTER_RESULT_INVALID_REASON_V2 = "router_result_invalid"

_EXCHANGE_SENTINEL = object()
_VERIFIED_RESULT_SENTINEL = object()
_SAFE_MODEL_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:/")


class RouterReceiptError(ValueError):
    """Fail-closed receipt rejection carrying a content-free reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _StrictReceiptModel(BaseModel):
    # This mirrors the Router authority.  In particular, defaults are not
    # revalidated: omission-only v2 slots use an unvalidated None default,
    # while an explicit JSON null is still rejected by the non-optional type.
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
        strict=True,
    )


class _ReceiptRequested(_StrictReceiptModel):
    model: str = Field(pattern=r"^review:[a-z0-9-]+$")


class _ReceiptReceivedInput(_StrictReceiptModel):
    canonicalization: Literal["openai-messages-json.v1"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


_ReceiptProvider = Literal["local", "gemini", "openai", "anthropic"]
_ReceiptFinishReason = Literal[
    "content_filter",
    "end_turn",
    "error",
    "function_call",
    "incomplete",
    "length",
    "max_tokens",
    "pause_turn",
    "refusal",
    "stop",
    "stop_sequence",
    "tool_calls",
    "tool_use",
    "unknown",
]
_ReceiptFailureCode = Literal[
    "auth_error",
    "context_limit",
    "invalid_parameters",
    "model_permission_denied",
    "model_unavailable",
    "provider_error",
    "provider_timeout",
    "provider_unavailable",
    "rate_limited",
]
_ReceiptLimitationCode = Literal[
    "model_revision_unobserved",
    "routing_policy_unobserved",
    "producer_revision_unobserved",
    "timing_unobserved",
    "transport_retry_unobserved",
    "usage_incomplete",
    "budget_unobserved",
    "coverage_incomplete",
]


class _ModelRevisionV1(_StrictReceiptModel):
    schema_: Literal["agent-router.model-revision.v1"] = Field(alias="schema")
    value: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source: Literal[
        "provider_response",
        "typed_model_catalog",
        "typed_routing_policy",
    ]


class _RoutingPolicyLoaderResultV1(_StrictReceiptModel):
    schema_: Literal["agent-router.routing-policy-loader-result.v1"] = Field(alias="schema")
    disposition: Literal[
        "loaded_repository",
        "loaded_configured",
        "safe_default_missing",
        "safe_default_invalid",
    ]
    source_kind: Literal["repository_file", "configured_file", "builtin_default"]
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    selected_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _RoutingPolicyBindingV1(_StrictReceiptModel):
    schema_: Literal["agent-router.routing-policy-binding.v1"] = Field(alias="schema")
    canonicalization: Literal["agent-router-routing-policy-json.v1"]
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    loader_result: _RoutingPolicyLoaderResultV1
    nominal_provider_order: tuple[_ReceiptProvider, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _validate_nominal_order(self) -> _RoutingPolicyBindingV1:
        if len(set(self.nominal_provider_order)) != len(self.nominal_provider_order):
            raise ValueError("nominal_provider_order must be unique")
        return self


class _ProducerV1(_StrictReceiptModel):
    schema_: Literal["agent-router.producer.v1"] = Field(alias="schema")
    service: Literal["agent-router-api"]
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class _ExecutionTimingV1(_StrictReceiptModel):
    schema_: Literal["agent-router.execution-timing.v1"] = Field(alias="schema")
    started_at: str = Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
    )
    completed_at: str = Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
    )
    duration_ms: int = Field(ge=0)
    duration_basis: Literal["monotonic.v1"]

    @field_validator("started_at", "completed_at")
    @classmethod
    def _validate_calendar_timestamp(cls, value: str) -> str:
        try:
            datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError:
            raise ValueError("timing values must be valid RFC3339 UTC instants") from None
        return value


class _TokenUsageCoverageV1(_StrictReceiptModel):
    adapter_invocations: int = Field(ge=1)
    observations: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_observations(self) -> _TokenUsageCoverageV1:
        if self.observations > self.adapter_invocations:
            raise ValueError("usage observations cannot exceed adapter invocations")
        return self


class _TokenUsageV1(_StrictReceiptModel):
    schema_: Literal["agent-router.token-usage.v1"] = Field(alias="schema")
    scope: Literal["attempt", "selected_attempt", "all_attempts"]
    source: Literal["provider_reported", "router_estimated"]
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    coverage: _TokenUsageCoverageV1

    @model_validator(mode="after")
    def _validate_total(self) -> _TokenUsageV1:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        return self


class _TokenBudgetV1(_StrictReceiptModel):
    schema_: Literal["agent-router.token-budget.v1"] = Field(alias="schema")
    scope: Literal["each_adapter_invocation", "receipt_execution"]
    source: Literal["router_config", "caller_bound", "typed_routing_policy"]
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)


class _InputCoverageV1(_StrictReceiptModel):
    schema_: Literal["agent-router.input-coverage.v1"] = Field(alias="schema")
    basis: Literal["router-review-plan.v1"]
    mode: Literal["single", "chunked"]
    chunk_count: int = Field(ge=1)
    truncated: bool


class _LimitationsV1(_StrictReceiptModel):
    schema_: Literal["agent-router.limitations.v1"] = Field(alias="schema")
    codes: tuple[_ReceiptLimitationCode, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _validate_unique_codes(self) -> _LimitationsV1:
        if len(set(self.codes)) != len(self.codes):
            raise ValueError("limitation codes must be unique")
        return self


class _RouteAttemptV2(_StrictReceiptModel):
    index: int = Field(ge=1, le=64)
    provider: _ReceiptProvider
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
    outcome: Literal["failed", "selected"]
    adapter_invocation_count: int = Field(ge=1, le=2_147_483_647)
    failure_code: _ReceiptFailureCode = Field(default_factory=lambda: None)
    transport_retry_count: int = Field(default_factory=lambda: None, ge=0, le=2_147_483_647)
    finish_reason: _ReceiptFinishReason = Field(default_factory=lambda: None)
    model_revision: _ModelRevisionV1 = Field(default_factory=lambda: None)
    usage: _TokenUsageV1 = Field(default_factory=lambda: None)

    @field_validator("model")
    @classmethod
    def _validate_public_model_identity(cls, value: str) -> str:
        if (
            not _SAFE_MODEL_IDENTIFIER_RE.fullmatch(value)
            or "://" in value
            or _WINDOWS_ABSOLUTE_PATH_RE.match(value) is not None
        ):
            raise ValueError("model must be a bounded public identifier")
        return value

    @model_validator(mode="after")
    def _validate_outcome_and_usage(self) -> _RouteAttemptV2:
        if self.outcome == "failed" and self.failure_code is None:
            raise ValueError("failed attempt requires failure_code")
        if self.outcome == "selected" and self.failure_code is not None:
            raise ValueError("selected attempt cannot have failure_code")
        if self.usage is not None:
            if self.usage.scope != "attempt":
                raise ValueError("attempt usage must have scope=attempt")
            if self.usage.coverage.adapter_invocations > self.adapter_invocation_count:
                raise ValueError("attempt usage coverage exceeds adapter invocations")
            if self.outcome == "failed" and (
                self.usage.coverage.adapter_invocations != self.adapter_invocation_count
                or self.usage.coverage.observations != self.adapter_invocation_count
            ):
                raise ValueError("failed-attempt usage requires complete coverage")
        return self


class _RouteTransitionV2(_StrictReceiptModel):
    from_attempt_index: int = Field(ge=1, le=64)
    to_attempt_index: int = Field(ge=1, le=64)
    kind: Literal["model_fallback", "provider_fallback"]
    reason_code: _ReceiptFailureCode


class _RoutingExecutionV2(_StrictReceiptModel):
    execution_path: Literal["legacy_cascade", "dynamic_cascade"]
    selected_attempt_index: int = Field(ge=1, le=64)
    attempts: tuple[_RouteAttemptV2, ...] = Field(min_length=1, max_length=64)
    transitions: tuple[_RouteTransitionV2, ...] = Field(max_length=63)

    @model_validator(mode="after")
    def _validate_route(self) -> _RoutingExecutionV2:
        if tuple(item.index for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("attempt indices must be contiguous and ordered")
        targets = tuple((item.provider, item.model) for item in self.attempts)
        if len(set(targets)) != len(targets):
            raise ValueError("a provider/model target cannot become a new attempt")
        selected = tuple(item.index for item in self.attempts if item.outcome == "selected")
        if selected != (self.selected_attempt_index,):
            raise ValueError("selected_attempt_index must identify the sole selected attempt")
        if self.attempts[-1].index != self.selected_attempt_index:
            raise ValueError("the selected attempt must be final")
        if len(self.transitions) != len(self.attempts) - 1:
            raise ValueError("each adjacent attempt requires one transition")
        for offset, transition in enumerate(self.transitions):
            previous = self.attempts[offset]
            following = self.attempts[offset + 1]
            if (
                transition.from_attempt_index != previous.index
                or transition.to_attempt_index != following.index
                or transition.reason_code != previous.failure_code
            ):
                raise ValueError("transition must bind adjacent attempts")
            expected_kind = (
                "model_fallback"
                if previous.provider == following.provider
                else "provider_fallback"
            )
            if transition.kind != expected_kind:
                raise ValueError("transition kind does not match route targets")
        return self


class _ReceiptExecution(_StrictReceiptModel):
    status: Literal["completed"]
    finish_reason: str = Field(min_length=1)


class _ReceiptReturnedOutput(_StrictReceiptModel):
    canonicalization: Literal["assistant-content-utf8.v1"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CallerDeclaredMetadata(_StrictReceiptModel):
    chunk_id: str = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    run_id: str = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    head_sha: str = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    content_sha256: str = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class _InferenceReceiptV2(_StrictReceiptModel):
    schema_: Literal["agent-router.inference-receipt.v2"] = Field(alias="schema")
    router_request_id: str = Field(pattern=r"^rreq_[0-9a-f]{32}$")
    requested: _ReceiptRequested
    received_input: _ReceiptReceivedInput
    execution: _ReceiptExecution
    returned_output: _ReceiptReturnedOutput
    caller_declared_metadata: _CallerDeclaredMetadata
    routing_execution: _RoutingExecutionV2

    routing_policy: _RoutingPolicyBindingV1 = Field(default_factory=lambda: None)
    producer: _ProducerV1 = Field(default_factory=lambda: None)
    timing: _ExecutionTimingV1 = Field(default_factory=lambda: None)
    usage: _TokenUsageV1 = Field(default_factory=lambda: None)
    budget: _TokenBudgetV1 = Field(default_factory=lambda: None)
    coverage: _InputCoverageV1 = Field(default_factory=lambda: None)
    limitations: _LimitationsV1 = Field(default_factory=lambda: None)

    @model_validator(mode="after")
    def _validate_receipt_usage(self) -> _InferenceReceiptV2:
        if self.usage is None:
            return self
        if self.usage.scope == "attempt":
            raise ValueError("receipt-level usage cannot have scope=attempt")
        if self.usage.scope == "selected_attempt":
            selected = self.routing_execution.attempts[
                self.routing_execution.selected_attempt_index - 1
            ]
            if self.usage.coverage.adapter_invocations > selected.adapter_invocation_count:
                raise ValueError("selected-attempt usage exceeds selected attempt")
            return self
        required = sum(
            item.adapter_invocation_count for item in self.routing_execution.attempts
        )
        if (
            self.usage.coverage.adapter_invocations != required
            or self.usage.coverage.observations != required
        ):
            raise ValueError("receipt usage requires complete mechanical coverage")
        return self


class _RouterTransportResponseV2:
    """Private single-exchange carrier: exact sent messages + one response."""

    __slots__ = ("_sent_messages", "_response", "_requested_model")

    def __init__(
        self,
        *,
        sentinel: object,
        sent_messages: list[dict[str, Any]],
        response: Mapping[str, Any],
        requested_model: str,
    ) -> None:
        if sentinel is not _EXCHANGE_SENTINEL:
            raise TypeError("Router transport responses are transport-owned")
        self._sent_messages = sent_messages
        self._response = response
        self._requested_model = requested_model


def _make_router_transport_response_v2(
    *,
    sent_messages: list[dict[str, Any]],
    response: Mapping[str, Any],
    requested_model: str,
) -> _RouterTransportResponseV2:
    return _RouterTransportResponseV2(
        sentinel=_EXCHANGE_SENTINEL,
        sent_messages=sent_messages,
        response=response,
        requested_model=requested_model,
    )


class _VerifiedRouterResultV2:
    """Sealed result released only after every receipt-v2 proof succeeds."""

    __slots__ = ("_request", "_result")

    def __init__(
        self,
        *,
        sentinel: object,
        request: ChunkReviewRequestV2,
        result: ChunkReviewResultV2,
    ) -> None:
        if sentinel is not _VERIFIED_RESULT_SENTINEL:
            raise TypeError("Router results are receipt-verifier-owned")
        self._request = request
        self._result = result


def _canonical_messages_bytes_v2(messages: object) -> bytes:
    return json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _extract_router_response_v2(
    response: Mapping[str, Any],
) -> tuple[str, str, Mapping[str, Any]]:
    choices = response.get("choices")
    receipt = response.get("inference_receipt")
    if (
        response.get("object") != "chat.completion"
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(receipt, Mapping)
    ):
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    choice = choices[0]
    if not isinstance(choice, Mapping) or choice.get("index") != 0:
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    content = message.get("content")
    finish_reason = choice.get("finish_reason")
    if not isinstance(content, str) or not isinstance(finish_reason, str):
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    return content, finish_reason, receipt


def _expected_caller_metadata_v2(request: ChunkReviewRequestV2) -> dict[str, str]:
    return {
        "chunk_id": request.chunk_id,
        "run_id": request.run_id,
        "payload_sha256": request.payload_sha256,
        "head_sha": request.head_sha,
        "content_sha256": request.content_sha256,
        "request_sha256": request.request_sha256,
    }


def _verify_router_transport_response_v2(
    exchange: _RouterTransportResponseV2,
    *,
    request: ChunkReviewRequestV2,
) -> _VerifiedRouterResultV2:
    """Verify Router input, declarations, execution, output, then domain JSON."""

    if not isinstance(exchange, _RouterTransportResponseV2):
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    if not isinstance(exchange._response, Mapping):
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2)
    assistant_content, choice_finish_reason, raw_receipt = _extract_router_response_v2(
        exchange._response
    )
    try:
        receipt = _InferenceReceiptV2.model_validate_json(
            json.dumps(raw_receipt, ensure_ascii=False, allow_nan=False),
            strict=True,
        )
    except (ValidationError, TypeError, ValueError, UnicodeError) as exc:
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2) from exc

    if receipt.requested.model != exchange._requested_model:
        raise RouterReceiptError(ROUTER_REQUESTED_MODEL_MISMATCH_REASON_V2)
    try:
        sent_sha256 = hashlib.sha256(
            _canonical_messages_bytes_v2(exchange._sent_messages)
        ).hexdigest()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RouterReceiptError(ROUTER_RECEIPT_INVALID_REASON_V2) from exc
    if receipt.received_input.sha256 != sent_sha256:
        raise RouterReceiptError(ROUTER_INPUT_MISMATCH_REASON_V2)
    caller_metadata = receipt.caller_declared_metadata.model_dump(exclude_none=True)
    if caller_metadata != _expected_caller_metadata_v2(request):
        raise RouterReceiptError(ROUTER_CALLER_BINDING_MISMATCH_REASON_V2)
    if (
        choice_finish_reason != "stop"
        or receipt.execution.finish_reason != choice_finish_reason
    ):
        raise RouterReceiptError(ROUTER_FINISH_REASON_INCONCLUSIVE_REASON_V2)
    output_sha256 = hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()
    if receipt.returned_output.sha256 != output_sha256:
        raise RouterReceiptError(ROUTER_OUTPUT_MISMATCH_REASON_V2)

    try:
        result = ChunkReviewResultV2.model_validate_json(assistant_content, strict=True)
    except (ValidationError, TypeError, ValueError, UnicodeError) as exc:
        raise RouterReceiptError(ROUTER_RESULT_INVALID_REASON_V2) from exc
    fresh_request = ChunkReviewRequestV2.model_validate_json(
        request.model_dump_json(), strict=True
    )
    return _VerifiedRouterResultV2(
        sentinel=_VERIFIED_RESULT_SENTINEL,
        request=fresh_request,
        result=result,
    )
