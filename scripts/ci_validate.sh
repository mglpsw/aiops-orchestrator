#!/usr/bin/env bash
# Explicit ownership: repository integrity, generated gates, or pytest lanes.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
MODE="${1:---all}"
[[ $# -le 1 ]] || { echo 'One validation mode expected' >&2; exit 2; }
case "$MODE" in
  --repository|--generated|--static|--unit|--requires-network|--all) ;;
  *) echo 'Usage: ci_validate.sh [--repository|--generated|--static|--unit|--requires-network|--all]' >&2; exit 2 ;;
esac
repository() {
  echo '[static] shell syntax'
  while IFS= read -r -d '' script; do bash -n "$script"; done < <(find scripts -name '*.sh' -print0)
  bash scripts/validate_actions_catalog.sh
  echo '[static] compose syntax (no daemon)'
  docker compose version >/dev/null
  # Compose also references ../.env as a service env_file. Render an isolated
  # copy with example values; never create/overwrite the checkout's .env.
  COMPOSE_TMP="$(mktemp -d)"
  trap 'rm -rf "$COMPOSE_TMP"' EXIT
  mkdir "$COMPOSE_TMP/deploy"
  cp .env.example "$COMPOSE_TMP/.env"
  cp deploy/docker-compose.yml deploy/docker-compose.bluegreen.yml "$COMPOSE_TMP/deploy/"
  docker compose --env-file "$COMPOSE_TMP/.env" -p aiops-orchestrator -f "$COMPOSE_TMP/deploy/docker-compose.yml" config --quiet
  docker compose --env-file "$COMPOSE_TMP/.env" -p aiops-orchestrator -f "$COMPOSE_TMP/deploy/docker-compose.yml" -f "$COMPOSE_TMP/deploy/docker-compose.bluegreen.yml" config --quiet
  git diff --check
  git diff --check HEAD^ HEAD
  echo '[static] dangerous-pattern inventory (informational); enforcement: catalog + focused guardrail tests'
  grep -RInE 'shell=True|create_subprocess_shell|docker exec|ssh |git push|git pull|docker compose up|docker compose down|docker compose restart|systemctl restart|systemctl start|systemctl stop|systemctl reload' app docs tests config scripts README.md || [[ $? == 1 ]]
}
generated() {
  python3 scripts/export-agent-review-v2-schemas.py --check
  python3 scripts/run-agent-review-v2-evals.py --check
  python3 scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check
  python3 scripts/generate-ri-b0a-2-reuse-view.py --check
  python3 scripts/generate-target-pack-runtime-authority-view.py --check
  python3 scripts/materialize-benchmark-case.py --check
  python3 scripts/generate-benchmark-corpus-manifest.py --check
  python3 scripts/generate-benchmark-premanifest.py --check
  python3 scripts/generate-benchmark-identity-final.py --check
  python3 scripts/generate-benchmark-report.py --check
  python3 scripts/validate-benchmark-corpus-safety.py
}
case "$MODE" in
 --repository) repository ;;
 --generated) generated ;;
 --static) repository; generated ;;
 --unit) bash scripts/test.sh ;;
 --requires-network) bash scripts/test.sh --lane network --serial ;;
 --all) repository; generated; bash scripts/test.sh; bash scripts/test.sh --lane network --serial ;;
esac
