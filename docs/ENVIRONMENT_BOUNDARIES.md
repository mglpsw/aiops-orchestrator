# Environment Boundaries

This repository distinguishes production runtime from development tooling before AgentReview Engine work begins.

| Context | Role | Source of Truth |
| --- | --- | --- |
| CT102 | prod/runtime | GitHub after reviewed merge |
| CT104 | dev/toolrepo/runner | GitHub branches and PRs |
| GitHub | source of truth | Reviewed commits and PR history |

## Recommended Paths

CT102 runtime:

```text
/opt/aiops-orchestrator
```

CT104 toolrepo:

```text
/opt/agent-tools/aiops-orchestrator-toolrepo
```

## CT102 Rules

- Do not develop experimental branches.
- Do not run AgentReview tooling.
- Do not run intake, parser, or synthesizer scripts.
- Do not use CT102 as staging.
- Update runtime only after a PR is merged and an explicit runtime decision is made.
- Treat every runtime transition, including `v0.20.0`, as operational runtime
  work separate from AgentReview tooling. AgentReview CLIs remain CT104-only
  and forbidden on CT102.

## CT104 Rules

- May clone the toolrepo.
- May create branches.
- May run offline pytest.
- May execute AgentReview scripts in the declared dev/toolrepo environment.
- Is not production.

## Correct Flow

```text
CT104 creates branch
-> push
-> PR
-> merge
-> CT102 updates runtime only when necessary
```

## Anti-Patterns

- Editing experimental branches on CT102.
- Running AgentReview on CT102.
- Calling CT102 from PR review.
- Hardcoding IPs or hosts in scripts.
- Using hostname as the only source of truth.
- Confusing deploy with offline validation.

## Recommended Variables

CT102:

```text
AIOPS_ENVIRONMENT=prod
AIOPS_NODE_ROLE=runtime
AIOPS_REPO_MODE=aiops_runtime
AIOPS_PRODUCTION_RUNTIME=true
```

CT104:

```text
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

AgentReview scripts must call, or reuse logic equivalent to:

```text
python scripts/guard-aiops-environment.py --require-mode agent_review_tooling --deny-production-runtime
```
