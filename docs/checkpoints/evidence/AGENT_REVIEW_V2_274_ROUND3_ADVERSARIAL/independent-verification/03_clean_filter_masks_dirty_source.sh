#!/bin/sh
# Reproduces: a repository-local filter.<driver>.clean executes during
# `git diff --name-only HEAD -- <bounded paths>` (the check
# establish_toolrepo_source_identity_v2 uses to prove the worktree matches
# HEAD), and can make bytes that are DIFFERENT on disk from what is
# committed read as identical -- because `clean` runs on the WORKTREE side
# of the comparison and can emit whatever it wants, including the
# committed bytes verbatim.
#
# This script demonstrates the mechanism directly against `git diff`, the
# same primitive the identity authority relies on. See
# 04_assume_unchanged_defeats_identity.py for the same class of defect
# reproduced through the real `establish_toolrepo_source_identity_v2`.
#
# Usage: ./03_clean_filter_masks_dirty_source.sh
set -eu
T=$(mktemp -d /tmp/clean-filter-mask.XXXXXX)
cd "$T"
mkdir -p tr/app
cd tr
git init -q .
git config user.email a@b.c && git config user.name t
echo "VALUE = 'benign'" > app/victim.py
git add -A && git commit -qm base >/dev/null
echo "committed bytes:  $(git show HEAD:app/victim.py)"

echo "VALUE = 'MALICIOUS_DIRTY_BYTES'" > app/victim.py
echo "worktree bytes:   $(cat app/victim.py)"

echo "-- WITHOUT a clean filter: does git diff see it dirty? (sanity check) --"
git diff --name-only HEAD -- app

rm -f "$T/CLEAN_RAN"
git config filter.evil.clean "sh -c 'touch $T/CLEAN_RAN; echo \"VALUE = '\''benign'\''\"'"
printf 'victim.py filter=evil\n' > app/.gitattributes
git add app/.gitattributes >/dev/null 2>&1
git commit -qm attrs >/dev/null 2>&1

echo "-- WITH the clean filter active: git diff --name-only HEAD -- app --"
OUT=$(git -c core.hooksPath=/dev/null diff --name-only HEAD -- app)
echo "  [${OUT}]  (empty means: dirty source was hidden)"

if [ -f "$T/CLEAN_RAN" ] && [ -z "$OUT" ]; then
    echo "CONFIRMED: clean filter executed and hid materially dirty tracked source"
else
    echo "not reproduced"
fi
echo "actual worktree bytes remain: $(cat app/victim.py)"
