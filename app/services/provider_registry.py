"""Registro de todos os providers de LLM e executores, com lógica de roteamento."""

from __future__ import annotations

from typing import Any

from app.adapters.base import BaseLLMAdapter, BaseExecutorAdapter
from app.adapters.ollama import OllamaAdapter
from app.adapters.claude import ClaudeAdapter
from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.adapters.codex import CodexAdapter
from app.adapters.executor_local import LocalExecutorAdapter
from app.adapters.executor_ssh import SSHExecutorAdapter
from app.adapters.docker import DockerAdapter
from app.core.config import get_settings, get_routes_config
from app.models.schemas import ProviderRole, ProviderStatus
from app.utils.logging import get_logger

logger = get_logger("services.provider_registry")


class ProviderRegistry:
    """Gerencia todos os providers e roteia requisições para o correto."""

    def __init__(self):
        self.llm_providers: dict[str, BaseLLMAdapter] = {}
        self.executor_providers: dict[str, BaseExecutorAdapter] = {}
        self._routes: dict[str, str] = {}
        self._fallbacks: dict[str, str] = {}

    def initialize(self):
        """Inicializa todos os providers a partir da configuração."""
        settings = get_settings()

        # Providers de LLM
        self.llm_providers["ollama"] = OllamaAdapter()
        self.llm_providers["claude"] = ClaudeAdapter()
        self.llm_providers["gpt"] = OpenAICompatibleAdapter()
        self.llm_providers["codex"] = CodexAdapter()

        # Providers de execuão (executores)
        self.llm_providers  # LLM providers are separate from executors
        self.executor_providers["local"] = LocalExecutorAdapter()
        self.executor_providers["ssh"] = SSHExecutorAdapter()
        self.executor_providers["docker"] = DockerAdapter()

        # Rotas padrão definidas nas configurações
        self._routes = {
            "classify": settings.classifier_default,
            "plan": settings.planner_default,
            "review": settings.planner_default,
            "execute": settings.executor_default,
            "summarize": settings.classifier_default,
        }
        self._fallbacks = {
            "classify": settings.planner_fallback,
            "plan": settings.planner_fallback,
            "review": settings.planner_fallback,
            "summarize": settings.planner_fallback,
        }

        # Override with routes config if present
        routes_config = get_routes_config()
        if routes_config:
            for role, provider in routes_config.get("routes", {}).items():
                self._routes[role] = provider
            for role, provider in routes_config.get("fallbacks", {}).items():
                self._fallbacks[role] = provider

        enabled_llm = [n for n, p in self.llm_providers.items() if p.enabled]
        enabled_exec = [n for n, p in self.executor_providers.items() if p.enabled]
        logger.info("Providers initialized: LLM=%s, Executors=%s", enabled_llm, enabled_exec)

    def get_llm(self, role: str | None = None, provider_name: str | None = None) -> BaseLLMAdapter:
        """Get LLM provider by explicit name or by role routing."""
        name = provider_name or self._routes.get(role, "ollama")
        provider = self.llm_providers.get(name)
        if provider and provider.enabled:
            return provider

        # Try fallback
        fallback_name = self._fallbacks.get(role, "ollama")
        fallback = self.llm_providers.get(fallback_name)
        if fallback and fallback.enabled:
            logger.warning("Provider %s unavailable, falling back to %s", name, fallback_name)
            return fallback

        # Last resort: any enabled provider
        for p in self.llm_providers.values():
            if p.enabled:
                logger.warning("Using fallback provider %s", p.name)
                return p

        raise RuntimeError("No LLM providers available")

    def get_executor(self, name: str | None = None) -> BaseExecutorAdapter:
        """Get executor provider by name."""
        name = name or self._routes.get("execute", "local")
        provider = self.executor_providers.get(name)
        if provider and provider.enabled:
            return provider
        # Fallback to local
        return self.executor_providers["local"]

    async def check_all_health(self) -> list[ProviderStatus]:
        """Check health of all providers."""
        statuses = []
        for provider in self.llm_providers.values():
            status = await provider.health_check()
            statuses.append(status)
        for provider in self.executor_providers.values():
            status = await provider.health_check()
            statuses.append(status)
        return statuses


# Singleton
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.initialize()
    return _registry
