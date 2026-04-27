"""LEGACY / NOT USED BY AIOPS RUNNER V1.

Do not wire this into /v1/aiops/actions/run. The official runner is
app/agent_router/services/action_runner.py.

Docker adapter for container inspection and management.

Provides read-heavy operations by default. Destructive operations require approval.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from app.adapters.base import BaseExecutorAdapter
from app.models.schemas import ProviderStatus
from app.utils.logging import get_logger
from app.utils.secrets import mask_secrets, truncate

logger = get_logger("adapters.docker")

# Safe read-only operations
SAFE_DOCKER_COMMANDS = frozenset({
    "ps", "inspect", "logs", "stats", "top", "port",
    "images", "volume ls", "network ls", "compose ps",
    "compose logs", "version", "info",
})


class DockerAdapter(BaseExecutorAdapter):
    name = "docker"

    def __init__(self):
        self.enabled = True

    def _is_safe_command(self, command: str) -> bool:
        """Check if a docker subcommand is in the safe list."""
        parts = command.strip().split()
        if not parts:
            return False
        # Check first subcommand and first two words for compose commands
        if parts[0] in SAFE_DOCKER_COMMANDS:
            return True
        if len(parts) >= 2 and f"{parts[0]} {parts[1]}" in SAFE_DOCKER_COMMANDS:
            return True
        return False

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
        dry_run: bool = False,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Prefix with docker if not already
        if not command.startswith("docker"):
            docker_cmd = f"docker {command}"
        else:
            docker_cmd = command

        # Extract the subcommand part for safety check
        sub = docker_cmd.replace("docker ", "", 1).strip()

        if dry_run:
            safe = self._is_safe_command(sub)
            return {
                "stdout": f"[DRY RUN] Would execute: {mask_secrets(docker_cmd)} (safe={safe})",
                "stderr": "",
                "exit_code": 0,
                "duration_ms": 0,
                "dry_run": True,
                "command": mask_secrets(docker_cmd),
            }

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            duration = (time.monotonic() - start) * 1000

            return {
                "stdout": mask_secrets(truncate(stdout_bytes.decode("utf-8", errors="replace"))),
                "stderr": mask_secrets(truncate(stderr_bytes.decode("utf-8", errors="replace"))),
                "exit_code": proc.returncode,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(docker_cmd),
            }
        except asyncio.TimeoutError:
            duration = (time.monotonic() - start) * 1000
            return {
                "stdout": "",
                "stderr": f"Docker command timed out after {timeout}s",
                "exit_code": -1,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(docker_cmd),
            }
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -2,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(docker_cmd),
            }

    async def health_check(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            result = await self.execute("docker version --format '{{.Server.Version}}'", timeout=5)
            latency = (time.monotonic() - start) * 1000
            healthy = result["exit_code"] == 0
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=healthy,
                last_check=datetime.utcnow(),
                latency_ms=latency,
                error=result["stderr"] if not healthy else None,
            )
        except Exception as e:
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=False,
                last_check=datetime.utcnow(),
                error=str(e),
            )
