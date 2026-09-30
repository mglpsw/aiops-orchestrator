# AgentReview v2 — minimal offline toolrepo installation

**Status:** `CURRENT | V2 DEVELOPMENT` — linha sucessora em desenvolvimento; não é GA, não é default e não é required check. Estado atual: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

Refs #85. Describes how a target repository's privileged workflow (CT104 or
equivalent offline runner) installs the AgentReview engine without pulling
in any AIOps runtime dependency (FastAPI, Uvicorn, SQLAlchemy, database
drivers) it does not need.

## Consumption contract

```text
checkout target repo
checkout aiops-orchestrator at an approved full 40-character lowercase SHA
create a dedicated venv at a fresh, absent path (never reusing an existing venv or the AIOps runtime venv)
install requirements-agent-review.lock with --require-hashes
load profile/policy from the target's trusted base/default checkout
run the v2 CLIs/library entry points offline
publish only artifacts allowlisted by the target's own workflow
```

A branch name, tag, or abbreviated SHA is never an acceptable pin for the
`aiops-orchestrator` checkout consumed by a target workflow.

## Interpreter and Platform contract

Canonical toolrepo target platform: **CPython 3.11 on Linux x86_64 (64-bit word size, glibc >= 2.17 / manylinux2014)**

`requirements-agent-review.lock` is platform/interpreter specific; binary wheels (`pydantic-core`, `PyYAML`) are compiled for `manylinux2014_x86_64`. The installer validates the complete platform and interpreter specification (CPython, 3.11, 64-bit pointer width via `struct.calcsize("P") == 8`, Linux, x86_64, glibc >= 2.17) in an isolated probe before venv creation, refusing incompatible environments (e.g. Python 3.12, 32-bit interpreters, ARM64, musl, macOS) fail-closed.

## Install script

```bash
bash scripts/install-agent-review-toolrepo.sh <venv-dir> \
  --toolrepo-sha <full-40-char-lowercase-sha>
```

On hosts where default `python3` is not CPython 3.11, specify the qualifying interpreter via `AGENT_REVIEW_PYTHON`:

```bash
AGENT_REVIEW_PYTHON=python3.11 \
  bash scripts/install-agent-review-toolrepo.sh <venv-dir>
```

The script:

1. requires `--toolrepo-sha`, when given, to match `^[0-9a-f]{40}$` exactly
   -- a short SHA, branch name, or tag is rejected before any installation
   is attempted;
2. verifies that SHA against the verifiable source identity of the checkout
   (`.source-commit` / `.toolrepo-sha` attestation files in standalone distributions,
   or `git rev-parse HEAD` in Git checkouts), rejecting any mismatch;
3. verifies that the selected interpreter (`$AGENT_REVIEW_PYTHON` or default `python3`)
   matches CPython 3.11 64-bit (`struct.calcsize("P") == 8`) on Linux x86_64 with glibc >= 2.17 via an isolated/no-site probe (`-I -S`), refusing incompatible platforms, 32-bit runtimes, or interpreters fail-closed before creating any venv and immune to ambient `sitecustomize.py`/`PYTHONPATH` hooks;
4. normalizes the prospective `<venv-dir>` path (removing relative/traversal components) before the freshness check; refuses an existing canonical target or symlink fail-closed (exit 2) without deleting or clearing it, eliminating stale residual `site-packages` survival;
5. creates a fresh venv at the verified, canonical prospective path using isolated venv execution (`$PYTHON_BIN -I -S -m venv`), completely isolated from ambient `PYTHONPATH`;
6. installs `requirements-agent-review.lock` using isolated pip execution with pip-level isolation and configuration disabled
   (`PIP_CONFIG_FILE=/dev/null "$VENV_TARGET/bin/python3" -I -m pip --isolated install --require-hashes --no-deps -r "$LOCK_FILE"`), preventing ambient `PYTHONPATH` from shadowing pip, and ignoring caller environment variables (such as `PIP_TARGET`, `PIP_PREFIX`), caller-exported `PIP_CONFIG_FILE`, and user/global configuration files (`pip.conf`), ensuring locked packages are installed strictly into the target venv.

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
