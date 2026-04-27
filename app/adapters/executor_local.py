"""LEGACY / NOT USED BY AIOPS RUNNER V1.

Do not wire this into /v1/aiops/actions/run. The official runner is
app/agent_router/services/action_runner.py.

Secure local command executor with backup, timeout, and secret masking.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.adapters.base import BaseExecutorAdapter
from app.core.config import get_settings
from app.models.schemas import ProviderStatus
from app.policies.engine import PolicyEngine
from app.utils.logging import get_logger
from app.utils.secrets import mask_secrets, truncate

logger = get_logger("adapters.executor_local")


class LocalExecutorAdapter(BaseExecutorAdapter):
    name = "local"

    def __init__(self):
        settings = get_settings()
        self.timeout_default = settings.executor_timeout_seconds
        self.max_output = settings.executor_max_output_bytes
        self.enabled = True
        self._policy = PolicyEngine()

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        dry_run: bool = False,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        timeout = timeout or self.timeout_default
        cwd = cwd or "/tmp"

        logger.info(
            "Executing local command",
            extra={"provider": self.name},
        )

        policy_result = self._policy.evaluate_command(command)
        if not policy_result["allowed"]:
            blocked_message = f"{policy_result['reason']}"
            return {
                "stdout": "" if not dry_run else f"[DRY RUN] Blocked by policy: {blocked_message}",
                "stderr": blocked_message,
                "exit_code": 126,
                "duration_ms": 0,
                "dry_run": dry_run,
                "command": mask_secrets(command),
                "cwd": cwd or "/tmp",
            }

        if dry_run:
            return {
                "stdout": f"[DRY RUN] Would execute: {mask_secrets(command)}",
                "stderr": "",
                "exit_code": 0,
                "duration_ms": 0,
                "dry_run": True,
                "command": mask_secrets(command),
                "cwd": cwd,
            }

        start = time.monotonic()
        merged_env = {**os.environ, **(env or {})}

        try:
            argv = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=merged_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            duration = (time.monotonic() - start) * 1000

            stdout = mask_secrets(truncate(stdout_bytes.decode("utf-8", errors="replace"), self.max_output))
            stderr = mask_secrets(truncate(stderr_bytes.decode("utf-8", errors="replace"), self.max_output))

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": proc.returncode,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "cwd": cwd,
            }
        except asyncio.TimeoutError:
            duration = (time.monotonic() - start) * 1000
            logger.warning("Command timed out after %ds", timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "cwd": cwd,
            }
        except ValueError as e:
            duration = (time.monotonic() - start) * 1000
            logger.error("Command parsing failed: %s", e)
            return {
                "stdout": "",
                "stderr": f"Command parsing failed: {e}",
                "exit_code": -2,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "cwd": cwd,
            }
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            logger.error("Command execution failed: %s", e)
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -2,
                "duration_ms": duration,
                "dry_run": False,
                "command": mask_secrets(command),
                "cwd": cwd,
            }

    async def backup_file(self, path: str) -> str | None:
        """Create timestamped backup of a file before modification."""
        p = Path(path)
        if not p.exists():
            return None
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup = p.with_suffix(f".bak-{ts}{p.suffix}")
        shutil.copy2(str(p), str(backup))
        logger.info("Backed up %s -> %s", path, backup)
        return str(backup)

    async def restore_backup(self, backup_path: str, original_path: str) -> bool:
        """Restore a backup file."""
        try:
            shutil.copy2(backup_path, original_path)
            logger.info("Restored %s from %s", original_path, backup_path)
            return True
        except Exception as e:
            logger.error("Restore failed: %s", e)
            return False

    async def health_check(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            result = await self.execute("echo ok", timeout=5)
            latency = (time.monotonic() - start) * 1000
            healthy = result["exit_code"] == 0
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=healthy,
                last_check=datetime.utcnow(),
                latency_ms=latency,
            )
        except Exception as e:
            return ProviderStatus(
                name=self.name,
                enabled=self.enabled,
                healthy=False,
                last_check=datetime.utcnow(),
                error=str(e),
            )
