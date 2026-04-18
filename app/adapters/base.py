"""Base interface for all LLM and execution providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from app.models.schemas import ProviderStatus


class BaseLLMAdapter(ABC):
    """Interface for LLM providers (Ollama, Claude, OpenAI-compatible)."""

    name: str = "base"
    enabled: bool = True

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate completion. Returns dict with keys: text, usage, model, latency_ms."""
        ...

    @abstractmethod
    async def health_check(self) -> ProviderStatus:
        """Check if provider is reachable and healthy."""
        ...

    async def classify_intent(self, message: str) -> dict[str, Any]:
        """Classify user intent and risk. Default uses generate with system prompt."""
        system = (
            "You are a task classifier for a homelab orchestrator. "
            "Given a user message, respond ONLY with valid JSON:\n"
            '{"intent": "<short_intent>", "category": "query|action|dangerous", '
            '"risk_level": "low|medium|high|critical", '
            '"summary": "<one_line_summary>", '
            '"affected_targets": ["<target1>"], '
            '"requires_execution": true/false}'
        )
        result = await self.generate(prompt=message, system=system, temperature=0.1)
        return result

    async def create_plan(self, message: str, context: str = "") -> dict[str, Any]:
        """Generate an execution plan. Default uses generate with system prompt."""
        system = (
            "You are an expert SRE/DevOps planner for a Proxmox homelab. "
            "Given a task, generate a structured execution plan as valid JSON:\n"
            '{"objective": "...", "context": "...", "assumptions": [...], '
            '"affected_targets": [...], '
            '"steps": [{"order": 1, "description": "...", "tool": "...", "args": {...}}], '
            '"dry_run_steps": [...], "validation_steps": [...], "rollback_steps": [...], '
            '"risk_level": "low|medium|high|critical", '
            '"requires_approval": true/false, '
            '"proposed_provider": "local|ssh|docker"}\n'
            "Be conservative. Prefer safe operations. Always include rollback steps."
        )
        prompt = f"Task: {message}"
        if context:
            prompt += f"\nContext: {context}"
        result = await self.generate(prompt=prompt, system=system, temperature=0.2)
        return result


class BaseExecutorAdapter(ABC):
    """Interface for execution providers (local shell, SSH, Docker, etc.)."""

    name: str = "base-executor"
    enabled: bool = True

    @abstractmethod
    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 120,
        dry_run: bool = False,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Execute a command/operation. Returns dict with keys:
        stdout, stderr, exit_code, duration_ms, dry_run
        """
        ...

    @abstractmethod
    async def health_check(self) -> ProviderStatus:
        """Check if executor is available."""
        ...
