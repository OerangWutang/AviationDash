#!/usr/bin/env bash
# Minimal post-deploy smoke check for the packaged API/frontend service.
set -euo pipefail

base_url="${ATLAS_ARGUS_SMOKE_BASE_URL:-${ATLAS_ARGUS_BASE_URL:-http://localhost:8100}}"
metrics_token="${ATLAS_ARGUS_SMOKE_METRICS_TOKEN:-${ATLAS_ARGUS_METRICS_TOKEN:-}}"
curl_args=(-fsS --connect-timeout 5 --max-time 20 --retry 3 --retry-delay 1 --retry-max-time 30)
metrics_header_file=""

cleanup() {
  if [[ -n "$metrics_header_file" ]]; then
    rm -f "$metrics_header_file"
  fi
}
trap cleanup EXIT

curl "${curl_args[@]}" "${base_url}/api/health" | grep -q '"status":"ok"'
if [[ -n "$metrics_token" ]]; then
  umask 077
  metrics_header_file="$(mktemp)"
  printf 'Authorization: Bearer %s\n' "$metrics_token" > "$metrics_header_file"
  curl "${curl_args[@]}" --header "@${metrics_header_file}" "${base_url}/api/metrics" \
    | grep -q 'atlas_argus_http_requests_total'
else
  curl "${curl_args[@]}" "${base_url}/api/metrics" \
    | grep -q 'atlas_argus_http_requests_total'
fi
curl "${curl_args[@]}" -D - -o /dev/null "${base_url}/api/health" | grep -qi 'x-content-type-options: nosniff'
curl "${curl_args[@]}" -D - -o /dev/null "${base_url}/api/health" | grep -qi 'x-frame-options: DENY'
curl "${curl_args[@]}" -D - -o /dev/null "${base_url}/" | grep -qi 'content-type: text/html'

if [[ "${ATLAS_ARGUS_SMOKE_PACKET_FLOW:-0}" == "1" ]]; then
  ATLAS_ARGUS_SMOKE_BASE_URL="$base_url" python3 scripts/packet_flow_smoke.py
fi

printf 'smoke ok: %s\n' "$base_url"
