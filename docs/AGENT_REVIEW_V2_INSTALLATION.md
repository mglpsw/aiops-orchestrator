# AgentReview v2 — minimal offline toolrepo installation

Refs #85. Describes how a target repository's privileged workflow (CT104 or
equivalent offline runner) installs the AgentReview engine without pulling
in any AIOps runtime dependency (FastAPI, Uvicorn, SQLAlchemy, database
drivers) it does not need.

## Consumption contract

```text
checkout target repo
checkout aiops-orchestrator at an approved full 40-character lowercase SHA
create a dedicated venv (never the AIOps runtime venv)
install requirements-agent-review.lock with --require-hashes
load profile/policy from the target's trusted base/default checkout
run the v2 CLIs/library entry points offline
publish only artifacts allowlisted by the target's own workflow
```

A branch name, tag, or abbreviated SHA is never an acceptable pin for the
`aiops-orchestrator` checkout consumed by a target workflow.

## Install script

```bash
bash scripts/install-agent-review-toolrepo.sh <venv-dir> \
  --toolrepo-sha <full-40-char-lowercase-sha>
```

The script:

1. requires `--toolrepo-sha`, when given, to match `^[0-9a-f]{40}$` exactly
   -- a short SHA, branch name, or tag is rejected before any installation
   is attempted;
2. verifies that SHA against `git rev-parse HEAD` of the current checkout,
   rejecting a mismatch;
3. creates a fresh venv at `<venv-dir>`;
4. installs `requirements-agent-review.lock` with
   `pip install --require-hashes --no-deps`.

`--toolrepo-sha` is optional for local iteration but should always be
supplied by an automated privileged workflow, so the install step itself
proves which exact commit was consumed.

## What is not installed

`requirements-agent-review.lock` contains only `pydantic`,
`pydantic-core`, `annotated-types`, `typing-extensions`,
`typing-inspection`, and `PyYAML` -- the complete import closure of
`app/agent_review` beyond the standard library, verified by
`tests/agent_review/test_minimal_toolrepo_lock.py`. `fastapi`, `uvicorn`,
`sqlalchemy`, `aiosqlite`, and equivalent production-runtime/database
dependencies are absent and are not required by any module under
`app/agent_review/`.

## Regenerating the lock

The lock is generated from the exact versions already pinned in
`requirements.txt`/`requirements-dev.txt` for the packages
`app/agent_review` actually imports, hashed for the target platform (CPython
3.11, manylinux2014/glibc x86_64 -- the offline toolrepo target). To
regenerate on a different platform or after a version bump:

```bash
pip download --no-deps --dest /tmp/agent-review-lock <package>==<version>  # for each pinned package
pip hash /tmp/agent-review-lock/*.whl
```

and update `requirements-agent-review.lock` with the resulting
`--hash=sha256:...` lines. `pydantic-core` and `PyYAML` ship compiled
extensions; hashes are platform-specific and must be regenerated (not
hand-merged from another platform) when the target platform changes.

## Verifying a clean install

```bash
bash scripts/install-agent-review-toolrepo.sh /tmp/agent-review-venv
/tmp/agent-review-venv/bin/python3 -m pip list --format=freeze
PYTHONPATH="$(pwd)" /tmp/agent-review-venv/bin/python3 -c \
  "from app.agent_review.contracts_v2 import ChunkPayloadV2; print('ok')"
```

`tests/agent_review/test_minimal_toolrepo_lock.py` automates this
end-to-end (marked `requires_network`, since it performs a real package
installation; excluded from the default offline gate the same way every
other `requires_network` test is).

## Release reproduction

For a pinnable release (see `docs/RELEASE_V0_21_0.md`), the clean install
above is re-run with `--toolrepo-sha` set to the release's exact source SHA
as part of the RC ensaio checklist, before the RC prerelease is published
and again before the final tag — never assumed to still pass from an
earlier run at a different SHA.
