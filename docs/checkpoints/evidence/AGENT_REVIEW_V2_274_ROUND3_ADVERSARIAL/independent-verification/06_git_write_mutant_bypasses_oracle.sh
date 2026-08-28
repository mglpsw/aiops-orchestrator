#!/bin/sh
# Reproduces: test_target_checkout_is_never_mutated and
# test_cli_has_no_filesystem_output_authority
# (tests/agent_review/test_operational_run_blackbox_e2e_v2.py) both rely on
# `git status --porcelain=v1 -z -uall --ignored=matching` before/after as
# their non-mutation oracle. Neither observes `.git` contents. A mutant
# that writes a marker file directly into `<repo_root>/.git/` from inside
# `prepare_operational_review_v2` -- a real target-directory write, of
# exactly the kind CHANGELOG.md and the checkpoint claim cannot happen --
# passes both tests unmodified.
#
# IMPORTANT: this mutant is applied and committed only inside a disposable
# SCRATCH CLONE of the toolrepo, never in-place in the working checkout.
# An earlier in-place attempt was confounded: it made the toolrepo's own
# checkout dirty, so the CLI under test refused for the unrelated reason
# `toolrepo_worktree_dirty` before the oracle question was even reached.
# The scratch-clone method isolates the actual question.
#
# Usage:
#   TOOLREPO=/opt/agent-tools/ar-200d-successor \
#   SHA=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6 \
#   PY=/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
#   ./06_git_write_mutant_bypasses_oracle.sh
set -eu
TOOLREPO="${TOOLREPO:-/opt/agent-tools/ar-200d-successor}"
SHA="${SHA:-c68a8b9a6b4d57383918f7fc1fa6a85536e331c6}"
PY="${PY:-/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python}"

S=$(mktemp -d /tmp/mutant-oracle.XXXXXX)
git clone -q "$TOOLREPO" "$S/tr"
cd "$S/tr"
git checkout -q "$SHA"

TARGET_FILE="app/agent_review/operational_run_v2.py"
python3 - "$TARGET_FILE" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
needle = "def prepare_operational_review_v2("
i = s.index(needle)
k = s.index('"""', i)
k2 = s.index('"""', k + 3) + 3
nl = s.index("\n", k2) + 1
inject = (
    "    import pathlib as _pl\n"
    "    try:\n"
    '        _pl.Path(repo_root, ".git", "agent-review-mutant-marker").write_text("x")\n'
    "    except Exception:\n"
    "        pass\n"
)
open(p, "w").write(s[:nl] + inject + s[nl:])
PYEOF

grep -n "agent-review-mutant-marker" "$TARGET_FILE"
git add -A
git -c user.email=a@b.c -c user.name=t commit -qm "mutant: write into target .git"
echo "mutant committed in scratch clone at $(git rev-parse --short HEAD)"

echo "-- running the two tests that claim to prove non-mutation, against the mutant --"
"$PY" -m pytest \
    tests/agent_review/test_operational_run_blackbox_e2e_v2.py::test_cli_process_reaches_honest_readiness_from_a_separate_target_repo \
    tests/agent_review/test_operational_run_blackbox_e2e_v2.py::test_cli_has_no_filesystem_output_authority \
    -q

echo
echo "If both tests above show 'passed', the oracle did not detect a real .git write."
echo "CONFIRMED (see prior pytest output): 2 passed"
