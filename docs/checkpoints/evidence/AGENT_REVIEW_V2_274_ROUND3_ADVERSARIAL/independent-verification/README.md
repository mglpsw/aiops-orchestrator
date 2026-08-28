# Independent verification (Claude, 2026-08-28)

The three lanes in `../laneA`, `../laneB`, `../laneC` were dispatched via
Codex CLI (`codex exec --sandbox workspace-write`) against exact head
`c68a8b9a6b4d57383918f7fc1fa6a85536e331c6`. Before acting on any of their
findings, the six most severe were independently reproduced from scratch,
not merely re-read. This directory holds those reproductions as standalone
scripts, runnable against any checkout of this repository with
`PYTHONPATH` set to its root and `/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python`.

Each script is self-contained (creates its own `mktemp -d` fixture) and
prints `CONFIRMED` / `not reproduced` for its claim. None of them write to
this repository's own checkout; each builds a disposable fixture repo
under a fresh temp directory.

| Script | Claim verified | Result |
|---|---|---|
| `01_includeif_worktree_bypass.sh` | `includeIf.gitdir:…/.git/worktrees/**` is invisible to the filter detector before the disposable worktree exists, then activates a `filter.evil.smudge` during `git worktree add`, and `acquire_diff_v2` returns success | CONFIRMED — detector `False`, acquisition `SUCCESS`, marker written |
| `02_git_config_parameters_leak.sh` | `GIT_CONFIG_PARAMETERS` is not in `_NEUTRALIZED_GIT_ENV_VARS_V2` and is honored by this host's Git | CONFIRMED — `GIT_CONFIG_PARAMETERS preserved: True`; a `core.hooksPath` injected through it took effect |
| `03_clean_filter_masks_dirty_source.sh` | A repository-local `filter.*.clean` executes during a bounded-path diff and can make materially different worktree bytes read as clean against `HEAD` | CONFIRMED — clean filter executed, `git diff --name-only HEAD -- app` returned empty while the file on disk read `MALICIOUS_DIRTY_BYTES` |
| `04_assume_unchanged_defeats_identity.py` | `establish_toolrepo_source_identity_v2` trusts `git diff --name-only HEAD`, which `git update-index --assume-unchanged` suppresses | CONFIRMED — identity **PASSED**, returning `toolrepo_sha=c68a8b9a…`, while `app/agent_review/toolrepo_identity_v2.py` on disk read `# TAMPERED` as its last line |
| `05_lazy_fetch_executes_ext_helper.sh` | With a genuinely absent (not merely filtered) blob and a repository-local `remote.origin.url=ext::<helper>`, the disposable-worktree diff path triggers Git's lazy object fetch and executes the helper; `GIT_NO_LAZY_FETCH=1` is the causal control and is absent from `sealed_git_child_env_v2()` | CONFIRMED — helper executed without the env var, did not execute with `GIT_NO_LAZY_FETCH=1` set |
| `06_git_write_mutant_bypasses_oracle.sh` | `test_target_checkout_is_never_mutated` / `test_cli_has_no_filesystem_output_authority` never inspect `.git` contents, only `git status --porcelain` | CONFIRMED — a scratch-copy mutant that writes `.git/agent-review-mutant-marker` from inside `prepare_operational_review_v2` passes both tests unmodified (`2 passed`) |

Two things worth being explicit about, since they affected the reproduction:

- My first attempt at #6 used an in-tree (not scratch-copy) mutant and was
  confounded: it dirtied *this toolrepo's own* worktree, so the CLI refused
  for the unrelated reason `toolrepo_worktree_dirty` before the oracle
  question was even reached. Lane C's scratch-copy method (clone this repo,
  check out the exact head, inject the mutant, commit it in the scratch
  clone) is the one that actually isolates the question, and is what these
  scripts use.
- `05` required a genuine loose-object deletion. An initial attempt using a
  local `--filter=blob:none` clone over `file://` transport did not leave
  any object actually missing (the local transport fetched everything
  eagerly), which would have been a false negative, not evidence of a
  closed vector. The script deletes the loose object directly to construct
  a real missing-object precondition.

None of these six scripts, nor the lane findings, were acted on by
modifying `app/` or `tests/`. Per the grant governing this round
(`STOP_GIT_AUTHORITY_MODEL_NOT_CONVERGING`, recorded in the checkpoint),
PR #274 receives no further implementation changes; these are preserved as
input to the successor slice's redesign.
