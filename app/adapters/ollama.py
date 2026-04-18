"""Ollama local LLM adapter."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.adapters.base import BaseLLMAdapter
from app.core.config import get_settings
from app.models.schemas import ProviderStatus
from app.utils.logging import get_logger

logger = get_logger("adapters.ollama")


class OllamaAdapter(BaseLLMAdapter):
    name = "ollama"

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.default_model = settings.ollama_default_model
        self.enabled = True
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
        model = model or self.default_model
        start = time.monotonic()
        try:
            payload: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
            if system:
                payload["system"] = system

            resp = await self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.monotonic() - start) * 1000

            return {
                "text": data.get("response", ""),
                "model": model,
                "provider": self.name,
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                },
                "latency_ms": latency,
            }
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.error("Ollama generate failed: %s", e, extra={"provider": self.name})
            raise

    async def health_check(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags", timeout=5.0)
            resp.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=True,
                last_check=datetime.utcnow(),
                latency_ms=latency,
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
