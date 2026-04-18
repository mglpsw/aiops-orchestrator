"""OpenAI-compatible API adapter. Works with OpenAI, local endpoints, or any compatible API."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.adapters.base import BaseLLMAdapter
from app.core.config import get_settings
from app.models.schemas import ProviderStatus
from app.utils.logging import get_logger

logger = get_logger("adapters.openai_compatible")


class OpenAICompatibleAdapter(BaseLLMAdapter):
    name = "gpt"

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.base_url = settings.openai_base_url.rstrip("/")
        self.model = settings.gpt_model
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
            raise RuntimeError("OpenAI API key not configured")

        model = model or self.model
        start = time.monotonic()
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_completion_tokens": max_tokens,  # gpt-5 compat; older models also accept this
            }

            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.monotonic() - start) * 1000

            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return {
                "text": text,
                "model": model,
                "provider": self.name,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                },
                "latency_ms": latency,
            }
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.error("OpenAI generate failed: %s", e, extra={"provider": self.name})
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
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = await self._client.get(
                f"{self.base_url}/models",
                headers=headers,
                timeout=10.0,
            )
            latency = (time.monotonic() - start) * 1000
            healthy = resp.status_code == 200
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=healthy,
                last_check=datetime.utcnow(),
                latency_ms=latency,
                error=None if healthy else f"HTTP {resp.status_code}",
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
