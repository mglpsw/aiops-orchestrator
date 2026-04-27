"""Motor de políticas: classificação de risco, lista de permissão/negação e lógica de aprovação."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any

from app.core.config import get_policies_config, get_settings
from app.models.schemas import RiskLevel
from app.policies.command_guardrails import find_blocked_command_reason, is_safe_command
from app.utils.logging import get_logger

logger = get_logger("policies.engine")

# --- Lista de negação fixa (sempre bloqueado, sem sobrescrita) ---
ALWAYS_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:^|[\s;&|])rm\s+(?=[^;&|]*-[^\s]*[rR])(?=[^;&|]*-[^\s]*f)[^;&|]*\s+/+(?:\s|$|[;&|*])"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bfdisk\b"),
    re.compile(r"\bparted\b"),
    re.compile(r"\bdd\s+if=.*of=/dev/"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bhalt\b"),
    re.compile(r"\bpoweroff\b"),
    re.compile(r"\binit\s+[06]\b"),
    re.compile(r"\bsystemctl\s+(disable|mask)\s+(prometheus|grafana|nginx|npm|docker|sshd|networking)"),
    re.compile(r"\bpct\s+(destroy|stop)\b"),
    re.compile(r"\bqm\s+(destroy|stop)\b"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bdocker\s+rm\s+-f\s+(prometheus|grafana|npm|open-webui|nextcloud)"),
    re.compile(r"\biptables\s+-F\b"),
    re.compile(r"\bip\s+route\s+(del|flush)\b"),
    re.compile(r"\bchmod\s+-R\s+777\s+/+(?:\s|$|[;&|])"),
    re.compile(r"\bchown\s+-R\s+.*\s+/+(?:\s|$|[;&|])"),
    re.compile(r">\s*/etc/(passwd|shadow|sudoers|fstab|network/interfaces)"),
    re.compile(r"\bcurl\b.*\|\s*(bash|sh)\b"),
    re.compile(r"\bwget\b.*\|\s*(bash|sh)\b"),
]

# --- Padrões de alto risco (requerem aprovação) ---
HIGH_RISK_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bsystemctl\s+(restart|stop)\b"),
    re.compile(r"\bservice\s+\w+\s+(stop|restart)\b"),
    re.compile(r"\bdocker\s+(stop|rm|restart)\b"),
    re.compile(r"\bdocker-compose\s+(down|restart)\b"),
    re.compile(r"\bpct\s+(start|reboot|migrate)\b"),
    re.compile(r"\bqm\s+(start|reboot|migrate)\b"),
    re.compile(r"\bapt\s+(remove|purge|dist-upgrade)\b"),
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bgit\s+(push|reset\s+--hard|force)\b"),
    re.compile(r"\bcrontab\s+-r\b"),
    re.compile(r"\bufw\s+(disable|reset)\b"),
]

# --- Padrões de risco médio ---
MEDIUM_RISK_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bapt\s+(install|upgrade)\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\bsystemctl\s+reload\b"),
    re.compile(r"\bdocker\s+exec\b"),
    re.compile(r"\bdocker(?:-compose|\s+compose)\s+up\b"),
    re.compile(r"\bdocker-compose\s+up\b"),
    re.compile(r"\bcp\s+-r\b"),
    re.compile(r"\bmv\s+"),
    re.compile(r"\bchmod\b"),
    re.compile(r"\bchown\b"),
    re.compile(r"\bmount\b"),
    re.compile(r"\bcrontab\s+-e\b"),
    re.compile(r"\bssh\b"),
]

# --- Operações seguras (sempre permitidas em modo seguro/supervisionado) ---
SAFE_COMMAND_PREFIXES = frozenset({
    "cat", "ls", "df", "du", "free", "uptime", "whoami", "hostname",
    "date", "uname", "id", "ps", "top", "htop",
    "systemctl status", "journalctl", "tail", "head", "grep", "find",
    "wc", "sort", "curl -s", "wget -q", "ping -c", "dig", "nslookup",
    "ss", "netstat", "ip addr", "ip route show", "pct list", "qm list",
    "pvesh get", "prometheus", "grafana", "echo",
})


# --- Dangerous intent keywords (pre-LLM screening) ---
BLOCKED_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\bpoweroff\b", re.IGNORECASE),
    re.compile(r"\bhalt\b.*\b(server|host|proxmox|system|machine)\b", re.IGNORECASE),
    re.compile(r"\bformat\b.*\b(disk|drive|partition|storage)\b", re.IGNORECASE),
    re.compile(r"\bdelete\b.*\b(all|everything|entire|root)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"\bdestroy\b.*\b(container|vm|ct|lxc)\b", re.IGNORECASE),
    re.compile(r"\bwipe\b", re.IGNORECASE),
    re.compile(r"\bdrop\b.*\b(database|table|all)\b", re.IGNORECASE),
    re.compile(r"\bdisable\b.*\b(firewall|ssh|network)\b", re.IGNORECASE),
]

HIGH_RISK_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brestart\b.*\b(service|container|docker|nginx|prometheus|grafana)\b", re.IGNORECASE),
    re.compile(r"\bstop\b.*\b(service|container|docker)\b", re.IGNORECASE),
    re.compile(r"\bremove\b.*\b(container|package|service)\b", re.IGNORECASE),
    re.compile(r"\binstall\b", re.IGNORECASE),
    re.compile(r"\bupgrade\b", re.IGNORECASE),
    re.compile(r"\bmodify\b.*\b(config|configuration|network|firewall)\b", re.IGNORECASE),
    re.compile(r"\bchange\b.*\b(password|port|ip|dns)\b", re.IGNORECASE),
    re.compile(r"\bmigrate\b", re.IGNORECASE),
]


class PolicyEngine:
    def __init__(self):
        self.settings = get_settings()
        self._custom_config = get_policies_config()

    @property
    def mode(self) -> str:
        return self.settings.policy_mode

    def pre_screen_message(self, message: str) -> dict[str, Any] | None:
        """
        Pre-screen raw user message for dangerous keywords BEFORE LLM classification.
        Returns policy result if blocked/high-risk, None if no match (proceed to LLM).
        """
        for pattern in BLOCKED_INTENT_PATTERNS:
            if pattern.search(message):
                logger.warning("Message BLOCKED by keyword pre-screen: %s", message[:100])
                return {
                    "allowed": False,
                    "risk_level": RiskLevel.blocked,
                    "requires_approval": False,
                    "reason": f"Blocked by keyword safety filter: {pattern.pattern}",
                    "pre_screened": True,
                }

        for pattern in HIGH_RISK_INTENT_PATTERNS:
            if pattern.search(message):
                logger.info("Message flagged as high-risk by keyword pre-screen: %s", message[:100])
                return {
                    "allowed": True,
                    "risk_level": RiskLevel.high,
                    "requires_approval": True,
                    "reason": f"High-risk intent detected: {pattern.pattern}",
                    "pre_screened": True,
                    "force_category": "action",
                    "force_requires_execution": True,
                }

        return None

    def evaluate_command(self, command: str) -> dict[str, Any]:
        """
        Evaluate a single command against policies.
        Returns: {allowed, risk_level, reason, requires_approval}
        """
        command_stripped = command.strip()

        blocked_reason = find_blocked_command_reason(command_stripped)
        if blocked_reason:
            logger.warning("Command BLOCKED by shared guardrails: %s", command_stripped[:100])
            return {
                "allowed": False,
                "risk_level": RiskLevel.blocked,
                "reason": blocked_reason,
                "requires_approval": False,
            }

        # Check hardcoded denylist first
        for pattern in ALWAYS_BLOCKED_PATTERNS:
            if pattern.search(command_stripped):
                logger.warning("Command BLOCKED by denylist: %s", command_stripped[:100])
                return {
                    "allowed": False,
                    "risk_level": RiskLevel.blocked,
                    "reason": f"Blocked by security policy: matches pattern {pattern.pattern}",
                    "requires_approval": False,
                }

        config_blocked = self._match_configured_operation(command_stripped, "blocked_operations")
        if config_blocked:
            logger.warning("Command BLOCKED by configured policy: %s", command_stripped[:100])
            return {
                "allowed": False,
                "risk_level": RiskLevel.blocked,
                "reason": f"Blocked by configured policy: {config_blocked}",
                "requires_approval": False,
            }

        config_high_risk = self._match_configured_operation(command_stripped, "high_risk_operations")
        if config_high_risk:
            allowed = self.mode != "manual-only"
            return {
                "allowed": allowed,
                "risk_level": RiskLevel.high,
                "reason": f"High-risk operation configured by policy: {config_high_risk}",
                "requires_approval": True,
            }

        # Check high-risk
        for pattern in HIGH_RISK_PATTERNS:
            if pattern.search(command_stripped):
                allowed = self.mode != "manual-only"
                return {
                    "allowed": allowed,
                    "risk_level": RiskLevel.high,
                    "reason": "High-risk operation detected",
                    "requires_approval": True,
                }

        # Check medium-risk
        for pattern in MEDIUM_RISK_PATTERNS:
            if pattern.search(command_stripped):
                allowed = self.mode in ("supervised", "safe")
                return {
                    "allowed": allowed,
                    "risk_level": RiskLevel.medium,
                    "reason": "Medium-risk operation detected",
                    "requires_approval": self.mode != "safe" or not self.settings.auto_approve_low_risk,
                }

        # Check safe commands
        if is_safe_command(command_stripped) or any(
            command_stripped.startswith(prefix) for prefix in SAFE_COMMAND_PREFIXES
        ):
            return {
                "allowed": True,
                "risk_level": RiskLevel.low,
                "reason": "Safe read-only operation",
                "requires_approval": False,
            }

        # Default: unknown commands are medium-risk
        return {
            "allowed": self.mode != "manual-only",
            "risk_level": RiskLevel.medium,
            "reason": "Unknown command - classified as medium risk by default",
            "requires_approval": True,
        }

    def _match_configured_operation(self, command: str, key: str) -> str | None:
        """Match command against glob-style operation patterns from policies.yml."""
        for pattern in self._custom_config.get(key, []) or []:
            if fnmatchcase(command, pattern):
                return pattern
        return None

    def evaluate_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate an execution plan. Returns overall risk assessment.
        """
        overall_risk = RiskLevel.low
        blocked_steps = []
        high_risk_steps = []

        steps = plan.get("steps", [])
        for step in steps:
            tool = step.get("tool", "")
            args = step.get("args", {})
            command = args.get("command", step.get("description", ""))

            eval_result = self.evaluate_command(command)
            step_risk = eval_result["risk_level"]

            if step_risk == RiskLevel.blocked:
                blocked_steps.append(step)
            elif step_risk == RiskLevel.high:
                high_risk_steps.append(step)

            # Escalate overall risk
            risk_order = [RiskLevel.low, RiskLevel.medium, RiskLevel.high, RiskLevel.critical, RiskLevel.blocked]
            if risk_order.index(step_risk) > risk_order.index(overall_risk):
                overall_risk = step_risk

        if blocked_steps:
            return {
                "allowed": False,
                "risk_level": RiskLevel.blocked,
                "blocked_steps": blocked_steps,
                "requires_approval": False,
                "reason": f"{len(blocked_steps)} step(s) blocked by security policy",
            }

        requires_approval = overall_risk in (RiskLevel.medium, RiskLevel.high, RiskLevel.critical)
        if overall_risk == RiskLevel.low and self.settings.auto_approve_low_risk:
            requires_approval = False

        return {
            "allowed": True,
            "risk_level": overall_risk,
            "requires_approval": requires_approval,
            "high_risk_steps": high_risk_steps,
            "reason": f"Plan risk level: {overall_risk.value}",
        }

    def check_user_execution_allowed(self, user_id: str, user_role: str = "user") -> dict[str, Any]:
        """Check whether a user is authorized to trigger host execution.

        Authorization rules (OR):
          1. user_id (email) is in AIOPS_ALLOWED_EXEC_USERS
          2. user_role == "admin" and allow_admin_role is True (default)
          3. policy_mode == "manual-only" always denies (overrides everything)

        Returns: {allowed: bool, reason: str}
        """
        if self.mode == "manual-only":
            return {
                "allowed": False,
                "reason": "Execution disabled: policy mode is manual-only",
            }

        # Parse allowlist from settings (comma-separated, case-insensitive)
        # Also merge with allowed_exec_users from policies.yml
        raw = (self.settings.allowed_exec_users or "").strip()
        allowed_users: set[str] = {u.strip().lower() for u in raw.split(",") if u.strip()}
        # Merge from YAML config
        for u in self._custom_config.get("allowed_exec_users") or []:
            if u:
                allowed_users.add(str(u).strip().lower())

        user_lower = (user_id or "").strip().lower()

        # Admin role bypass
        if getattr(self.settings, "allow_admin_role", True) and user_role == "admin":
            logger.info("Execution authorized for admin user: %s", user_id)
            return {"allowed": True, "reason": f"Authorized: role=admin ({user_id})"}

        # Explicit allowlist
        if user_lower and user_lower in allowed_users:
            logger.info("Execution authorized for allowed user: %s", user_id)
            return {"allowed": True, "reason": f"Authorized: user in allowlist ({user_id})"}

        # Deny all others
        if not allowed_users and not getattr(self.settings, "allow_admin_role", True):
            # Allowlist empty and admin bypass disabled → open to all (not recommended)
            return {"allowed": True, "reason": "No restrictions configured"}

        logger.warning("Execution denied for unauthorized user: %s (role=%s)", user_id, user_role)
        return {
            "allowed": False,
            "reason": (
                f"Acesso negado: usuário '{user_id}' não está autorizado a executar "
                "comandos no host. Apenas administradores ou usuários autorizados podem "
                "usar esta função."
            ),
        }

    def evaluate_intent(self, classification: dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate a classified intent from the LLM classifier.
        """
        risk_str = classification.get("risk_level", "medium")
        try:
            risk = RiskLevel(risk_str)
        except ValueError:
            risk = RiskLevel.medium

        category = classification.get("category", "action")
        requires_execution = classification.get("requires_execution", False)

        # Queries are always safe
        if category == "query" and not requires_execution:
            return {
                "allowed": True,
                "risk_level": RiskLevel.low,
                "requires_approval": False,
                "reason": "Information query - no execution needed",
            }

        # Dangerous category
        if category == "dangerous":
            return {
                "allowed": False,
                "risk_level": RiskLevel.blocked,
                "requires_approval": False,
                "reason": "Intent classified as dangerous",
            }

        # Actions follow risk level
        requires_approval = risk in (RiskLevel.medium, RiskLevel.high, RiskLevel.critical)
        if risk == RiskLevel.low and self.settings.auto_approve_low_risk and self.mode == "safe":
            requires_approval = False

        allowed = risk != RiskLevel.blocked
        if self.mode == "manual-only":
            requires_approval = True

        return {
            "allowed": allowed,
            "risk_level": risk,
            "requires_approval": requires_approval,
            "reason": f"Intent risk: {risk.value}, category: {category}",
        }
