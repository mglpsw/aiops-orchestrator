"""Main orchestrator: receives chat, classifies, plans, validates, executes."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    TaskStatus, RiskLevel, ChatIngestRequest, ChatIngestResponse,
)
from app.policies.engine import PolicyEngine
from app.services.provider_registry import get_registry
from app.services.task_service import TaskService
from app.utils.logging import get_logger

logger = get_logger("services.orchestrator")


class Orchestrator:
    """Coordinates the full flow: classify -> plan -> policy check -> execute."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.task_service = TaskService(db)
        self.policy = PolicyEngine()
        self.registry = get_registry()

    async def ingest_chat(self, request: ChatIngestRequest) -> ChatIngestResponse:
        """Main entry point: receive chat message, create task, classify, plan."""
        # Extract user identity from context (set by agent-router after JWT decode)
        user_id = request.user_id or "anonymous"
        user_role = str(request.context.get("user_role", "user")).lower()

        # 0. User execution authorization check (before any LLM call)
        # Queries are exempt — only execution-capable requests need this gate.
        # We do a lightweight pre-check here; enforcement is also in the plan phase.
        user_auth = self.policy.check_user_execution_allowed(user_id, user_role)

        # 1. Create task
        task = await self.task_service.create_task(
            message=request.message,
            user_id=user_id,
            context=request.context,
        )
        logger.info("Task created: %s (user=%s role=%s)", task.id, user_id, user_role, extra={"task_id": task.id})

        try:
            # 1.5. Pre-screen message for dangerous keywords (before LLM)
            pre_screen = self.policy.pre_screen_message(request.message)
            if pre_screen and not pre_screen["allowed"]:
                task = await self.task_service.update_status(
                    task.id, TaskStatus.blocked,
                    risk_level=pre_screen["risk_level"].value,
                    error=pre_screen["reason"],
                )
                return ChatIngestResponse(
                    task_id=task.id,
                    status=TaskStatus.blocked,
                    summary=request.message[:100],
                    risk_level=pre_screen["risk_level"],
                    requires_approval=False,
                    message=f"Blocked: {pre_screen['reason']}",
                )

            # 2. Classify intent via LLM
            task = await self.task_service.update_status(task.id, TaskStatus.planning)
            classification = await self._classify(task.id, request.message)

            # 2.5. Override classification if pre-screen flagged high risk
            if pre_screen and pre_screen.get("force_category"):
                classification["category"] = pre_screen["force_category"]
                classification["requires_execution"] = pre_screen.get("force_requires_execution", True)
                risk_order = ["low", "medium", "high", "critical", "blocked"]
                pre_risk = pre_screen["risk_level"].value
                llm_risk = classification.get("risk_level", "low")
                if risk_order.index(pre_risk) > risk_order.index(llm_risk) if llm_risk in risk_order else True:
                    classification["risk_level"] = pre_risk

            # 3. Evaluate intent policy
            intent_eval = self.policy.evaluate_intent(classification)
            risk_level = intent_eval["risk_level"]

            if not intent_eval["allowed"]:
                task = await self.task_service.update_status(
                    task.id, TaskStatus.blocked,
                    risk_level=risk_level.value,
                    error=intent_eval["reason"],
                )
                return ChatIngestResponse(
                    task_id=task.id,
                    status=TaskStatus.blocked,
                    summary=classification.get("summary", request.message[:100]),
                    risk_level=risk_level,
                    requires_approval=False,
                    message=f"Blocked: {intent_eval['reason']}",
                )

            # 4. For queries, respond directly without planning
            if classification.get("category") == "query" and not classification.get("requires_execution"):
                answer = await self._answer_query(task.id, request.message)
                task = await self.task_service.set_result(task.id, {"answer": answer})
                return ChatIngestResponse(
                    task_id=task.id,
                    status=TaskStatus.completed,
                    summary=classification.get("summary", "Query answered"),
                    risk_level=RiskLevel.low,
                    requires_approval=False,
                    message=answer,
                )

            # 4.5. Enforce execution authorization — only after confirming this is an
            # action/execution task, not a query.
            if not user_auth["allowed"]:
                task = await self.task_service.update_status(
                    task.id, TaskStatus.blocked,
                    risk_level=RiskLevel.blocked.value,
                    error=user_auth["reason"],
                )
                return ChatIngestResponse(
                    task_id=task.id,
                    status=TaskStatus.blocked,
                    summary=request.message[:100],
                    risk_level=RiskLevel.blocked,
                    requires_approval=False,
                    message=user_auth["reason"],
                )

            # 5. Generate execution plan
            plan = await self._create_plan(task.id, request.message, classification)

            # 6. Evaluate plan against policy
            plan_eval = self.policy.evaluate_plan(plan)
            plan_risk = plan_eval["risk_level"]
            requires_approval = plan_eval["requires_approval"]

            if not plan_eval["allowed"]:
                task = await self.task_service.update_status(
                    task.id, TaskStatus.blocked,
                    plan_json=plan,
                    risk_level=plan_risk.value,
                    error=plan_eval["reason"],
                )
                return ChatIngestResponse(
                    task_id=task.id,
                    status=TaskStatus.blocked,
                    summary=plan.get("objective", request.message[:100]),
                    risk_level=plan_risk,
                    requires_approval=False,
                    message=f"Plan blocked: {plan_eval['reason']}",
                )

            # 7. Set plan and determine next status
            task = await self.task_service.set_plan(
                task.id, plan, plan_risk, requires_approval
            )

            if not requires_approval:
                # Auto-execute low-risk approved tasks
                result = await self._execute_plan(task.id, plan)
                status = TaskStatus.completed if result.get("success") else TaskStatus.failed
                task = await self.task_service.set_result(task.id, result, status)
                return ChatIngestResponse(
                    task_id=task.id,
                    status=status,
                    summary=plan.get("objective", ""),
                    risk_level=plan_risk,
                    requires_approval=False,
                    message=result.get("summary", "Execution complete"),
                )

            # 8. Needs approval
            return ChatIngestResponse(
                task_id=task.id,
                status=TaskStatus.awaiting_approval,
                summary=plan.get("objective", request.message[:100]),
                risk_level=plan_risk,
                requires_approval=True,
                message=f"Plan requires approval (risk: {plan_risk.value}). Use /v1/approvals to approve.",
            )

        except Exception as e:
            logger.exception("Orchestration failed for task %s", task.id)
            await self.task_service.set_error(task.id, str(e))
            return ChatIngestResponse(
                task_id=task.id,
                status=TaskStatus.failed,
                summary=request.message[:100],
                risk_level=None,
                message=f"Error: {str(e)[:500]}",
            )

    async def execute_approved_task(self, task_id: str) -> dict[str, Any]:
        """Execute a task that has been approved."""
        task = await self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != TaskStatus.approved.value:
            raise ValueError(f"Task {task_id} is not approved (status: {task.status})")

        plan = task.plan_json
        if not plan:
            raise ValueError(f"Task {task_id} has no plan")

        await self.task_service.update_status(task_id, TaskStatus.executing)
        result = await self._execute_plan(task_id, plan)
        status = TaskStatus.completed if result.get("success") else TaskStatus.failed
        await self.task_service.set_result(task_id, result, status)
        return result

    async def _classify(self, task_id: str, message: str) -> dict[str, Any]:
        """Classify intent using configured classifier provider."""
        provider = self.registry.get_llm(role="classify")
        result = await provider.classify_intent(message)

        # Record the call
        await self.task_service.record_provider_call(
            task_id=task_id,
            provider=provider.name,
            role="classify",
            model=result.get("model"),
            input_tokens=result.get("usage", {}).get("prompt_tokens"),
            output_tokens=result.get("usage", {}).get("completion_tokens"),
            latency_ms=result.get("latency_ms"),
            success=True,
        )

        # Parse JSON from LLM response
        text = result.get("text", "{}")
        try:
            # Try to extract JSON from response
            text_clean = text.strip()
            if text_clean.startswith("```"):
                # Strip markdown code block
                lines = text_clean.split("\n")
                text_clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            classification = json.loads(text_clean)
        except json.JSONDecodeError:
            logger.warning("Failed to parse classification JSON, using defaults")
            classification = {
                "intent": "unknown",
                "category": "action",
                "risk_level": "medium",
                "summary": message[:100],
                "requires_execution": True,
            }

        return classification

    async def _create_plan(self, task_id: str, message: str, classification: dict) -> dict[str, Any]:
        """Generate execution plan using planner provider."""
        provider = self.registry.get_llm(role="plan")
        context = json.dumps(classification, default=str)
        result = await provider.create_plan(message, context=context)

        await self.task_service.record_provider_call(
            task_id=task_id,
            provider=provider.name,
            role="plan",
            model=result.get("model"),
            input_tokens=result.get("usage", {}).get("prompt_tokens"),
            output_tokens=result.get("usage", {}).get("completion_tokens"),
            latency_ms=result.get("latency_ms"),
            success=True,
        )

        text = result.get("text", "{}")
        try:
            text_clean = text.strip()
            if text_clean.startswith("```"):
                lines = text_clean.split("\n")
                text_clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            plan = json.loads(text_clean)
        except json.JSONDecodeError:
            logger.warning("Failed to parse plan JSON, creating minimal plan")
            plan = {
                "objective": message[:200],
                "steps": [],
                "risk_level": classification.get("risk_level", "medium"),
                "requires_approval": True,
                "proposed_provider": "local",
            }

        return plan

    async def _answer_query(self, task_id: str, message: str) -> str:
        """Answer a query directly using the planner provider."""
        provider = self.registry.get_llm(role="plan")
        result = await provider.generate(
            prompt=message,
            system="You are a helpful homelab assistant. Answer concisely and accurately.",
        )
        await self.task_service.record_provider_call(
            task_id=task_id,
            provider=provider.name,
            role="summarize",
            model=result.get("model"),
            latency_ms=result.get("latency_ms"),
            success=True,
        )
        return result.get("text", "Unable to answer")

    async def _execute_plan(self, task_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Execute plan steps sequentially with safety checks."""
        results = []
        success = True

        steps = plan.get("steps", [])
        if not steps:
            return {"success": True, "summary": "No steps to execute", "results": []}

        for step in steps:
            order = step.get("order", 0)
            tool = step.get("tool", "local")
            args = step.get("args", {})
            command = args.get("command", "")

            if not command:
                results.append({
                    "step": order,
                    "status": "skipped",
                    "reason": "No command specified",
                })
                continue

            # Re-validate each step against policy
            eval_result = self.policy.evaluate_command(command)
            if not eval_result["allowed"]:
                results.append({
                    "step": order,
                    "status": "blocked",
                    "reason": eval_result["reason"],
                })
                success = False
                break

            # Get executor
            executor = self.registry.get_executor(tool)
            exec_result = await executor.execute(
                command=command,
                cwd=args.get("cwd"),
                timeout=args.get("timeout", 60),
                dry_run=args.get("dry_run", False),
            )

            # Record execution
            await self.task_service.record_execution(
                task_id=task_id,
                step_order=order,
                tool=tool,
                command=command,
                cwd=args.get("cwd"),
                stdout=exec_result.get("stdout", "")[:2000],
                stderr=exec_result.get("stderr", "")[:2000],
                exit_code=exec_result.get("exit_code"),
                duration_ms=exec_result.get("duration_ms"),
                dry_run=exec_result.get("dry_run", False),
            )

            results.append({
                "step": order,
                "status": "ok" if exec_result.get("exit_code") == 0 else "failed",
                "exit_code": exec_result.get("exit_code"),
                "stdout": exec_result.get("stdout", "")[:500],
                "stderr": exec_result.get("stderr", "")[:500],
            })

            if exec_result.get("exit_code") != 0:
                success = False
                logger.warning("Step %d failed with exit code %s", order, exec_result.get("exit_code"))
                # Don't continue executing after failure
                break

        summary = f"Executed {len(results)}/{len(steps)} steps. " + ("All succeeded." if success else "Execution stopped on failure.")
        return {"success": success, "summary": summary, "results": results}
