#!/usr/bin/env bash
# EXPERIMENTAL ONLY -- reproduce the #301 S0 evidence on the declared AgentReview runtime.
#
#   bash run_py311.sh <aiops-orchestrator checkout> <toolrepo commit> <results dir>
#
# Everything runs inside an ephemeral container (image pinned by digest). Nothing on the host
# is installed or modified besides <results dir>. Network is used only to fetch the image and the
# lock's wheels (pip verifies the lock hashes; s0_deps.py re-verifies them from the bytes it reads).
#
# Roles inside the container:
#   root           provisions the TCB copy /opt/toolrepo-tcb (producer/launcher side, root-owned)
#                  and the experiment sources /exp (read-only mount)
#   runner (2000)  the same-UID actor: owns the mutable checkout /work/toolrepo, the venv
#                  /work/venv and every scratch directory; every experiment runs as this user
set -euo pipefail

SRC="$(cd "$1" && pwd)"
COMMIT="$2"
RESULTS="$(mkdir -p "$3" && cd "$3" && pwd)"
EXP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="python:3.11-bookworm@sha256:b99029c95d3d37fb1e4e76d287f7984373dca77c665885986e31b2c95260c13c"

[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "commit must be a full 40-hex sha" >&2; exit 2; }
BUNDLE_DIR="$(mktemp -d)"
trap 'rm -rf "$BUNDLE_DIR"' EXIT
# a throwaway bare repo fetches exactly COMMIT, so no ref is created in the source repository
git init -q --bare "$BUNDLE_DIR/tmp.git"
git -C "$BUNDLE_DIR/tmp.git" fetch -q "$SRC" "$COMMIT:refs/heads/s0-subject"
git -C "$BUNDLE_DIR/tmp.git" bundle create -q "$BUNDLE_DIR/toolrepo.bundle" refs/heads/s0-subject
chmod 755 "$BUNDLE_DIR" && chmod 644 "$BUNDLE_DIR/toolrepo.bundle"  # readable by the unprivileged runner

# /work is tmpfs: C3 admits only ext2/3/4 and tmpfs for materialization (overlay is refused by
# `_require_workspace_name_semantics_v2`); M exists in two fixtures, so the workspace must be admitted.
docker run --rm --tmpfs /work:rw,exec,size=3g \
  -v "$EXP":/exp:ro -v "$BUNDLE_DIR":/bundle:ro -v "$RESULTS":/results \
  -e COMMIT="$COMMIT" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$IMAGE" bash -euo pipefail -c '
    export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
    useradd -u 2000 -m runner
    # root-owned TCB copy of the toolrepo (producer/launcher code, and the git object store it reads)
    git clone -q -b s0-subject /bundle/toolrepo.bundle /opt/toolrepo-tcb
    git -C /opt/toolrepo-tcb -c advice.detachedHead=false checkout -q "$COMMIT"
    mkdir -p /work /work/out && chown runner:runner /work /work/out
    python3 -m pip download -q --require-hashes --no-deps -r /opt/toolrepo-tcb/requirements-agent-review.lock -d /work/wheels
    chown -R runner:runner /work/wheels
    R() { runuser -u runner -- env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/home/runner "$@"; }
    # the MUTABLE side: checkout + venv owned by the same UID that runs the experiments
    R git clone -q -b s0-subject /bundle/toolrepo.bundle /work/toolrepo
    R git -C /work/toolrepo -c advice.detachedHead=false checkout -q "$COMMIT"
    R bash /work/toolrepo/scripts/install-agent-review-toolrepo.sh /work/venv --toolrepo-sha "$COMMIT" >/work/out/install.log 2>&1
    PY=/usr/local/bin/python3.11
    {
      echo "{"
      echo "\"image\": \"python:3.11-bookworm@sha256:b99029c95d3d37fb1e4e76d287f7984373dca77c665885986e31b2c95260c13c\","
      echo "\"python\": \"$($PY -c "import sys;print(sys.version.split()[0])")\","
      echo "\"git\": \"$(git --version)\","
      echo "\"glibc\": \"$(ldd --version | head -1)\","
      echo "\"kernel\": \"$(uname -r)\","
      echo "\"work_filesystem\": \"$(stat -f -c %T /work)\","
      echo "\"yama_ptrace_scope\": \"$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo unavailable)\","
      echo "\"runner_uid\": 2000, \"toolrepo_commit\": \"$COMMIT\","
      echo "\"wheels_sha256\": \"$(cd /work/wheels && sha256sum *.whl | tr "\n" ";")\""
      echo "}"
    } > /work/out/environment.json
    run() { local name="$1"; shift; R timeout 900 "$PY" -I -S -B "/exp/$name.py" "$@" > "/work/out/$name.json" 2> "/work/out/$name.stderr" || echo "{\"harness_rc\": $?}" >> "/work/out/$name.rc"; }
    run exp_n1_auth /opt/toolrepo-tcb /work/scr-n1
    run exp_structure /opt/toolrepo-tcb /work/scr-struct
    run exp_capture_stability /opt/toolrepo-tcb /work/scr-cap "$PY"
    run exp_process_channel "$PY"
    run exp_bootstrap_env "$PY" /work/scr-boot
    run exp_resources /opt/toolrepo-tcb /work/scr-res "$PY"
    run exp_deps /work/scr-deps
    run exp_functional /work/toolrepo "$COMMIT" /work/wheels /work/venv \
        /opt/toolrepo-tcb/tests/agent_review/fixtures/v2/agent_escala /work/scr-func "$PY" /opt/toolrepo-tcb
    cp /work/out/* /results/ && chown -R "$HOST_UID:$HOST_GID" /results
  '
echo "results in $RESULTS"
