#!/bin/sh
# Reproduces: with a genuinely absent (not merely `--filter`-hidden) blob
# and a repository-local remote configured as a promisor over `ext::`, the
# disposable-worktree diff path (`git diff <base>...<head>` against a
# checkout with that blob missing) triggers Git's own lazy object fetch,
# which executes the `ext::` transport helper. GIT_NO_LAZY_FETCH=1 is the
# documented Git control for this and is absent from
# sealed_git_child_env_v2().
#
# An earlier attempt at this reproduction used a local `--filter=blob:none`
# clone over `file://` transport and found NO missing objects -- local
# transport fetches eagerly regardless of the filter, which would have been
# a false negative (absence of evidence, not evidence of a closed vector).
# This script instead deletes the loose object directly to construct a
# genuinely missing-object precondition, matching what a real partial
# clone over a non-local transport produces.
#
# Usage: ./05_lazy_fetch_executes_ext_helper.sh
set -eu
T=$(mktemp -d /tmp/lazy-fetch.XXXXXX)
cd "$T"
git init -q origin && cd origin
git config user.email a@b.c && git config user.name t
i=0; while [ $i -lt 200 ]; do printf 'aaaa\n'; i=$((i+1)); done > big.txt
git add -A && git commit -qm base >/dev/null
BASE=$(git rev-parse HEAD)
i=0; while [ $i -lt 200 ]; do printf 'bbbb\n'; i=$((i+1)); done > big.txt
git add -A && git commit -qm head >/dev/null
HEAD2=$(git rev-parse HEAD)
BASEBLOB=$(git rev-parse "$BASE:big.txt")

cd "$T"
cp -r origin target
cd target
OBJ_DIR=$(echo "$BASEBLOB" | cut -c1-2)
OBJ_FILE=$(echo "$BASEBLOB" | cut -c3-)
OBJ=".git/objects/${OBJ_DIR}/${OBJ_FILE}"
if [ -f "$OBJ" ]; then
    rm -f "$OBJ"
    echo "deleted loose blob $BASEBLOB -- it is now genuinely absent"
else
    echo "blob was already packed; this reproduction needs a loose object" >&2
    exit 2
fi

cat > "$T/helper.sh" <<HLP
#!/bin/sh
touch $T/LAZY_HELPER_RAN
exec git upload-pack "$T/origin"
HLP
chmod +x "$T/helper.sh"
git config extensions.partialClone origin
git config remote.origin.promisor true
git config remote.origin.partialclonefilter blob:none
git config protocol.ext.allow always
git config remote.origin.url "ext::$T/helper.sh"

git cat-file -e "$BASEBLOB" 2>/dev/null \
    && echo "sanity: blob resolvable (would lazy-fetch)" \
    || echo "sanity: blob genuinely absent from this checkout"

run_diff() {
    label=$1; shift
    rm -f "$T/LAZY_HELPER_RAN"
    echo "-- $label --" >&2
    rc=0
    env "$@" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
        git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
        diff --no-ext-diff --no-textconv --binary "$BASE...$HEAD2" >/dev/null 2>&1 || rc=$?
    ran=no
    [ -f "$T/LAZY_HELPER_RAN" ] && ran=yes
    echo "   rc=$rc  helper_executed=$ran" >&2
    echo "$ran"
}

WITHOUT=$(run_diff "current sealed env (no GIT_NO_LAZY_FETCH)")
WITH=$(run_diff "with the proposed control" GIT_NO_LAZY_FETCH=1)

if [ "$WITHOUT" = "yes" ] && [ "$WITH" = "no" ]; then
    echo "CONFIRMED: lazy fetch executes the ext:: helper; GIT_NO_LAZY_FETCH=1 is the causal control"
else
    echo "not reproduced as expected (without=$WITHOUT with=$WITH)"
fi
