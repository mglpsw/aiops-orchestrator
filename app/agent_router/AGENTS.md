# AGENTS.md — app/agent_router

Specializes the root `AGENTS.md`. Does not weaken any hard boundary declared
there; only adds invariants specific to this directory.

## What lives here

The AIOps Diagnostic Engine v1 and Action Planner v1: signal collection,
finding generation, dry-run action simulation, an approval store, and the
action runner. This is runtime-facing code (`app/agent_router/main.py`
mounts a real FastAPI router), not the AgentReview review engine — do not
confuse the two when reasoning about blast radius.

## Runtime boundary

- This directory's job is diagnosis and, once explicitly approved,
  execution of a pre-catalogued, allow-listed action
  (`app/agent_router/services/action_runner.py`'s `allowed_action_ids`) —
  never an arbitrary or model-generated command. A finding never carries
  executable content; `AIOpsFinding`/`AIOpsRecommendedAction`
  (`schemas.py`) are diagnosis-only, dry-run-only data shapes by
  construction.
- `simulate_action_dry_run` (dry-run) and `execute_action` (real execution)
  are separate, explicitly named entry points. Never collapse them into one
  path "for convenience" — a caller must not be able to trigger real
  execution while believing it requested a dry run.
- An approval (`approval_store.py`) is required before a real action runs.
  Do not add a code path that executes a catalogued action without first
  passing through `decide_approval`/an equivalent explicit approval check.
- This service is CT102-adjacent runtime surface, not CT104/toolrepo. See
  the root `AGENTS.md` for the CT102/CT104 distinction — a change here has
  a materially different blast radius than a change under
  `app/agent_review/`.

## Router transport, not authority

`AGENT_ROUTER_BASE_URL`/`AGENT_ROUTER_API_KEY` (consumed by
`scripts/github_agent_review.py` and `.github/workflows/agent-review.yml`)
make this service a transport for LLM inference, not a decision authority.
HTTPS access to this router does not, by itself, authorize host access,
SSH, Docker, or filesystem access on whatever runs behind it — an agent
reasoning about this code must not conflate "I can reach the router" with
"I am authorized to do X through it".

## What a reviewer here must never suggest

- collapsing dry-run and real execution into a single code path;
- adding an action-execution path that bypasses `allowed_action_ids` or the
  approval store;
- treating router reachability as authorization for host/SSH/Docker/
  filesystem access;
- free shell, SSH, `docker exec`, or direct deploy from this service.
