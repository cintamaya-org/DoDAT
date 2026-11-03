#!/usr/bin/env bash
set -euo pipefail

service_block() {
  local file="$1"
  local service="$2"
  awk -v svc="$service" '
    BEGIN {in_block=0}
    $0 ~ "^  "svc":" {in_block=1; print; next}
    in_block && $0 ~ "^  [a-zA-Z0-9_-]+:" {exit}
    in_block {print}
  ' "$file"
}

assert_block_has() {
  local file="$1"
  local service="$2"
  local pattern="$3"
  local block
  block="$(service_block "$file" "$service")"
  if [[ -z "$block" ]]; then
    echo "FAIL: service '$service' not found in $file"
    exit 1
  fi
  if ! grep -Eq -- "$pattern" <<<"$block"; then
    echo "FAIL: '$service' in $file missing pattern: $pattern"
    exit 1
  fi
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Scaling stack: app runtimes must drop privileges.
assert_block_has "docker-compose.scaling.dev.yml" "web" "security_opt:"
assert_block_has "docker-compose.scaling.dev.yml" "web" "no-new-privileges:true"
assert_block_has "docker-compose.scaling.dev.yml" "web" "cap_drop:"
assert_block_has "docker-compose.scaling.dev.yml" "web" "- ALL"

assert_block_has "docker-compose.scaling.dev.yml" "worker" "security_opt:"
assert_block_has "docker-compose.scaling.dev.yml" "worker" "no-new-privileges:true"
assert_block_has "docker-compose.scaling.dev.yml" "worker" "cap_drop:"
assert_block_has "docker-compose.scaling.dev.yml" "worker" "- ALL"



# Deploy base stack: same profile for app + LikeC4 services.
assert_block_has "deploy/docker-compose.base.yml" "web" "security_opt:"
assert_block_has "deploy/docker-compose.base.yml" "web" "no-new-privileges:true"
assert_block_has "deploy/docker-compose.base.yml" "web" "cap_drop:"
assert_block_has "deploy/docker-compose.base.yml" "web" "- ALL"



python3 deploy/scripts/test_likec4_isolation.py

echo "PASS: least-privilege policy checks passed."
