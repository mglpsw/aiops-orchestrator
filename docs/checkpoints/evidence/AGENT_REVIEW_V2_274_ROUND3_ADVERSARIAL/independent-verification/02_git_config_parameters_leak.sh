#!/bin/sh
# Reproduces: GIT_CONFIG_PARAMETERS is absent from
# _NEUTRALIZED_GIT_ENV_VARS_V2 in _sealed_git_execution_v2.py, so
# sealed_git_child_env_v2() preserves it, and this host's Git (2.39.5)
# honors it -- an ambient, non-target-local vector for injecting arbitrary
# config (including an executable filter/hook config key) into every
# sealed Git invocation.
#
# Usage: TOOLREPO=/path/to/checkout ./02_git_config_parameters_leak.sh
set -eu
TOOLREPO="${TOOLREPO:-/opt/agent-tools/ar-200d-successor}"
PY="${PY:-/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python}"

echo "-- is GIT_CONFIG_PARAMETERS stripped by sealed_git_child_env_v2()? --"
PYTHONPATH="$TOOLREPO" "$PY" -c "
import os
os.environ['GIT_CONFIG_PARAMETERS'] = \"'filter.evil.clean=touch /tmp/should-not-run'\"
from app.agent_review._sealed_git_execution_v2 import sealed_git_child_env_v2
env = sealed_git_child_env_v2()
print('GIT_CONFIG_PARAMETERS preserved:', 'GIT_CONFIG_PARAMETERS' in env)
"

T=$(mktemp -d /tmp/gcp-leak.XXXXXX)
cd "$T"
git init -q r1 && cd r1
git config user.email a@b.c && git config user.name t
echo x > f && git add -A && git commit -qm b >/dev/null

echo "-- does this host's Git honor it? --"
RESULT=$(GIT_CONFIG_PARAMETERS="'core.hooksPath=/tmp/zzz'" git config --get core.hooksPath || true)
echo "core.hooksPath as seen by git: $RESULT"

if [ "$RESULT" = "/tmp/zzz" ]; then
    echo "CONFIRMED: GIT_CONFIG_PARAMETERS is preserved by the seal and honored by Git"
else
    echo "not reproduced"
fi
