# Handoff to `agent-router-api`

## State of the canonical AIOps

The canonical AIOps path in this repository is now the read-only, audited, approval-gated
workflow:

- `POST /v1/aiops/diagnose`
- `GET /v1/aiops/actions/catalog`
- `POST /v1/aiops/actions/plan`
- `POST /v1/aiops/actions/dry-run`
- `POST /v1/aiops/actions/approvals`
- `GET /v1/aiops/actions/approvals/{approval_id}`
- `POST /v1/aiops/actions/approvals/{approval_id}/approve`
- `POST /v1/aiops/actions/approvals/{approval_id}/reject`
- `POST /v1/aiops/actions/run`
- `GET /v1/aiops/runs/recent`
- `GET /v1/aiops/runs/{run_id}`
- `GET /v1/aiops/audit/recent`

The current AIOps stack includes:

- deterministic health scoring
- action planning from a validated allowlist catalog
- safe dry-run simulation
- persistent approvals
- read-only local runner
- run history
- structured audit log
- redaction of tokens, passwords, secrets, API keys, cookies, and connection strings

## Read-only actions available

The official runner currently exposes only fixed, allowlisted read-only actions:

- `curl_health_8000`
- `curl_ready_8000`
- `curl_health_8001`
- `curl_ready_8001`
- `git_status`
- `git_diff_stat`
- `docker_compose_config`
- `docker_compose_bluegreen_config`
- `systemctl_status_aiops`
- `journalctl_aiops_recent`
- `prometheus_query_allowlisted`

These actions are mapped to internal functions in
`app/agent_router/services/action_runner.py`. The catalog is structural; it is not a source of
free-form executable commands.

## Legacy surfaces

Legacy chat/task/provider routes still exist and are intentionally kept compatible for now:

- `POST /v1/chat`
- `POST /v1/chat/ingest`
- `GET /v1/tasks`
- `GET /v1/tasks/{id}`
- `GET /v1/approvals`
- `POST /v1/approvals/{task_id}`
- `GET /v1/providers/status`

They are deprecated and emit:

- `Deprecation: true`
- `Warning: 299 - "Legacy AIOps endpoint; use canonical /v1/aiops/* APIs"`

Legacy usage is also tracked in Prometheus as:

- `aiops_legacy_endpoint_hits_total{endpoint="..."}`

## What still should not be removed

Do not remove yet:

- `app/services/provider_registry.py`
- `app/services/orchestrator.py`
- `app/api/routes.py`
- `app/adapters/executor_local.py`
- `app/adapters/executor_ssh.py`
- `app/adapters/docker.py`
- the legacy chat/task/provider routes

These surfaces are still live and may have consumers outside this repository.

## What `agent-router-api` must provide

The next phase depends on `agent-router-api` exposing stable operational signals and routing
context so AIOps can stay diagnostic and safe:

- stable `health` and `ready`
- latency metrics
- error metrics
- selected backend identity
- fallback selection status
- token/auth context
- rate limit context
- blocked task counts
- Ollama status
- LiteLLM status

These signals should be available without requiring the AIOps repo to guess or execute.

## Remaining risks

- Legacy surfaces remain active, so replacement is still only partial.
- Provider registry and legacy adapters are still loaded by historical paths.
- The read-only runner depends on host-local tooling for a few allowlisted inspections.
- Prometheus and journal reads may still expose sensitive data patterns, which is why strong
  redaction remains mandatory.

## Next plan on `agent-router-api`

1. Expose the operational signals needed by the AIOps diagnostic layer.
2. Make the backend selection and fallback state observable.
3. Preserve strict auth and rate-limit visibility.
4. Keep the AIOps contract read-only until the next explicit phase.
5. Avoid adding any new execution bridge until the signals and policy model are stable.

## Status summary

- Canonical AIOps here: ready for diagnostic and read-only operation
- Legacy AIOps: deprecated and still compatible
- Next focus: `agent-router-api`

