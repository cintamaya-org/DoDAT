#!/usr/bin/env bash
set -euo pipefail

extract_services_with_ports() {
  local file="$1"
  awk '
    /^[[:space:]]{2}[a-zA-Z0-9_-]+:/ {
      svc=$1
      sub(":", "", svc)
      current=svc
    }
    /^[[:space:]]{4}ports:/ {
      print current
    }
  ' "$file" | sort -u
}

assert_allowed_ports_only() {
  local file="$1"
  shift
  local allowed=("$@")
  local actual
  actual="$(extract_services_with_ports "$file" || true)"

  if [[ -z "$actual" ]]; then
    echo "FAIL: no services expose ports in $file"
    exit 1
  fi

  while IFS= read -r svc; do
    [[ -z "$svc" ]] && continue
    local ok="0"
    for allowed_svc in "${allowed[@]}"; do
      if [[ "$svc" == "$allowed_svc" ]]; then
        ok="1"
        break
      fi
    done
    if [[ "$ok" != "1" ]]; then
      echo "FAIL: service '$svc' exposes ports in $file but is not in allowlist (${allowed[*]})"
      exit 1
    fi
  done <<< "$actual"
}

assert_pattern_present() {
  local file="$1"
  local pattern="$2"
  if ! grep -Eq -- "$pattern" "$file"; then
    echo "FAIL: pattern '$pattern' not found in $file"
    exit 1
  fi
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

assert_allowed_ports_only "docker-compose.scaling.dev.yml" "traefik"
assert_allowed_ports_only "deploy/docker-compose.prod.yml" "web"
assert_allowed_ports_only "deploy/docker-compose.test.yml" "web"

# Ensure segmentation networks are explicitly declared in the main hardened stacks.
assert_pattern_present "docker-compose.scaling.dev.yml" "^networks:"
assert_pattern_present "docker-compose.scaling.dev.yml" "^  edge:"
assert_pattern_present "docker-compose.scaling.dev.yml" "^  app:"
assert_pattern_present "docker-compose.scaling.dev.yml" "^  data:"
assert_pattern_present "deploy/docker-compose.base.yml" "^networks:"
assert_pattern_present "deploy/docker-compose.base.yml" "^  edge:"
assert_pattern_present "deploy/docker-compose.base.yml" "^  app:"

echo "PASS: network segmentation policy checks passed."
