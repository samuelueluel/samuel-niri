#!/usr/bin/env bash
# Restore the retained Ubuntu 24 Lemonade container. No volumes are deleted.
set -Eeuo pipefail

PRODUCTION_CONTAINER="${PRODUCTION_CONTAINER:-lemonade}"
ROLLBACK_CONTAINER="${ROLLBACK_CONTAINER:-lemonade-ubuntu24-rollback}"
PRESERVED_NEW_CONTAINER="${PRESERVED_NEW_CONTAINER:-lemonade-ubuntu26-promoted-$(date +%Y%m%d-%H%M%S)}"
PORT="${PORT:-13305}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
exists() { podman container exists "$1"; }
running() { [[ "$(podman inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == true ]]; }

health_json() { curl -fsS --max-time 15 "http://127.0.0.1:$1/api/v1/health"; }

wait_health() {
  local i data
  for i in $(seq 1 45); do
    if data=$(health_json "$PORT") && python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("status") != "ok" or d.get("version") != "10.8.0":
    raise SystemExit(1)
if d.get("model_loaded") is not None or d.get("all_models_loaded"):
    raise SystemExit(2)
' <<<"$data"; then
      log "Ubuntu 24 Lemonade 10.8.0 is healthy on port $PORT"
      return 0
    else
      rc=$?
      [[ "$rc" == 2 ]] && die "restored server has a model loaded; inspect before retrying"
    fi
    sleep 2
  done
  return 1
}

main() {
  command -v podman >/dev/null || die 'podman not found'
  command -v curl >/dev/null || die 'curl not found'
  command -v python3 >/dev/null || die 'python3 not found'
  exists "$ROLLBACK_CONTAINER" || die "rollback container not found: $ROLLBACK_CONTAINER"
  ! exists "$PRESERVED_NEW_CONTAINER" || die "preservation name already exists: $PRESERVED_NEW_CONTAINER"
  ! exists "$PRODUCTION_CONTAINER" || true

  if exists "$PRODUCTION_CONTAINER"; then
    log "Stopping Ubuntu 26 production container"
    podman update --restart=no "$PRODUCTION_CONTAINER" >/dev/null
    podman stop --time 15 "$PRODUCTION_CONTAINER" >/dev/null
    podman rename "$PRODUCTION_CONTAINER" "$PRESERVED_NEW_CONTAINER"
  fi

  log "Restoring Ubuntu 24 production container"
  podman rename "$ROLLBACK_CONTAINER" "$PRODUCTION_CONTAINER"
  podman update --restart=always "$PRODUCTION_CONTAINER" >/dev/null
  podman start "$PRODUCTION_CONTAINER" >/dev/null
  wait_health || die "Ubuntu 24 rollback health check failed"
  log "Ubuntu 24 restored; Ubuntu 26 retained as $PRESERVED_NEW_CONTAINER"
}

main "$@"
