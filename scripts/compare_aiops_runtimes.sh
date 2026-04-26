#!/usr/bin/env bash
set -euo pipefail

LEGACY_URL="${AIOPS_LEGACY_URL:-http://127.0.0.1:8000}"
NEXT_URL="${AIOPS_NEXT_URL:-http://127.0.0.1:8001}"
CONNECT_TIMEOUT="${AIOPS_COMPARE_CONNECT_TIMEOUT:-3}"
MAX_TIME="${AIOPS_COMPARE_MAX_TIME:-15}"
STRICT="${STRICT:-0}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

tmp_files=()
cleanup() {
  if [ "${#tmp_files[@]}" -gt 0 ]; then
    rm -f "${tmp_files[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

mkbody() {
  local file
  file="$(mktemp)"
  tmp_files+=("$file")
  printf '%s' "$file"
}

http_probe() {
  local method="$1"
  local base_url="$2"
  local path="$3"
  local payload="${4:-}"
  local auth_mode="${5:-none}"
  local body_file
  body_file="$(mkbody)"

  local curl_args=(
    --silent
    --show-error
    --connect-timeout "$CONNECT_TIMEOUT"
    --max-time "$MAX_TIME"
    -o "$body_file"
    -w '%{http_code} %{time_total}'
    -X "$method"
    -H 'Accept: application/json'
  )

  if [ -n "$payload" ]; then
    curl_args+=(-H 'Content-Type: application/json' --data-raw "$payload")
  fi

  case "$auth_mode" in
    invalid)
      curl_args+=(-H 'Authorization: Bearer intentionally-invalid-token')
      ;;
    valid)
      if [ -n "${AIOPS_COMPARE_TOKEN:-}" ]; then
        curl_args+=(-H "Authorization: Bearer ${AIOPS_COMPARE_TOKEN}")
      fi
      ;;
  esac

  local out status time_total
  if out="$(curl "${curl_args[@]}" "${base_url}${path}" 2>/dev/null)"; then
    :
  else
    out="000 0"
  fi
  read -r status time_total <<<"$out"
  printf '%s\t%s\t%s\n' "$status" "$time_total" "$body_file"
}

classify() {
  local label="$1"
  local runtime="$2"
  local status="$3"
  local time_total="$4"
  local body_file="$5"
  python3 - "$label" "$runtime" "$status" "$time_total" "$body_file" <<'PY'
import json
import pathlib
import sys

label, runtime, status_s, time_s, body_path = sys.argv[1:]
status = int(status_s)
time_total = float(time_s)

def emit(level: str, detail: str) -> None:
    print(f"{level}\t{detail}")

if status == 0:
    emit("FAIL", f"{runtime} {label}: request failed")
    raise SystemExit(0)

try:
    body_text = pathlib.Path(body_path).read_text(encoding="utf-8", errors="replace").strip()
except FileNotFoundError:
    body_text = ""

if label == "health":
    if status != 200:
        emit("FAIL", f"{runtime} health: HTTP {status}")
        raise SystemExit(0)
    try:
        data = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        emit("FAIL", f"{runtime} health: non-JSON body")
        raise SystemExit(0)
    health = data.get("status")
    if isinstance(health, str) and health.lower() in {"ok", "healthy"}:
        emit("PASS", f"{runtime} health: status={health!r}, {time_total:.3f}s")
    else:
        emit("WARN", f"{runtime} health: unexpected payload keys={sorted(data.keys())}")
    raise SystemExit(0)

if label == "ready":
    if status != 200:
        emit("FAIL", f"{runtime} ready: HTTP {status}")
        raise SystemExit(0)
    try:
        data = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        emit("FAIL", f"{runtime} ready: non-JSON body")
        raise SystemExit(0)
    if data.get("ready") is True:
        checks = data.get("checks")
        if isinstance(checks, dict):
            emit("PASS", f"{runtime} ready: ready=True, checks={sorted(checks.keys())}, {time_total:.3f}s")
        else:
            emit("WARN", f"{runtime} ready: ready=True but checks payload differs, keys={sorted(data.keys())}")
    else:
        emit("FAIL", f"{runtime} ready: ready={data.get('ready')!r}")
    raise SystemExit(0)

if label == "metrics":
    if status != 200:
        emit("FAIL", f"{runtime} metrics: HTTP {status}")
        raise SystemExit(0)
    if "# HELP" in body_text and "# TYPE" in body_text:
        emit("PASS", f"{runtime} metrics: Prometheus text, {time_total:.3f}s")
    else:
        emit("WARN", f"{runtime} metrics: 200 without Prometheus markers")
    raise SystemExit(0)

if label == "diagnose":
    if status == 404:
        emit("WARN", f"{runtime} diagnose: endpoint unavailable (404)")
        raise SystemExit(0)
    if status != 200:
        emit("FAIL", f"{runtime} diagnose: HTTP {status}")
        raise SystemExit(0)
    try:
        data = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        emit("FAIL", f"{runtime} diagnose: non-JSON body")
        raise SystemExit(0)
    required = {"status", "severity", "summary"}
    if required.issubset(data.keys()):
        emit("PASS", f"{runtime} diagnose: keys={sorted(required)}, {time_total:.3f}s")
    else:
        emit("WARN", f"{runtime} diagnose: missing basic keys, keys={sorted(data.keys())}")
    raise SystemExit(0)

if label == "auth-invalid":
    if status in {401, 403}:
        emit("PASS", f"{runtime} auth-invalid: rejected with HTTP {status}, {time_total:.3f}s")
    elif status == 200:
        emit("WARN", f"{runtime} auth-invalid: token ignored or auth disabled, {time_total:.3f}s")
    else:
        emit("WARN", f"{runtime} auth-invalid: HTTP {status}")
    raise SystemExit(0)

if label == "response-time":
    if status != 200:
        emit("FAIL", f"{runtime} response-time: HTTP {status}")
    elif time_total > 5.0:
        emit("WARN", f"{runtime} response-time: slow at {time_total:.3f}s")
    else:
        emit("PASS", f"{runtime} response-time: {time_total:.3f}s")
    raise SystemExit(0)

emit("WARN", f"{runtime} {label}: no classifier")
PY
}

record_result() {
  local result="$1"
  case "$result" in
    PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    WARN) WARN_COUNT=$((WARN_COUNT + 1)) ;;
    FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
  esac
}

run_pair() {
  local label="$1"
  local legacy_method="$2"
  local next_method="$3"
  local legacy_path="$4"
  local next_path="$5"
  local legacy_payload="${6:-}"
  local next_payload="${7:-}"
  local auth_mode="${8:-none}"

  local legacy_status legacy_time legacy_body
  local next_status next_time next_body
  read -r legacy_status legacy_time legacy_body < <(http_probe "$legacy_method" "$LEGACY_URL" "$legacy_path" "$legacy_payload" "$auth_mode")
  read -r next_status next_time next_body < <(http_probe "$next_method" "$NEXT_URL" "$next_path" "$next_payload" "$auth_mode")

  local legacy_result legacy_detail next_result next_detail
  read -r legacy_result legacy_detail < <(classify "$label" "legacy" "$legacy_status" "$legacy_time" "$legacy_body")
  read -r next_result next_detail < <(classify "$label" "next" "$next_status" "$next_time" "$next_body")
  rm -f "$legacy_body" "$next_body"

  record_result "$legacy_result"
  record_result "$next_result"

  local overall="PASS"
  if [ "$legacy_result" = "FAIL" ] || [ "$next_result" = "FAIL" ]; then
    overall="FAIL"
  elif [ "$legacy_result" = "WARN" ] || [ "$next_result" = "WARN" ]; then
    overall="WARN"
  fi

  printf '%-16s %-5s %s | %s\n' "$label" "$overall" "$legacy_detail" "$next_detail"
  return 0
}

main() {
  echo "Comparing AIOps runtimes"
  echo "  legacy: $LEGACY_URL"
  echo "  next:   $NEXT_URL"
  echo

  local diagnose_payload analyze_payload
  diagnose_payload='{"target":"aiops-orchestrator","scope":"self","checks":["readiness"],"dry_run":true,"metadata":{"source":"bluegreen-compare"}}'
  analyze_payload='{"objective":"bluegreen-compare","queries":[],"signals":{"cpu":{"usage_percent":42}},"include_ollama":false}'

  run_pair "health" "GET" "GET" "/health" "/health"
  run_pair "ready" "GET" "GET" "/ready" "/ready"
  run_pair "metrics" "GET" "GET" "/metrics" "/metrics"
  run_pair "auth-invalid" "POST" "POST" "/v1/aiops/diagnose" "/v1/analyze" "$diagnose_payload" "$analyze_payload" "invalid"
  run_pair "response-time" "GET" "GET" "/ready" "/ready"

  echo
  echo "Summary: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
  fi
  if [ "$STRICT" = "1" ] && [ "$WARN_COUNT" -gt 0 ]; then
    echo "STRICT=1 enabled: WARN treated as failure" >&2
    exit 1
  fi
}

main "$@"
