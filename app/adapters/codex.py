"""Codex adapter — uses OpenAI API with codex-mini-latest, optimised for code generation."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.core.config import get_settings
from app.models.schemas import ProviderStatus
from app.utils.logging import get_logger

logger = get_logger("adapters.codex")

_CODEX_SYSTEM = (
    "You are Codex, a code-generation and infrastructure-automation agent. "
    "Return precise, executable code, scripts, or configuration with minimal prose. "
    "For shell commands, wrap in ```bash``` blocks. "
    "Do NOT propose destructive, irreversible operations unless explicitly requested and confirmed."
)


class CodexAdapter(OpenAICompatibleAdapter):
    """OpenAI Codex — codex-mini-latest, focused on code and infra tasks."""

    name = "codex"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        # Override model to codex-mini
        self.model = settings.codex_model
        self.enabled = bool(self.api_key)

    async def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,      # lower temp → more deterministic code
        max_tokens: int = 4096,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await super().generate(
            prompt=prompt,
            system=system or _CODEX_SYSTEM,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model or self.model,
            **kwargs,
        )

    async def health_check(self) -> ProviderStatus:
        result = await super().health_check()
        # Return with our own name so the registry shows "codex" not "openai"
        return ProviderStatus(
            name=self.name,
            enabled=result.enabled,
            healthy=result.healthy,
            last_check=result.last_check,
            latency_ms=result.latency_ms,
            error=result.error,
        )
