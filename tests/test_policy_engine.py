"""Policy engine regression tests."""

from __future__ import annotations

import os
import unittest

from app.core.config import get_settings
from app.models.schemas import RiskLevel
from app.policies.engine import PolicyEngine


class PolicyEngineCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AIOPS_POLICY_MODE"] = "supervised"
        get_settings.cache_clear()
        self.policy = PolicyEngine()

    def test_blocks_catastrophic_root_delete_variants(self) -> None:
        commands = [
            "rm -rf /",
            "rm -fr /",
            "sudo rm -rf /",
            "rm -rf /*",
            "rm -rf --no-preserve-root /",
        ]

        for command in commands:
            with self.subTest(command=command):
                result = self.policy.evaluate_command(command)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["risk_level"], RiskLevel.blocked)
                self.assertFalse(result["requires_approval"])

    def test_non_root_recursive_delete_requires_approval(self) -> None:
        result = self.policy.evaluate_command("rm -rf /tmp/scratch")

        self.assertTrue(result["allowed"])
        self.assertEqual(result["risk_level"], RiskLevel.high)
        self.assertTrue(result["requires_approval"])

    def test_configured_high_risk_operations_apply(self) -> None:
        result = self.policy.evaluate_command("pct start 102")

        self.assertTrue(result["allowed"])
        self.assertEqual(result["risk_level"], RiskLevel.high)
        self.assertTrue(result["requires_approval"])

    def test_safe_docker_diagnostics_are_allowed(self) -> None:
        commands = [
            "docker version --format '{{.Server.Version}}'",
            "docker info",
            "docker ps",
            "docker inspect aiops-orchestrator",
            "docker logs aiops-orchestrator",
            "docker events --since 1m",
            "docker compose -f deploy/docker-compose.yml config --quiet",
            "docker-compose -f deploy/docker-compose.yml config --quiet",
        ]

        for command in commands:
            with self.subTest(command=command):
                result = self.policy.evaluate_command(command)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["risk_level"], RiskLevel.low)
                self.assertFalse(result["requires_approval"])

    def test_destructive_docker_commands_are_blocked(self) -> None:
        commands = [
            "docker compose down",
            "docker compose stop",
            "docker compose restart",
            "docker compose rm -f",
            "docker-compose down",
            "docker-compose stop",
            "docker-compose restart",
            "docker-compose rm -f",
            "docker stop aiops-orchestrator",
            "docker kill aiops-orchestrator",
            "docker rm -f aiops-orchestrator",
            "docker restart aiops-orchestrator",
            "docker update --restart=no aiops-orchestrator",
            "docker system prune -f",
            "docker container prune -f",
            "docker network prune -f",
            "docker volume prune -f",
        ]

        for command in commands:
            with self.subTest(command=command):
                result = self.policy.evaluate_command(command)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["risk_level"], RiskLevel.blocked)
                self.assertFalse(result["requires_approval"])

    def test_shell_wrappers_used_to_hide_docker_commands_are_blocked(self) -> None:
        commands = [
            "sudo docker compose down",
            "env docker compose stop",
            "nohup docker compose restart",
            "sh -c 'docker compose rm -f'",
            "bash -c 'docker-compose down'",
        ]

        for command in commands:
            with self.subTest(command=command):
                result = self.policy.evaluate_command(command)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["risk_level"], RiskLevel.blocked)
                self.assertFalse(result["requires_approval"])


if __name__ == "__main__":
    unittest.main()
