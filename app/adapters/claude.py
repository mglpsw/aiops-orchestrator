"""Claude (Anthropic) LLM adapter."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.adapters.base import BaseLLMAdapter
from app.core.config import get_settings
from app.models.schemas import ProviderStatus
from app.utils.logging import get_logger

logger = get_logger("adapters.claude")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeAdapter(BaseLLMAdapter):
    name = "claude"

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.claude_api_key
        self.model = settings.claude_model
        self.enabled = bool(self.api_key)
        self._client = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Claude API key not configured")

        model = model or self.model
        start = time.monotonic()
        try:
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }
            if system:
                payload["system"] = system

            resp = await self._client.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.monotonic() - start) * 1000

            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            usage = data.get("usage", {})
            return {
                "text": text,
                "model": model,
                "provider": self.name,
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                },
                "latency_ms": latency,
            }
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.error("Claude generate failed: %s", e, extra={"provider": self.name})
            raise

    async def health_check(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(
                name=self.name,
                enabled=False,
                healthy=False,
                last_check=datetime.utcnow(),
                error="API key not configured",
            )
        start = time.monotonic()
        try:
            # Light check: just verify key format and connectivity
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            resp = await self._client.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json={
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                timeout=10.0,
            )
            latency = (time.monotonic() - start) * 1000
            # 200 = ok, 401 = bad key, other = issue
            healthy = resp.status_code == 200
            error = None if healthy else f"HTTP {resp.status_code}"
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=healthy,
                last_check=datetime.utcnow(),
                latency_ms=latency,
                error=error,
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=False,
                last_check=datetime.utcnow(),
                latency_ms=latency,
                error=str(e),
            )
