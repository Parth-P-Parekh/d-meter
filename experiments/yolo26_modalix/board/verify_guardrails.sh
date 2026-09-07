#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-snapshot}"
STATE="${2:-/tmp/yolo26-inference-demo/protected-services.before}"
mkdir -p "$(dirname "$STATE")"
capture() {
  for port in 5001 5003; do
    ss -ltnp "sport = :$port" 2>/dev/null | tail -n +2 | sed "s/^/port=$port /"
  done
}
health_codes() {
  for port in 5001 5003; do
    code="$(curl -sS -o /dev/null --max-time 5 -w '%{http_code}' "http://127.0.0.1:${port}/health" || true)"
    printf 'port=%s health_http=%s\n' "$port" "$code"
  done
}
case "$MODE" in
  snapshot)
    capture >"$STATE"
    grep -q 'port=5001 ' "$STATE" && grep -q 'port=5003 ' "$STATE" || {
      echo "Protected ports 5001 and 5003 are not both listening; stop." >&2; exit 1;
    }
    health_codes >"${STATE}.health"
    cat "$STATE"
    cat "${STATE}.health"
    ;;
  compare)
    current="${STATE}.current"
    capture >"$current"
    diff -u "$STATE" "$current"
    health_codes >"${current}.health"
    diff -u "${STATE}.health" "${current}.health"
    ;;
  *) echo "usage: $0 snapshot|compare [state-file]" >&2; exit 2 ;;
esac
