#!/usr/bin/env bash
# Promote the validated Ubuntu 26 Lemonade candidate without touching the
# production data volumes. The Ubuntu 24 container is retained for rollback.
set -Eeuo pipefail

OLD_CONTAINER="${OLD_CONTAINER:-lemonade}"
ROLLBACK_CONTAINER="${ROLLBACK_CONTAINER:-lemonade-ubuntu24-rollback}"
CANDIDATE_CONTAINER="${CANDIDATE_CONTAINER:-lemonade26-candidate}"
NEW_IMAGE="${NEW_IMAGE:-localhost/lemonade-server:ubuntu26-v108-candidate}"
ROLLBACK_IMAGE="${ROLLBACK_IMAGE:-localhost/lemonade-server:ubuntu24-v108-rollback}"
ENGINE_VOLUME="${ENGINE_VOLUME:-lemonade26-llama}"
RECIPE_VOLUME="${RECIPE_VOLUME:-lemonade26-v108-recipe}"
CACHE_VOLUME="${CACHE_VOLUME:-lemonade26-v108-cache}"
PORT="${PORT:-13305}"
CANDIDATE_PORT="${CANDIDATE_PORT:-13310}"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/ubuntu26-migration}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }
exists() { podman container exists "$1"; }
running() { [[ "$(podman inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == true ]]; }
volume_exists() { podman volume exists "$1"; }

health_json() {
  curl -fsS --max-time 15 "http://127.0.0.1:$1/api/v1/health"
}

assert_health() {
  local port="$1" expected_version="$2" data
  data=$(health_json "$port") || return 1
  python3 -c '
import json, sys
expected = sys.argv[1]
data = json.load(sys.stdin)
if data.get("status") != "ok" or data.get("version") != expected:
    raise SystemExit(1)
if data.get("model_loaded") is not None or data.get("all_models_loaded"):
    raise SystemExit(2)
' "$expected_version" <<<"$data"
}

wait_health() {
  local port="$1" expected="$2" i rc
  for i in $(seq 1 45); do
    if assert_health "$port" "$expected"; then
      log "Lemonade $expected is healthy on port $port"
      return 0
    else
      rc=$?
      [[ "$rc" == 2 ]] && die "server on port $port has a model loaded; refusing promotion check"
    fi
    sleep 2
  done
  return 1
}

main() {
  require podman
  require curl
  require python3
  mkdir -p "$STATE_DIR"
  local stamp="$({ date +%Y%m%d-%H%M%S; })"
  local state="$STATE_DIR/promotion-$stamp"
  mkdir -p "$state"

  exists "$OLD_CONTAINER" || die "production container not found: $OLD_CONTAINER"
  ! exists "$ROLLBACK_CONTAINER" || die "rollback container already exists: $ROLLBACK_CONTAINER"
  exists "$CANDIDATE_CONTAINER" || die "candidate container not found: $CANDIDATE_CONTAINER"
  podman image exists "$NEW_IMAGE" || die "candidate image not found: $NEW_IMAGE"
  for volume in "$ENGINE_VOLUME" "$RECIPE_VOLUME" "$CACHE_VOLUME"; do
    volume_exists "$volume" || die "candidate volume not found: $volume"
  done

  log "Checking candidate before promotion"
  wait_health "$CANDIDATE_PORT" 10.8.0 || die "candidate health check failed; production was not changed"
  podman inspect "$CANDIDATE_CONTAINER" > "$state/candidate-inspect.json" 2>/dev/null || true

  log "Recording and tagging the Ubuntu 24 rollback generation"
  podman inspect "$OLD_CONTAINER" > "$state/production-before.json"
  podman volume inspect lemonade-llama lemonade-cache lemonade-recipe lemonade-rocm > "$state/production-volumes-before.json"
  local old_image
  # Tag the exact image ID referenced by the retained container, not a
  # possibly moved floating image name.
  old_image=$(podman inspect --format '{{.Image}}' "$OLD_CONTAINER")
  podman tag "$old_image" "$ROLLBACK_IMAGE"
  podman image inspect "$ROLLBACK_IMAGE" > "$state/rollback-image.json"

  log "Checking that production has no loaded model"
  wait_health "$PORT" 10.8.0 || die "production is not clean/healthy; promotion aborted"

  log "Stopping only the candidate container"
  podman rm -f "$CANDIDATE_CONTAINER" >/dev/null 2>&1 || true

  log "Stopping and retaining the Ubuntu 24 production container"
  podman update --restart=no "$OLD_CONTAINER" >/dev/null
  podman stop --time 15 "$OLD_CONTAINER" >/dev/null
  podman rename "$OLD_CONTAINER" "$ROLLBACK_CONTAINER"

  log "Starting Ubuntu 26 as the production container"
  if ! podman run -d \
    --name "$OLD_CONTAINER" \
    --restart=always \
    --user root \
    --network=host \
    --device /dev/dri \
    --device /dev/kfd \
    --group-add video \
    --security-opt seccomp=unconfined \
    --ipc=host \
    -v "$CACHE_VOLUME:/root/.cache/huggingface:rw,z" \
    -v "$RECIPE_VOLUME:/root/.cache/lemonade:rw,z" \
    -v "$ENGINE_VOLUME:/opt/lemonade/llama:rw,z" \
    "$NEW_IMAGE" \
    ./lemond /root/.cache/lemonade --port "$PORT" --host 0.0.0.0 >/dev/null; then
    podman logs "$OLD_CONTAINER" > "$state/failed-promotion.log" 2>&1 || true
    printf '\nProduction container start failed. Run the rollback script if needed.\n'
    printf 'Run: %s/llm-ubuntu26-rollback.sh\n' "$(dirname "$0")"
    exit 1
  fi

  if ! wait_health "$PORT" 10.8.0; then
    podman logs "$OLD_CONTAINER" > "$state/failed-promotion.log" 2>&1 || true
    printf '\nPromotion health check failed. Production is unavailable until rollback.\n'
    printf 'Run: %s/llm-ubuntu26-rollback.sh\n' "$(dirname "$0")"
    exit 1
  fi

  podman inspect "$OLD_CONTAINER" > "$state/production-after.json"
  {
    printf 'rollback_container=%s\n' "$ROLLBACK_CONTAINER"
    printf 'rollback_image=%s\n' "$ROLLBACK_IMAGE"
    printf 'production_container=%s\n' "$OLD_CONTAINER"
    printf 'production_image=%s\n' "$NEW_IMAGE"
    printf 'engine_volume=%s\nrecipe_volume=%s\ncache_volume=%s\n' "$ENGINE_VOLUME" "$RECIPE_VOLUME" "$CACHE_VOLUME"
  } > "$STATE_DIR/last-promotion"
  log "Ubuntu 26 production container is healthy; Ubuntu 24 rollback retained"
  log "Promotion state: $state"
}

main "$@"
