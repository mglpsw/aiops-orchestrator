"""SSH executor adapter - prepared for remote command execution via SSH.

This adapter is configured but disabled by default.
It requires SSH keys to be set up and targets to be allowlisted in policies.
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

logger = get_logger("adapters.executor_ssh")


class SSHExecutorAdapter(BaseExecutorAdapter):
    name = "ssh"

    def __init__(self, default_timeout: int = 60):
        self.enabled = False  # Disabled by default for safety
        self.default_timeout = default_timeout
        self._allowed_hosts: set[str] = set()

    def configure(self, allowed_hosts: list[str], enabled: bool = False):
        """Configure SSH targets. Called during app startup from config."""
        self._allowed_hosts = set(allowed_hosts)
        self.enabled = enabled
        logger.info("SSH executor configured: enabled=%s, hosts=%s", enabled, allowed_hosts)

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        dry_run: bool = False,
        env: dict[str, str] | None = None,
        host: str | None = None,
        user: str = "root",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "stdout": "",
                "stderr": "SSH executor is disabled",
                "exit_code": -3,
                "duration_ms": 0,
                "dry_run": False,
                "command": mask_secrets(command),
            }

        if not host:
            return {
                "stdout": "",
                "stderr": "No target host specified",
                "exit_code": -3,
                "duration_ms": 0,
                "dry_run": False,
                "command": mask_secrets(command),
            }

        if host not in self._allowed_hosts:
            logger.warning("SSH to non-allowlisted host blocked: %s", host)
            return {
                "stdout": "",
                "stderr": f"Host {host} not in SSH allowlist",
                "exit_code": -3,
                "duration_ms": 0,
                "dry_run": False,
                "command": mask_secrets(command),
            }

        timeout = timeout or self.default_timeout

        if dry_run:
            return {
                "stdout": f"[DRY RUN] Would execute via SSH on {user}@{host}: {mask_secrets(command)}",
                "stderr": "",
                "exit_code": 0,
                "duration_ms": 0,
                "dry_run": True,
                "command": mask_secrets(command),
            }

        start = time.monotonic()
        ssh_cmd = f"ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 {user}@{host} {command!r}"

        try:
            proc = await asyncio.create_subprocess_shell(
                ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
                "command": mask_secrets(command),
                "host": host,
            }
        except asyncio.TimeoutError:
            duration = (time.monotonic() - start) * 1000
            return {
                "stdout": "",
                "stderr": f"SSH command timed out after {timeout}s",
                "exit_code": -1,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "host": host,
            }
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -2,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "host": host,
            }

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            enabled=self.enabled,
            healthy=self.enabled,
            last_check=datetime.utcnow(),
            error=None if self.enabled else "SSH executor disabled by policy",
        )
