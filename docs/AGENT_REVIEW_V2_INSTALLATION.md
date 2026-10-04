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

Additional installer capabilities are distinct from that platform identity. The
same qualified bootstrap authority must admit Linux subreaper SET/GET and a
readable, consumable `/proc/self/task/<tid>/children` interface before its first
installation filesystem mutation: parent creation, private staging or workers.
Admission and runtime discovery use one structural reader. Empty contents are
valid; tokens must be positive decimal PIDs representable by the signal consumer.
Missing, unreadable or invalid contents refuse admission with exit 2 and
`STOP_UNQUALIFIED_PROC_CHILDREN`, without creating a parent, staging environment,
worker or final target. The startup list is not cached for later discovery.

This procfs interface supplies current first-level children of the single-thread
subreaper authority, including descendants subsequently adopted by it. Reading
it is an operational discovery mechanism, not proof of an immutable/exhaustive
process-tree snapshot under concurrent exit. Admission does not guarantee future
availability after external mount/permission changes; runtime read failures remain
operational errors, never an empty-tree observation or presumed success. Existing
actual wait/reaping and finite-scenario limits below remain applicable. This does
not change the separate NO_REPLACE environmental precondition: negative discovery
of that operation is still permitted at publication, without an early probe.

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

The selected bootstrap interpreter (`AGENT_REVIEW_PYTHON`, or `python3` when
absent) runs the compatibility preflight, installation supervisor and bootstrap
helpers. The supervisor does not select a second interpreter from the host.
The private staging venv's own Python continues to run its isolated pip.

The script:

1. requires `--toolrepo-sha`, when given, to match `^[0-9a-f]{40}$` exactly
   -- a short SHA, branch name, or tag is rejected before any installation
   is attempted;
2. verifies that SHA against the verifiable source identity of the checkout
   (`.source-commit` / `.toolrepo-sha` attestation files in standalone distributions,
   or `git rev-parse HEAD` in Git checkouts), rejecting any mismatch. Git resolution adheres to the project's ratified `#200-G1` bounded git TCB (`bounded_git_v2`): `git` is resolved strictly against the platform's default search path (`os.defpath` / `getconf PATH`), executing with an allowlist clean environment (`env -i`) that disables ambient `GIT_*` configuration hooks and ignores ambient caller `PATH` manipulation; both `getconf` and `env` helpers are bound strictly to trusted absolute system executables (`/usr/bin/getconf`, `/bin/getconf`, `/usr/bin/env`, `/bin/env`) outside ambient caller `PATH`;
3. verifies that the selected interpreter (`$AGENT_REVIEW_PYTHON` or default `python3`)
   matches CPython 3.11 64-bit (`struct.calcsize("P") == 8`) on Linux x86_64 with glibc >= 2.17 via an isolated/no-site probe (`-I -S`), refusing incompatible platforms, 32-bit runtimes, or interpreters fail-closed before creating any venv and immune to ambient `sitecustomize.py`/`PYTHONPATH` hooks. When `AGENT_REVIEW_PYTHON` is specified, it is canonicalized to an absolute path upon admission, establishing same-object binding across the preflight probe, target normalization, process exec transfer, and all authority worker phases, bypassing ambient `python3` on `PATH`. In the absence of `AGENT_REVIEW_PYTHON`, fallback `python3` defaults to caller environment `PATH` (`EXPLICIT_TCB` assumption for standard interactive invocations); callers operating in untrusted or multi-tenant PATH environments must specify `AGENT_REVIEW_PYTHON` to establish bound same-object qualification;
4. validates the prospective `<venv-dir>` path under `TargetPathContract`: enforces the admitted project policy of path length <= 4096 bytes and component length <= 255 bytes, and rejects exactly NUL, LF and CR (`\x00`, `\n`, `\r`) fail-closed (exit 2) before creating any staging directory; normalizes the prospective path (removing relative/traversal components) before the freshness check; refuses an existing canonical target or symlink fail-closed (exit 2) without deleting or clearing it, eliminating stale residual `site-packages` survival;
5. loads `scripts/agent-review-install-authority.py` using Bash built-in redirection (`$(< "$INSTALL_AUTHORITY")`) without invoking ambient `cat` from `PATH`, resolving script directory and commit hashes via built-in parameter expansion without external `dirname` or `tr`, and transfers the installer PID with Bash `exec` to the authority code executed by the selected bootstrap with `-I -S`. The authority verifies Linux subreaper SET/GET and the shared procfs child reader before parent creation, staging or workers, supervises preparation workers, performs the actual NO_REPLACE publication and records its result itself. INT/TERM handlers record cancellation requests; workers remain interruptible. Only the short publication/result transition masks INT/TERM. Worker escalation never targets the authority. There is no FIFO witness, separate disposable publisher or Bash KILL timeout. The staging venv's Python still owns isolated pip.
6. after the authority admits subreaper and procfs child-reader capabilities, creates any missing target parent and a fresh venv in a private, unguessable staging directory (`.agent_review_stage.XXXXXX`) within the target's parent directory using isolated venv execution (`$PYTHON_BIN -I -S -m venv`), completely isolated from ambient `PYTHONPATH`;
7. installs `requirements-agent-review.lock` strictly inside the private staging venv using isolated pip execution with pip-level isolation and configuration disabled (`PIP_CONFIG_FILE=/dev/null "$PRIVATE_STAGE/bin/python3" -I -m pip --isolated install --require-hashes --no-deps -r "$LOCK_FILE"`), passing environment variables directly via `Popen`'s environment mapping without resolving ambient `PATH` helpers (`env`), preventing ambient `PYTHONPATH` from shadowing pip, and ignoring caller environment variables (such as `PIP_TARGET`, `PIP_PREFIX`), caller-exported `PIP_CONFIG_FILE`, and user/global configuration files (`pip.conf`), ensuring locked packages are installed strictly into the private staging environment;
8. applies `RelocationClosureV1` to the staged venv before publication: removes disposable bytecode (`__pycache__` and `*.pyc`) failing closed on any filesystem error; applies `DiscardAtBoundary` to unconsumed surfaces by discarding unused shell activation scripts (`bin/activate*`, `Activate.ps1`) and console pip scripts (`bin/pip*`), failing closed (exit 2) if any discard operation fails, eliminating quoting vulnerabilities on whitespace/unusual paths without silent error suppression; safely rewrites verified textual paths referencing the staging directory to the canonical target path; fails closed on any unnormalizable binary or residual staging references (inspecting all symlinks across both directories and regular files without following them); and strictly validates under R4 census that remaining executable shebangs target the canonical venv Python and do not exceed the conservative project shebang policy of 127 bytes (not a universal kernel limit). All census traversals enforce fail-closed directory iteration (`onerror=walk_error_handler`) and unreadable file detection;
9. commits the transaction via atomic `renameat2(RENAME_NOREPLACE)` from the private staging path to the canonical target; if the target already exists or is created concurrently, publication fails fail-closed (status 2) without mutating or deleting the winner's target;

### Publication, teardown and external result

The authority reports publication (`NOT_PUBLISHED`, `COMMITTED`, or `UNKNOWN`) and teardown (`COMPLETE` or `FAILED`) with reasons on stderr. These dimensions are independent: teardown failure does not undo a commit, and commit alone does not qualify successful teardown. Staging teardown executes an isolated controlled cleanup worker via qualified bootstrap (`$PYTHON_BIN -I -S -c`) without relying on ambient `rm` from `PATH`, and enforces mandatory postcondition verification of staging absence (`not os.path.lexists(stage_dir)`), failing closed with `teardown=FAILED` if the staging directory or any residual entry survives.

| Exit | Meaning |
|---|---|
| 0 | This invocation committed and teardown completed. |
| 2 | Admission refusal or concurrent target won NO_REPLACE; no publication by this invocation. |
| 3 | Staging setup operational failure (mkdir/mkdtemp/chmod) or publication operational failure, including unavailable NO_REPLACE; no overwrite fallback; no false admission refusal. |
| Phase error | Existing nonzero venv/pip/preparation status before publication is preserved. |
| 130 / 143 | INT / TERM cancellation with no publication by this invocation. |
| 4 | COMMITTED with teardown failed; also teardown failure with no other primary failure. Final is preserved; global success is false. |
| 5 | Publication outcome could not be established, when the authority remains able to communicate. Neither success nor safe cancellation is inferred. |

A secondary precommit cleanup failure preserves its existing nonzero primary status and reports teardown FAILED explicitly. The installer performs no automatic retry and never cleans FINAL, whether published or owned by a competitor. Staging cleanup is executed via the qualified bootstrap interpreter rather than ambient `rm`, and verifies that the staging directory is completely absent on disk, preventing ambient path hijacks from fabricating successful teardown. A committed operation with exit 4 requires operator diagnosis, not an automatic reinstall. These statements govern this installer; they do not claim control of unexamined external retry policies.

Worker grace deadlines use monotonic time. Expiry admits TERM/KILL escalation of owned children, not a conclusion that the process tree is gone. The verified subreaper retains responsibility until actual waits establish completion or an operational error is explicitly classified. Qualification covers finite admitted trees (including the controlled real-installer U1 escaping TERM-resistant orphan), workers eventually scheduled and killable in a healthy kernel. There is no absolute bound for arbitrary kernel conditions. External death of the last authority, kernel failure and power loss exclude guaranteed delivery of a terminal result; absence of a report is inconclusive and never proves rollback. The installer's own cancellation/escalation is included in the guarantee, not excluded under that limitation.

### RelocationClosureV1 and fail-closed census

Under `RelocationClosureV1`, preparation operations are divided into strictly enforced obligations:
- **Mandatory discards (`DiscardAtBoundary`):** Disposable shell activation scripts (`bin/activate*`, `bin/Activate.ps1`) and console pip wrappers (`bin/pip*`) must be unlinked or removed prior to publication. Unlike legacy scripts that silently suppressed errors with `except OSError: pass`, any failure to discard an unconsumed surface raises an immediate operational failure, causing preparation to exit 2 fail-closed (`publication=NOT_PUBLISHED`).
- **Bytecode elimination:** Removal of bytecode files (`*.pyc`) and caches (`__pycache__`) is mandatory; deletion failures raise an immediate error and fail closed (exit 2).
- **Mandatory census & fail-closed traversal:**
  - R1: Bytecode removal pass.
  - R2: Textual path normalization pass. Any file that cannot be opened for reading fails closed immediately.
  - R3: Residual staging path census. Verifies that no remaining regular files or symlinks (inspecting symlinks across both directories and regular files collections without following symlinks) reference the staging pathname. Unreadable files or unreadable symlinks fail closed immediately (`CannotInspect(x) != PropertyAbsent(x)`).
  - R4: Executable shebang census. Verifies that all executables in `bin/` have canonical shebangs strictly targeting the canonical venv Python (`final_dir/bin/python3`, `final_dir/bin/python`, or versioned `pythonX.Y`) and meet the 127-byte boundary policy. Directory listing or file reading failures fail closed immediately.
  - All `os.walk` passes specify an explicit `walk_error_handler` callback to abort execution on any directory traversal or permission denial rather than silently truncating the census domain.

### Threat model and concurrency boundaries

The installer explicitly defines its security and concurrency perimeter:
- **In-scope concurrency:** Multiple legitimate installer invocations may compete for the final target directory (`VENV_TARGET`). Concurrency safety is guaranteed by random, unguessable staging directories created via `tempfile.mkdtemp` and an atomic `renameat2(RENAME_NOREPLACE)` commit. Exactly one installer can win the publication race; losing installers observe `EEXIST`, fail closed (exit 2), and clean up only their own private staging directory. A competitor's final target is never inspected, mutated, or deleted.
- **Out-of-scope adversary:** An unprivileged, hostile process executing under the same UID (for example, attempting to inspect `/proc/*/cmdline` and substitute or manipulate private staging directory pathnames between creation and cleanup) operates outside the admitted threat perimeter of this offline toolrepo installer. The installer assumes a cooperative single-user security context; establishing security isolation against hostile concurrent processes under the same user identity would require external containerization or namespace boundaries (e.g. user/mount namespaces or cgroups), which is explicitly out of scope for the toolrepo installer (`STOP_SCOPE_EXPANSION`). Therefore, cleanup authority over the private staging directory relies on process-private filesystem state under the admitted threat model, and Finding 4173148120 is formally adjudicated as `DISMISSED`.

### Pathnames and diagnostics

Operations and argument vectors retain the original admitted pathname. Installer-produced diagnostics render pathname values using delimited ASCII escapes: Bash's builtin `printf %q` under `LC_ALL=C` before qualification (inside `<...>`), and Python `ascii` after qualification. Actual LF, ESC, TAB, backslash and Unicode direction controls remain distinguishable from literal visible escape sequences and cannot operate the terminal through these messages. Ordinary Unicode paths remain admitted under the existing path policy. Human-readable success messages retain their labels; their pathname payload is now quoted/escaped. No machine consumer of these labels was found in scripts/tests/workflows. This is not a general sanitizer for arbitrary external-tool output.

The authority helper is part of the manifest's required standalone installation boundary. Its complete stdlib source is executed by the qualified bootstrap; no import from the hosting checkout is required. The materializer and source attestations retain their existing authority and behavior.


`--toolrepo-sha` is optional for local iteration but should always be
supplied by an automated privileged workflow, so the install step itself
proves which exact commit was consumed.

Path byte limits are admission policy, not a promise that every filesystem supports every admitted path. Atomic publication requires working `renameat2(RENAME_NOREPLACE)` support on the target kernel/filesystem; the interpreter/platform probe alone does not establish that capability.

The consumed runtime surface is the published `bin/python3`, its real venv (`sys.prefix`), locked dependencies and AgentReview imports/entry points. `python3 -m pip list` is an inspection surface used for qualification; activation and console pip scripts are discarded. Wrapper-based path controls qualify argument preservation, publication, execution by pathname and discard behavior. Real independent installations qualify the eight tested classes (ordinary, space, single/double quote, dollar, backtick, backslash and Unicode); they do not exhaust every pathname admitted by the policy.

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
