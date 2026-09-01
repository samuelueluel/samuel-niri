#!/usr/bin/env bash
# Build and validate an isolated Ubuntu 26.04 GPU/Lemonade candidate.
# This script never mutates the production lemonade container or volumes.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENGINE_SCRIPT="$SCRIPT_DIR/llm-ubuntu26-build-engines.sh"
CONTAINERFILE="$SCRIPT_DIR/lemonade-ubuntu26/Containerfile"

LEMONADE_SRC="${LEMONADE_SRC:-$HOME/.local/src/lemonade-v10.8.0}"
LEMONADE_REPO="${LEMONADE_REPO:-https://github.com/lemonade-sdk/lemonade.git}"
LEMONADE_REF="${LEMONADE_REF:-v10.8.0}"
UBUNTU_IMAGE="${UBUNTU_IMAGE:-docker.io/library/ubuntu@sha256:7c2884fd32770fc6c173b78e0dc2278a2851d89f5447919edbc45475ac55dd6a}"
ROCM_IMAGE="${ROCM_IMAGE:-docker.io/rocm/dev-ubuntu-26.04@sha256:372e5efcd5c68bed44c4e3d13a57648634f669a34e7e8b4b756985c4e4fa4cdf}"
RUNTIME_IMAGE="${RUNTIME_IMAGE:-localhost/lemonade-server:ubuntu26-v108-candidate}"
CANDIDATE_CONTAINER="${CANDIDATE_CONTAINER:-lemonade26-candidate}"
ENGINE_VOLUME="${ENGINE_VOLUME:-lemonade26-llama}"
RECIPE_VOLUME="${RECIPE_VOLUME:-lemonade26-v108-recipe}"
CACHE_VOLUME="${CACHE_VOLUME:-lemonade26-v108-cache}"
SOURCE_RECIPE_VOLUME="${SOURCE_RECIPE_VOLUME:-lemonade-recipe}"
SOURCE_CACHE_VOLUME="${SOURCE_CACHE_VOLUME:-lemonade-cache}"
PORT="${PORT:-13310}"
JOBS="${JOBS:-64}"
DRIVER_URL="${DRIVER_URL:-https://github.com/Nathanw1014/strix-halo-llamacpp/releases/download/v0.7.3/strix-halo-llamacpp-vulkan-portable.tar.gz}"

WRAPPER="$SCRIPT_DIR/llama-dispatch-wrapper.sh"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_file() { [[ -f "$1" ]] || die "required file not found: $1"; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }
volume_mountpoint() { podman volume inspect --format '{{.Mountpoint}}' "$1"; }
volume_exists() { podman volume exists "$1"; }
container_running() { [[ "$(podman inspect --format '{{.State.Running}}' "$CANDIDATE_CONTAINER" 2>/dev/null || true)" == true ]]; }

ensure_lemonade_source() {
  if [[ ! -e "$LEMONADE_SRC/.git" ]]; then
    [[ ! -e "$LEMONADE_SRC" ]] || die "Lemonade source path exists but is not a checkout: $LEMONADE_SRC"
    log "Cloning Lemonade $LEMONADE_REF into $LEMONADE_SRC"
    mkdir -p "$(dirname "$LEMONADE_SRC")"
    git clone --depth=1 --branch "$LEMONADE_REF" "$LEMONADE_REPO" "$LEMONADE_SRC"
  fi

  local actual expected
  actual=$(git -C "$LEMONADE_SRC" rev-parse HEAD)
  expected=$(git -C "$LEMONADE_SRC" rev-parse "$LEMONADE_REF^{commit}" 2>/dev/null || true)
  if [[ -z "$expected" ]]; then
    git -C "$LEMONADE_SRC" fetch --depth=1 origin \
      "refs/tags/$LEMONADE_REF:refs/tags/$LEMONADE_REF"
    expected=$(git -C "$LEMONADE_SRC" rev-parse "$LEMONADE_REF^{commit}")
  fi
  if [[ "$actual" != "$expected" ]]; then
    [[ -z "$(git -C "$LEMONADE_SRC" status --porcelain)" ]] || \
      die "Lemonade checkout has uncommitted changes and is not pinned to $LEMONADE_REF"
    log "Checking out pinned Lemonade revision $LEMONADE_REF"
    git -C "$LEMONADE_SRC" checkout --detach "$LEMONADE_REF"
    actual=$(git -C "$LEMONADE_SRC" rev-parse HEAD)
  fi
  [[ "$actual" == "$expected" ]] || die "Lemonade checkout is not pinned to $LEMONADE_REF (actual $actual)"
}

check_inputs() {
  require_cmd podman
  require_cmd git
  require_file "$ENGINE_SCRIPT"
  require_file "$CONTAINERFILE"
  require_file "$WRAPPER"
  ensure_lemonade_source
}

build_runtime() {
  check_inputs
  local revision
  revision=$(git -C "$LEMONADE_SRC" rev-parse HEAD)
  log "Building $RUNTIME_IMAGE from Lemonade $LEMONADE_REF on Ubuntu 26.04"
  podman build --pull=never \
    --build-arg "UBUNTU_IMAGE=$UBUNTU_IMAGE" \
    --label "org.opencontainers.image.revision=$revision" \
    --label "org.opencontainers.image.base.name=$UBUNTU_IMAGE" \
    --label 'org.opencontainers.image.lemonade-version=10.8.0' \
    --label 'org.opencontainers.image.npu=disabled' \
    -t "$RUNTIME_IMAGE" \
    -f "$CONTAINERFILE" \
    "$LEMONADE_SRC"
}

prepare_recipe() {
  volume_exists "$SOURCE_RECIPE_VOLUME" || die "source recipe volume missing: $SOURCE_RECIPE_VOLUME"
  volume_exists "$RECIPE_VOLUME" || podman volume create "$RECIPE_VOLUME" >/dev/null
  local src dest fs
  src=$(volume_mountpoint "$SOURCE_RECIPE_VOLUME")
  dest=$(volume_mountpoint "$RECIPE_VOLUME")

  if [[ ! -f "$dest/.ubuntu26-v108-recipe" ]]; then
    fs=$(stat -f -c %T "$src")
    [[ "$fs" == btrfs ]] || die "refusing an ordinary copy of the recipe volume (filesystem is $fs, not btrfs)"
    log "Creating Btrfs reflink clone $RECIPE_VOLUME from $SOURCE_RECIPE_VOLUME"
    find "$dest" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    cp -a --reflink=always --no-preserve=context "$src/." "$dest/"
    chmod -R u+rwX,go+rX "$dest"
    {
      printf 'source_volume=%s\n' "$SOURCE_RECIPE_VOLUME"
      printf 'source_recipe_hash=%s\n' "$(sha256sum "$src/recipe_options.json" | awk '{print $1}')"
      printf 'method=btrfs-reflink\n'
      printf 'lemonade_ref=%s\n' "$LEMONADE_REF"
    } > "$dest/.ubuntu26-v108-recipe"
  else
    log "Using existing isolated recipe volume $RECIPE_VOLUME"
  fi
}

prepare_cache() {
  volume_exists "$SOURCE_CACHE_VOLUME" || die "source cache volume missing: $SOURCE_CACHE_VOLUME"
  volume_exists "$CACHE_VOLUME" || podman volume create "$CACHE_VOLUME" >/dev/null
  local src dest fs
  src=$(volume_mountpoint "$SOURCE_CACHE_VOLUME")
  dest=$(volume_mountpoint "$CACHE_VOLUME")
  if [[ ! -f "$dest/.ubuntu26-reflink-clone" ]]; then
    fs=$(stat -f -c %T "$src")
    [[ "$fs" == btrfs ]] || die "refusing a full ordinary copy of the 833 GiB cache (filesystem is $fs, not btrfs)"
    log "Creating Btrfs reflink clone $CACHE_VOLUME from $SOURCE_CACHE_VOLUME"
    find "$dest" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    cp -a --reflink=always --no-preserve=context "$src/." "$dest/"
    printf 'source_volume=%s\nmethod=btrfs-reflink\n' "$SOURCE_CACHE_VOLUME" > "$dest/.ubuntu26-reflink-clone"
  else
    log "Using existing Btrfs reflink cache $CACHE_VOLUME"
  fi
}

prepare_volumes() {
  prepare_recipe
  prepare_cache
  volume_exists "$ENGINE_VOLUME" || podman volume create "$ENGINE_VOLUME" >/dev/null
}

build_engines() {
  check_inputs
  prepare_volumes
  log "Building candidate engines in $ENGINE_VOLUME with pinned ROCm image"
  IMAGE="$ROCM_IMAGE" VOLUME="$ENGINE_VOLUME" JOBS="$JOBS" DRIVER_URL="$DRIVER_URL" ONLY=all \
    "$ENGINE_SCRIPT"
}

start_candidate() {
  require_file "$CONTAINERFILE"
  volume_exists "$ENGINE_VOLUME" || die "candidate engine volume missing: $ENGINE_VOLUME"
  volume_exists "$RECIPE_VOLUME" || die "candidate recipe volume missing: $RECIPE_VOLUME"
  volume_exists "$CACHE_VOLUME" || die "candidate cache volume missing: $CACHE_VOLUME"
  podman image exists "$RUNTIME_IMAGE" || die "candidate runtime image missing: $RUNTIME_IMAGE"

  if container_running; then
    log "$CANDIDATE_CONTAINER is already running"
    return
  fi
  podman rm -f "$CANDIDATE_CONTAINER" >/dev/null 2>&1 || true
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq ":${PORT}$"; then
    die "candidate port $PORT is already in use; choose PORT=<free-port>"
  fi

  log "Starting isolated candidate $CANDIDATE_CONTAINER on 127.0.0.1:$PORT"
  podman run -d \
    --name "$CANDIDATE_CONTAINER" \
    --restart=no \
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
    "$RUNTIME_IMAGE" \
    ./lemond /root/.cache/lemonade --port "$PORT" --host 127.0.0.1 >/dev/null
  sleep 4
  container_running || { podman logs "$CANDIDATE_CONTAINER" >&2; die "candidate container exited"; }
}

install_wrapper() {
  container_running || die "candidate container is not running"
  log "Installing dispatch wrapper into candidate engine volume"
  podman cp "$WRAPPER" "$CANDIDATE_CONTAINER:/tmp/llama-dispatch-wrapper.sh"
  podman exec "$CANDIDATE_CONTAINER" sh -lc '
    install -m755 /tmp/llama-dispatch-wrapper.sh /opt/lemonade/llama/llama-server
    install -m755 /tmp/llama-dispatch-wrapper.sh /opt/lemonade/llama/vulkan/bin/llama-server
    sha256sum /opt/lemonade/llama/llama-server /opt/lemonade/llama/vulkan/bin/llama-server
  '
}

validate_runtime() {
  container_running || die "candidate container is not running"
  log "Validating candidate runtime"
  podman exec "$CANDIDATE_CONTAINER" sh -lc '. /etc/os-release; printf "%s %s\\n" "$PRETTY_NAME" "$VERSION_ID"; ./lemond --version'
  curl -fsS --max-time 20 "http://127.0.0.1:$PORT/api/v1/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="ok"; assert d.get("version")=="10.8.0"; print(json.dumps({k:d.get(k) for k in ("status","version","model_loaded")}))'
  curl -fsS --max-time 20 "http://127.0.0.1:$PORT/api/v1/models/Qwen3.8-27B-Q4" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("recipe_options",{}).get("llamacpp_args"); print("recipe_options loaded")'
}

validate_binaries() {
  volume_exists "$ENGINE_VOLUME" || die "candidate engine volume missing: $ENGINE_VOLUME"
  log "Validating candidate ELF dependencies"
  podman run --rm --user root \
    -v "$ENGINE_VOLUME:/opt/lemonade/llama:ro,z" \
    "$RUNTIME_IMAGE" \
    bash -euc '
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -qq binutils >/dev/null
      for spec in \
        "nathan|/opt/lemonade/llama/vulkan/bin/llama-server-real|/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver" \
        "laurent|/opt/lemonade/llama/vulkan-laurent/bin/llama-server|/opt/lemonade/llama/vulkan-laurent/bin:/opt/lemonade/llama/vulkan-laurent/driver" \
        "gaetan|/opt/lemonade/llama/rocm/llama-server|/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/lib" \
        "ciru|/opt/lemonade/llama/ciru/llama-server|/opt/lemonade/llama/ciru:/opt/lemonade/llama/ciru/lib"; do
        IFS="|" read -r name bin libs <<< "$spec"
        test -x "$bin"
        echo "[$name] $(readelf --version-info "$bin" 2>/dev/null | grep -oE "GLIBC_[0-9.]+" | sort -Vu | tail -1 || true)"
        LD_LIBRARY_PATH="$libs" ldd "$bin" | tee "/tmp/$name-ldd"
        ! grep -q "not found" "/tmp/$name-ldd"
      done
      test -s /opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json
      test -s /opt/lemonade/llama/vulkan-laurent/driver/radeon_icd.x86_64.json
    '
}

validate_wrappers() {
  container_running || die "candidate container is not running"
  log "Validating all four dispatch routes"
  podman exec "$CANDIDATE_CONTAINER" bash -euc '
    for spec in "nathan|--force-vulkan" "laurent|--force-laurent" "gaetan|--force-rocm" "ciru|--force-ciru"; do
      IFS="|" read -r name force <<< "$spec"
      timeout 60s /opt/lemonade/llama/llama-server "$force" --help >/tmp/$name-help
      grep -qiE "usage|options|llama" /tmp/$name-help
      echo "$name: ok"
    done
    sha256sum /opt/lemonade/llama/llama-server /opt/lemonade/llama/vulkan/bin/llama-server /root/.cache/lemonade/bin/llamacpp/vulkan/llama-server
  '
}

validate() {
  validate_runtime
  validate_binaries
  validate_wrappers
  cat <<EOF
Candidate infrastructure passed runtime/ELF/wrapper validation.
Candidate API: http://127.0.0.1:$PORT/api/v1
Next model gates (run from the host):
  llm-bench --base-url http://127.0.0.1:$PORT/api/v1 --models Qwen3.8-27B-Q4 --suite prose --depths 0 --runs 1 --no-power --yes
  llm-bench --base-url http://127.0.0.1:$PORT/api/v1 --models Qwen3.8-Flash-Next-ROCmFP4 --suite prose --depths 0 --runs 1 --no-power --yes
EOF
}

status() {
  printf 'runtime image: %s\n' "$RUNTIME_IMAGE"
  podman image exists "$RUNTIME_IMAGE" && podman image inspect "$RUNTIME_IMAGE" | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print("  id:",d.get("Id")); print("  digest:",d.get("Digest")); print("  labels:",d.get("Config",{}).get("Labels",{}))' || echo '  missing'
  printf 'candidate container: %s\n' "$CANDIDATE_CONTAINER"
  podman ps -a --filter "name=$CANDIDATE_CONTAINER" --format '  {{.Names}} {{.Status}}' || true
  printf 'candidate volumes:\n'
  for v in "$ENGINE_VOLUME" "$RECIPE_VOLUME" "$CACHE_VOLUME"; do
    if volume_exists "$v"; then printf '  %s %s\n' "$v" "$(volume_mountpoint "$v")"; else printf '  %s missing\n' "$v"; fi
  done
  if container_running; then curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/v1/health" || true; printf '\n'; fi
}

all() {
  build_runtime
  prepare_volumes
  build_engines
  start_candidate
  install_wrapper
  validate
}

ACTION="${1:-status}"
case "$ACTION" in
  runtime) build_runtime ;;
  prepare) prepare_volumes ;;
  build|engines) build_engines ;;
  start|run) start_candidate; install_wrapper ;;
  validate) validate ;;
  status) status ;;
  all) all ;;
  stop) podman rm -f "$CANDIDATE_CONTAINER" >/dev/null 2>&1 || true ;;
  *) die "usage: $0 {runtime|prepare|build|start|validate|status|all|stop}" ;;
esac
