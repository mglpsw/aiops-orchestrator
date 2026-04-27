# Legacy Adapters

The repository still contains execution adapters that predate the controlled AIOps runner.
They are kept for historical compatibility and for legacy flows, but they are not part of
`POST /v1/aiops/actions/run`.

## Current official execution path

The only official execution path for the AIOps runner v1 is:

- `app/agent_router/services/action_runner.py`

That runner is the only module allowed to execute the read-only allowlisted AIOps actions used
by `/v1/aiops/actions/run`.

## Legacy adapters

The following adapters exist in `app/adapters/`:

- `executor_local.py`
- `docker.py`
- `executor_ssh.py`

### Why they are legacy

- They were designed before the controlled AIOps runner existed.
- They accept command-oriented inputs instead of fixed action functions.
- They are not the official runner for the AIOps v1 `/run` endpoint.

### Why they are not used by the official runner

- The official runner maps `action_id` to fixed internal functions.
- The official runner does not import these adapters.
- The official runner does not accept shell text, free-form arguments, or request-driven command
  strings.

### Future reintroduction

If any of these adapters must be reintroduced in a future session, that work must include:

1. A new security review.
2. A fresh approval gate.
3. An explicit policy update.
4. Audit logging for the new path.
5. Dedicated tests proving the adapter cannot be reached from the controlled runner accidentally.

Until that happens, they should be treated as legacy code paths only.
