#!/usr/bin/env bash
set -Eeuo pipefail

# AMD ROCm 10.0.0-full Ubuntu 26.04 amd64 builder image.
# The image tag is intentionally used as the stable release track; the resolved
# image ID is recorded in BUILD-INFO by the caller's container runtime.
IMAGE="${IMAGE:-docker.io/rocm/dev-ubuntu-26.04:10.0.0-full}"
VOLUME="${VOLUME:-lemonade26-llama}"
JOBS="${JOBS:-64}"
ONLY="${ONLY:-all}"
FORCE="${FORCE:-false}"
TOOLCHAIN_ONLY="${TOOLCHAIN_ONLY:-false}"
case "$FORCE" in
  true|1|force|force=true|--force) FORCE="true" ;;
  *) FORCE="false" ;;
esac
case "$TOOLCHAIN_ONLY" in
  true|1|yes|toolchain-only) TOOLCHAIN_ONLY="true" ;;
  *) TOOLCHAIN_ONLY="false" ;;
esac

# Vulkan portable driver asset
DRIVER_URL="${DRIVER_URL:-https://github.com/Nathanw1014/strix-halo-llamacpp/releases/download/v0.7.3/strix-halo-llamacpp-vulkan-portable.tar.gz}"

# The engine builders are disposable. Keep the rolling-stable Shaderc toolchain in a
# separate persistent volume so Nathan and Laurent share one compiler without coupling
# the build to the host's Homebrew installation.
SHADERC_REPO="${SHADERC_REPO:-https://github.com/google/shaderc.git}"
TOOLCHAIN_VOLUME="${TOOLCHAIN_VOLUME:-lemonade26-toolchain}"
SHADERC_TAG=""

# Community Strix Halo fork. Resolve this once per invocation so the Vulkan and
# HIP builds below use the same immutable commit, even if master moves mid-run.
HALO_REPO="${HALO_REPO:-https://github.com/halo-box/strix-llama.cpp.git}"
HALO_REF="${HALO_REF:-refs/heads/master}"
HALO_BRANCH="${HALO_REF#refs/heads/}"
HALO_COMMIT=""

podman volume exists "$VOLUME" || podman volume create "$VOLUME" >/dev/null
podman volume exists "$TOOLCHAIN_VOLUME" || podman volume create "$TOOLCHAIN_VOLUME" >/dev/null

VOLUME_MOUNT=$(podman volume inspect "$VOLUME" --format '{{.Mountpoint}}' 2>/dev/null || true)
TOOLCHAIN_MOUNT=$(podman volume inspect "$TOOLCHAIN_VOLUME" --format '{{.Mountpoint}}' 2>/dev/null || true)

# Refresh the selected stable builder tag and capture its resolved identity. The tag
# remains the user-facing stable track; the ID/digest makes each engine artifact auditable.
echo "=== Builder image: $IMAGE ==="
podman pull "$IMAGE" >/dev/null
IMAGE_ID=$(podman image inspect "$IMAGE" --format '{{.Id}}')
IMAGE_DIGEST=$(podman image inspect "$IMAGE" --format '{{range .RepoDigests}}{{println .}}{{end}}' | head -1)

latest_shaderc_tag() {
  local refs tag
  refs=$(git ls-remote --tags --refs "$SHADERC_REPO" 'refs/tags/v*') || {
    echo "Unable to query Shaderc stable releases from $SHADERC_REPO" >&2
    return 1
  }
  tag=$(printf '%s\n' "$refs" |
    awk -F/ '$NF ~ /^v[0-9]+\.[0-9]+(\.[0-9]+)?$/ { print $NF }' |
    sort -V | tail -1)
  [[ -n "$tag" ]] || {
    echo "No stable Shaderc release tag found at $SHADERC_REPO" >&2
    return 1
  }
  printf '%s\n' "$tag"
}

ensure_shaderc() {
  local current_tag="" current_image="" current_image_id=""
  if [[ -f "$TOOLCHAIN_MOUNT/shaderc/current/VERSION" ]]; then
    current_tag=$(cat "$TOOLCHAIN_MOUNT/shaderc/current/VERSION")
  fi
  if [[ -f "$TOOLCHAIN_MOUNT/shaderc/current/BUILD_IMAGE" ]]; then
    current_image=$(cat "$TOOLCHAIN_MOUNT/shaderc/current/BUILD_IMAGE")
  fi
  if [[ -f "$TOOLCHAIN_MOUNT/shaderc/current/BUILD_IMAGE_ID" ]]; then
    current_image_id=$(cat "$TOOLCHAIN_MOUNT/shaderc/current/BUILD_IMAGE_ID")
  fi

  if [[ "$current_tag" == "$SHADERC_TAG" && "$current_image" == "$IMAGE" && "$current_image_id" == "$IMAGE_ID" && -x "$TOOLCHAIN_MOUNT/shaderc/current/glslc" ]]; then
    echo "=== Shaderc $SHADERC_TAG (cached) ==="
    echo "  glslc: $(cat "$TOOLCHAIN_MOUNT/shaderc/current/GLSLC_VERSION" 2>/dev/null || true)"
    return 0
  fi

  echo "=== Shaderc $SHADERC_TAG (latest stable; building) ==="
  podman run --rm --user 0:0 --name lemonade26-build-shaderc \
    -e "SHADERC_REPO=$SHADERC_REPO" \
    -e "SHADERC_TAG=$SHADERC_TAG" \
    -e "BUILD_IMAGE=$IMAGE" \
    -e "BUILD_IMAGE_ID=$IMAGE_ID" \
    -e "JOBS=$JOBS" \
    -v "$TOOLCHAIN_VOLUME:/toolchain:rw,z" \
    "$IMAGE" bash -euc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates git cmake ninja-build build-essential python3

  rm -rf /tmp/shaderc-src /tmp/shaderc-build
  git clone --branch "$SHADERC_TAG" --single-branch --depth=1 "$SHADERC_REPO" /tmp/shaderc-src
  (cd /tmp/shaderc-src && ./utils/git-sync-deps)
  cmake -S /tmp/shaderc-src -B /tmp/shaderc-build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DSHADERC_SKIP_TESTS=ON \
    -DSHADERC_SKIP_EXAMPLES=ON \
    -DSHADERC_SKIP_COPYRIGHT_CHECK=ON
  cmake --build /tmp/shaderc-build --target glslc_exe -j "$JOBS"
  test -x /tmp/shaderc-build/glslc/glslc

  mkdir -p /toolchain/shaderc
  stage="/toolchain/shaderc/$SHADERC_TAG.new"
  rm -rf "$stage"
  mkdir -p "$stage"
  install -m 0755 /tmp/shaderc-build/glslc/glslc "$stage/glslc"
  "$stage/glslc" --version | head -1 > "$stage/GLSLC_VERSION"
  printf "%s\\n" "$SHADERC_TAG" > "$stage/VERSION"
  printf "%s\\n" "$BUILD_IMAGE" > "$stage/BUILD_IMAGE"
  printf "%s\\n" "$BUILD_IMAGE_ID" > "$stage/BUILD_IMAGE_ID"
  rm -rf "/toolchain/shaderc/$SHADERC_TAG"
  mv "$stage" "/toolchain/shaderc/$SHADERC_TAG"

  link="/toolchain/shaderc/current.new"
  rm -f "$link"
  ln -s "$SHADERC_TAG" "$link"
  mv -Tf "$link" /toolchain/shaderc/current
'
}

if [[ "$TOOLCHAIN_ONLY" == "true" || "$ONLY" == all || "$ONLY" == nathan || "$ONLY" == laurent || "$ONLY" == halo || "$ONLY" == halo-vulkan ]]; then
  SHADERC_TAG=$(latest_shaderc_tag)
  ensure_shaderc
fi

if [[ "$TOOLCHAIN_ONLY" == "true" ]]; then
  echo "Shaderc toolchain ready: $TOOLCHAIN_MOUNT/shaderc/current/glslc"
  exit 0
fi

should_build() {
  local engine="$1"
  local repo="$2"
  local ref="$3"
  local out_sub="$4"
  local expected_shaderc_tag="${5:-}"
  local expected_image_id="${6:-}"

  if [[ "$FORCE" == "true" ]]; then
    return 0
  fi

  local remote_commit
  remote_commit=$(git ls-remote "$repo" "$ref" 2>/dev/null | awk '{print $1}')
  if [[ -z "$remote_commit" ]]; then
    return 0
  fi

  local local_commit=""
  if [[ -n "$VOLUME_MOUNT" && -f "$VOLUME_MOUNT/$out_sub/BUILD-INFO" ]]; then
    local_commit=$(grep '^commit=' "$VOLUME_MOUNT/$out_sub/BUILD-INFO" 2>/dev/null | cut -d= -f2 || true)
  fi

  if [[ -n "$local_commit" && "$local_commit" == "$remote_commit" ]]; then
    if [[ -n "$expected_shaderc_tag" ]]; then
      local local_shaderc_tag=""
      local_shaderc_tag=$(grep '^shaderc_tag=' "$VOLUME_MOUNT/$out_sub/BUILD-INFO" 2>/dev/null | cut -d= -f2 || true)
      if [[ "$local_shaderc_tag" != "$expected_shaderc_tag" ]]; then
        echo "=== $engine ==="
        echo "  Source commit is current, but Shaderc changed: ${local_shaderc_tag:-<unrecorded>} -> $expected_shaderc_tag"
        return 0
      fi
    fi
    if [[ -n "$expected_image_id" ]]; then
      local local_image_id=""
      local_image_id=$(grep '^base_image_id=' "$VOLUME_MOUNT/$out_sub/BUILD-INFO" 2>/dev/null | cut -d= -f2 || true)
      if [[ "$local_image_id" != "$expected_image_id" ]]; then
        echo "=== $engine ==="
        echo "  Source/toolchain is current, but builder image changed: ${local_image_id:-<unrecorded>} -> $expected_image_id"
        return 0
      fi
    fi
    echo "=== $engine ==="
    echo "  Already up to date at latest commit: $local_commit (Shaderc ${expected_shaderc_tag:-not tracked}; image ${expected_image_id:-not tracked})"
    return 1
  fi

  return 0
}

# Same as should_build, but the caller has already resolved the remote commit.
# This is important for halo-box: both backend variants must be built from one
# exact commit, not from two independent reads of a moving branch.
should_build_exact() {
  local engine="$1"
  local remote_commit="$2"
  local out_sub="$3"
  local expected_shaderc_tag="${4:-}"
  local expected_image_id="${5:-}"

  if [[ "$FORCE" == "true" ]]; then
    return 0
  fi

  local info_file=""
  local local_commit=""
  if [[ -n "$VOLUME_MOUNT" && -f "$VOLUME_MOUNT/$out_sub/BUILD-INFO" ]]; then
    info_file="$VOLUME_MOUNT/$out_sub/BUILD-INFO"
    local_commit=$(grep '^commit=' "$info_file" 2>/dev/null | cut -d= -f2 || true)
  fi

  if [[ -n "$local_commit" && "$local_commit" == "$remote_commit" ]]; then
    if [[ -n "$expected_shaderc_tag" ]]; then
      local local_shaderc_tag=""
      local_shaderc_tag=$(grep '^shaderc_tag=' "$info_file" 2>/dev/null | cut -d= -f2 || true)
      if [[ "$local_shaderc_tag" != "$expected_shaderc_tag" ]]; then
        echo "=== $engine ==="
        echo "  Source commit is current, but Shaderc changed: ${local_shaderc_tag:-<unrecorded>} -> $expected_shaderc_tag"
        return 0
      fi
    fi
    if [[ -n "$expected_image_id" ]]; then
      local local_image_id=""
      local_image_id=$(grep '^base_image_id=' "$info_file" 2>/dev/null | cut -d= -f2 || true)
      if [[ "$local_image_id" != "$expected_image_id" ]]; then
        echo "=== $engine ==="
        echo "  Source/toolchain is current, but builder image changed: ${local_image_id:-<unrecorded>} -> $expected_image_id"
        return 0
      fi
    fi
    echo "=== $engine ==="
    echo "  Already up to date at latest commit: $local_commit (Shaderc ${expected_shaderc_tag:-not tracked}; image ${expected_image_id:-not tracked})"
    return 1
  fi

  return 0
}

run_builder() {
  local name="$1"
  shift
  echo "=== $name (building bleeding-edge) ==="
  podman run --rm --name "lemonade26-build-$name" \
    -e "SHADERC_TAG=${SHADERC_TAG:-}" \
    -e "BUILD_IMAGE=$IMAGE" \
    -e "BUILD_IMAGE_ID=$IMAGE_ID" \
    -e "BUILD_IMAGE_DIGEST=$IMAGE_DIGEST" \
    -e "DRIVER_URL=$DRIVER_URL" \
    -e "HALO_REPO=$HALO_REPO" \
    -e "HALO_REF=$HALO_REF" \
    -e "HALO_BRANCH=$HALO_BRANCH" \
    -e "HALO_COMMIT=${HALO_COMMIT:-}" \
    -v "$VOLUME:/out:rw,z" \
    -v "$TOOLCHAIN_VOLUME:/toolchain:ro,z" \
    "$IMAGE" bash -euc "$*"
}

if [[ "$ONLY" == all || "$ONLY" == halo || "$ONLY" == halo-vulkan || "$ONLY" == halo-rocm ]]; then
  HALO_COMMIT=$(git ls-remote "$HALO_REPO" "$HALO_REF" 2>/dev/null | awk 'NR == 1 { print $1 }')
  [[ "$HALO_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "Unable to resolve $HALO_REF at $HALO_REPO" >&2
    exit 1
  }
  echo "=== halo-box strix-llama.cpp: $HALO_REF @ $HALO_COMMIT ==="
fi

if [[ "$ONLY" == all || "$ONLY" == nathan ]]; then
if should_build nathan "https://github.com/Nathanw1014/llama.cpp.git" "refs/heads/strix-halo-vulkan" "vulkan" "$SHADERC_TAG" "$IMAGE_ID"; then
run_builder nathan '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential curl libvulkan-dev glslang-tools spirv-headers spirv-tools libssl-dev
  GLSLC=/toolchain/shaderc/current/glslc
  test -x "$GLSLC"
  CPU_TARGET=$(gcc -Q --help=target -march=native 2>/dev/null | awk '\''$1 == "-march=" { print $2; exit }'\'')
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $("$GLSLC" --version 2>&1 | head -1)"
  echo "glslangValidator: $(glslangValidator --version 2>&1 | head -1)"

  rm -rf /tmp/vulkan-portable /tmp/vulkan-src /out/vulkan26.new
  mkdir -p /tmp/vulkan-portable /out/vulkan26.new/driver /out/vulkan26.new/bin
  curl --fail --location --retry 3 --silent --show-error \
    -o /tmp/vulkan-portable.tar.gz "'"$DRIVER_URL"'"
  tar xzf /tmp/vulkan-portable.tar.gz -C /tmp/vulkan-portable --strip-components=1
  test -f /tmp/vulkan-portable/driver/radeon_icd.x86_64.json
  cp -a /tmp/vulkan-portable/driver/. /out/vulkan26.new/driver/

  rm -rf /tmp/vulkan-src
  git clone --branch strix-halo-vulkan --single-branch --depth=1 https://github.com/Nathanw1014/llama.cpp.git /tmp/vulkan-src
  cd /tmp/vulkan-src
  NATHAN_COMMIT=$(git rev-parse HEAD)
  cmake -B build \
    -DGGML_VULKAN=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=ON \
    -DVulkan_INCLUDE_DIR=/usr/include \
    -DVulkan_GLSLC_EXECUTABLE="$GLSLC"
  cmake --build build --config Release -j'"$JOBS"' --target llama-server llama-cli llama-bench
  test -x build/bin/llama-server
  test -x build/bin/llama-cli
  test -x build/bin/llama-bench
  cp -f build/bin/llama-server /out/vulkan26.new/bin/llama-server-real
  cp -f build/bin/llama-cli build/bin/llama-bench /out/vulkan26.new/bin/
  {
    printf "repo=https://github.com/Nathanw1014/llama.cpp.git\n"
    printf "ref=strix-halo-vulkan\n"
    printf "commit=%s\n" "$NATHAN_COMMIT"
    printf "base_image=%s\n" "$BUILD_IMAGE"
    printf "base_image_id=%s\n" "$BUILD_IMAGE_ID"
    printf "base_image_digest=%s\n" "$BUILD_IMAGE_DIGEST"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
    printf "cmake=%s\n" "$(cmake --version | head -1)"
    printf "shaderc_tag=%s\n" "$SHADERC_TAG"
    printf "glslc=%s\n" "$("$GLSLC" --version 2>&1 | head -1)"
    printf "ggml_native=ON\n"
    printf "cpu_target=%s\n" "${CPU_TARGET:-native}"
  } > /out/vulkan26.new/BUILD-INFO
  rm -rf /out/vulkan
  mv /out/vulkan26.new /out/vulkan
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == laurent ]]; then
if should_build laurent "https://github.com/LaurentZuijdwijk/llama.cpp.git" "refs/heads/vulkan/qwen4exp-rocmfpx" "vulkan-laurent" "$SHADERC_TAG" "$IMAGE_ID"; then
run_builder laurent '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential curl libvulkan-dev glslang-tools spirv-headers spirv-tools libssl-dev
  GLSLC=/toolchain/shaderc/current/glslc
  test -x "$GLSLC"
  CPU_TARGET=$(gcc -Q --help=target -march=native 2>/dev/null | awk '\''$1 == "-march=" { print $2; exit }'\'')
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $("$GLSLC" --version 2>&1 | head -1)"

  rm -rf /tmp/vulkan-portable /tmp/laurent-src /out/vulkan-laurent26.new
  mkdir -p /tmp/vulkan-portable /out/vulkan-laurent26.new/driver /out/vulkan-laurent26.new/bin
  curl --fail --location --retry 3 --silent --show-error \
    -o /tmp/vulkan-portable.tar.gz "'"$DRIVER_URL"'"
  tar xzf /tmp/vulkan-portable.tar.gz -C /tmp/vulkan-portable --strip-components=1
  test -f /tmp/vulkan-portable/driver/radeon_icd.x86_64.json
  cp -a /tmp/vulkan-portable/driver/. /out/vulkan-laurent26.new/driver/

  LAURENT_BRANCH="vulkan/qwen4exp-rocmfpx"
  rm -rf /tmp/laurent-src
  git clone --branch "$LAURENT_BRANCH" --single-branch --depth=1 https://github.com/LaurentZuijdwijk/llama.cpp.git /tmp/laurent-src
  cd /tmp/laurent-src
  LAURENT_COMMIT=$(git rev-parse HEAD)
  cmake -B build \
    -DGGML_VULKAN=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=ON \
    -DVulkan_INCLUDE_DIR=/usr/include \
    -DVulkan_GLSLC_EXECUTABLE="$GLSLC"
  cmake --build build --config Release -j'"$JOBS"' --target llama-server llama-cli llama-bench
  test -x build/bin/llama-server
  test -x build/bin/llama-cli
  test -x build/bin/llama-bench
  cp -f build/bin/llama-server build/bin/llama-cli build/bin/llama-bench /out/vulkan-laurent26.new/bin/
  {
    printf "repo=https://github.com/LaurentZuijdwijk/llama.cpp.git\n"
    printf "ref=%s\n" "$LAURENT_BRANCH"
    printf "commit=%s\n" "$LAURENT_COMMIT"
    printf "base_image=%s\n" "$BUILD_IMAGE"
    printf "base_image_id=%s\n" "$BUILD_IMAGE_ID"
    printf "base_image_digest=%s\n" "$BUILD_IMAGE_DIGEST"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
    printf "cmake=%s\n" "$(cmake --version | head -1)"
    printf "shaderc_tag=%s\n" "$SHADERC_TAG"
    printf "glslc=%s\n" "$("$GLSLC" --version 2>&1 | head -1)"
    printf "ggml_native=ON\n"
    printf "cpu_target=%s\n" "${CPU_TARGET:-native}"
  } > /out/vulkan-laurent26.new/BUILD-INFO
  rm -rf /out/vulkan-laurent
  mv /out/vulkan-laurent26.new /out/vulkan-laurent
'
fi
fi

# halo-box Vulkan/RADV backend. Keep it separate from Nathan's Vulkan build so
# both can be selected at runtime and compared on the same model.
if [[ "$ONLY" == all || "$ONLY" == halo || "$ONLY" == halo-vulkan ]]; then
if should_build_exact halo-vulkan "$HALO_COMMIT" "halo-vulkan" "$SHADERC_TAG" "$IMAGE_ID"; then
run_builder halo-vulkan '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential curl libvulkan-dev glslang-tools spirv-headers spirv-tools libssl-dev
  GLSLC=/toolchain/shaderc/current/glslc
  test -x "$GLSLC"
  CPU_TARGET=$(gcc -Q --help=target -march=native 2>/dev/null | awk '\''$1 == "-march=" { print $2; exit }'\'')
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $("$GLSLC" --version 2>&1 | head -1)"
  echo "glslangValidator: $(glslangValidator --version 2>&1 | head -1)"

  rm -rf /tmp/halo-vulkan-src /tmp/vulkan-portable /out/halo-vulkan26.new
  mkdir -p /tmp/vulkan-portable /out/halo-vulkan26.new/driver /out/halo-vulkan26.new/bin
  curl --fail --location --retry 3 --silent --show-error \
    -o /tmp/vulkan-portable.tar.gz "$DRIVER_URL"
  tar xzf /tmp/vulkan-portable.tar.gz -C /tmp/vulkan-portable --strip-components=1
  test -f /tmp/vulkan-portable/driver/radeon_icd.x86_64.json
  cp -a /tmp/vulkan-portable/driver/. /out/halo-vulkan26.new/driver/

  git clone --branch "$HALO_BRANCH" --single-branch --depth=1 "$HALO_REPO" /tmp/halo-vulkan-src
  cd /tmp/halo-vulkan-src
  git fetch --depth=1 origin "$HALO_COMMIT"
  git checkout --detach "$HALO_COMMIT"
  HALO_BUILT_COMMIT=$(git rev-parse HEAD)
  [[ "$HALO_BUILT_COMMIT" == "$HALO_COMMIT" ]]

  cmake -S . -B build \
    -DGGML_VULKAN=ON \
    -DGGML_HIP=OFF \
    -DGGML_CUDA=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=ON \
    -DVulkan_INCLUDE_DIR=/usr/include \
    -DVulkan_GLSLC_EXECUTABLE="$GLSLC"
  cmake --build build --config Release -j'"$JOBS"' --target llama-server llama-cli llama-bench
  test -x build/bin/llama-server
  test -x build/bin/llama-cli
  test -x build/bin/llama-bench
  cp -f build/bin/llama-server /out/halo-vulkan26.new/bin/llama-server-real
  cp -f build/bin/llama-cli build/bin/llama-bench /out/halo-vulkan26.new/bin/
  {
    printf "repo=%s\\n" "$HALO_REPO"
    printf "ref=%s\\n" "$HALO_REF"
    printf "commit=%s\\n" "$HALO_BUILT_COMMIT"
    printf "backend=vulkan\\n"
    printf "base_image=%s\\n" "$BUILD_IMAGE"
    printf "base_image_id=%s\\n" "$BUILD_IMAGE_ID"
    printf "base_image_digest=%s\\n" "$BUILD_IMAGE_DIGEST"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
    printf "cmake=%s\\n" "$(cmake --version | head -1)"
    printf "shaderc_tag=%s\\n" "$SHADERC_TAG"
    printf "glslc=%s\\n" "$("$GLSLC" --version 2>&1 | head -1)"
    printf "ggml_vulkan=ON\\n"
    printf "ggml_hip=OFF\\n"
    printf "ggml_cuda=OFF\\n"
    printf "ggml_native=ON\\n"
    printf "cpu_target=%s\\n" "${CPU_TARGET:-native}"
  } > /out/halo-vulkan26.new/BUILD-INFO
  rm -rf /out/halo-vulkan
  mv /out/halo-vulkan26.new /out/halo-vulkan
'
fi
fi

# halo-box ROCm/HIP backend. The fork's own ROCm container recipe passes
# AMDGPU_TARGETS=gfx1151; keep that explicit even though its quick-start text
# also shows the older GPU_TARGETS spelling.
if [[ "$ONLY" == all || "$ONLY" == halo || "$ONLY" == halo-rocm ]]; then
if should_build_exact halo-rocm "$HALO_COMMIT" "halo-rocm" "" "$IMAGE_ID"; then
run_builder halo-rocm '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  export PATH=/opt/rocm/bin:$PATH
  export ROCM_PATH="${ROCM_PATH:-$(hipconfig -R)}"
  export HIP_PATH="${HIP_PATH:-$ROCM_PATH}"
  export HIPCXX="${HIPCXX:-$(hipconfig -l)/clang}"
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential clang libssl-dev
  command -v hipconfig >/dev/null
  test -x "$HIPCXX"
  CPU_TARGET=$(gcc -Q --help=target -march=native 2>/dev/null | awk '\''$1 == "-march=" { print $2; exit }'\'')
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "ROCm: $(hipconfig --version 2>/dev/null || true)"
  echo "HIP_PATH: $HIP_PATH"
  echo "HIPCXX: $HIPCXX"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"

  rm -rf /tmp/halo-rocm-src /out/halo-rocm26.new
  git clone --branch "$HALO_BRANCH" --single-branch --depth=1 "$HALO_REPO" /tmp/halo-rocm-src
  cd /tmp/halo-rocm-src
  git fetch --depth=1 origin "$HALO_COMMIT"
  git checkout --detach "$HALO_COMMIT"
  HALO_BUILT_COMMIT=$(git rev-parse HEAD)
  [[ "$HALO_BUILT_COMMIT" == "$HALO_COMMIT" ]]

  cmake -S . -B build \
    -DGGML_HIP=ON \
    -DGGML_VULKAN=OFF \
    -DGGML_CUDA=OFF \
    -DAMDGPU_TARGETS=gfx1151 \
    -DGGML_HIP_GRAPHS=ON \
    -DGGML_HIP_MMQ_MFMA=ON \
    -DGGML_HIP_NO_VMM=ON \
    -DGGML_HIP_RCCL=OFF \
    -DGGML_NATIVE=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release
  cmake --build build --config Release -j'"$JOBS"' --target llama-server llama-cli llama-bench
  for f in llama-server llama-cli llama-bench; do test -x "build/bin/$f"; done

  OUT=/out/halo-rocm26.new
  rm -rf "$OUT"
  mkdir -p "$OUT/lib"
  shopt -s nullglob
  cp -f build/bin/llama-server build/bin/llama-cli build/bin/llama-bench "$OUT/"
  build_libs=(build/bin/*.so*)
  ((${#build_libs[@]})) && cp -Pf "${build_libs[@]}" "$OUT/lib/"
  rocm_libs=(/opt/rocm/lib/*.so*)
  ((${#rocm_libs[@]})) && cp -aL "${rocm_libs[@]}" "$OUT/lib/"
  llvm_libs=(/opt/rocm/llvm/lib/*.so*)
  ((${#llvm_libs[@]})) && cp -aL "${llvm_libs[@]}" "$OUT/lib/"
  cp -a /opt/rocm/lib/rocblas "$OUT/lib/"
  cp -aL /lib64/ld-linux-x86-64.so.2 "$OUT/ld-linux-x86-64.so.2"

  export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/llvm/lib:"$OUT/lib":$PWD/build/bin
  declare -A seen=()
  queue=("$OUT/llama-server" "$OUT/llama-cli" "$OUT/llama-bench" "$OUT/lib"/*.so*)
  while ((${#queue[@]})); do
    f="${queue[0]}"; queue=("${queue[@]:1}")
    test -e "$f" || continue
    while read -r lib; do
      lib_real=$(readlink -f "$lib" 2>/dev/null || true)
      [[ -n "$lib_real" && -f "$lib_real" ]] || continue

      dest="$OUT/lib/$(basename "$lib")"
      key="$dest"
      [[ ${seen[$key]+x} ]] && continue
      seen[$key]=1

      dest_real=$(readlink -f "$dest" 2>/dev/null || true)
      if [[ "$lib_real" != "$dest_real" ]]; then
        cp -aL "$lib_real" "$dest"
      fi
      queue+=("$dest")
    done < <(ldd "$f" 2>/dev/null | grep -oE "/[^ (]+" || true)
  done
  {
    printf "repo=%s\\n" "$HALO_REPO"
    printf "ref=%s\\n" "$HALO_REF"
    printf "commit=%s\\n" "$HALO_BUILT_COMMIT"
    printf "backend=rocm\\n"
    printf "base_image=%s\\n" "$BUILD_IMAGE"
    printf "base_image_id=%s\\n" "$BUILD_IMAGE_ID"
    printf "base_image_digest=%s\\n" "$BUILD_IMAGE_DIGEST"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "rocm=%s\\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "hip_path=%s\\n" "$HIP_PATH"
    printf "hipcxx=%s\\n" "$HIPCXX"
    printf "target_variable=AMDGPU_TARGETS\\n"
    printf "amdgpu_targets=gfx1151\\n"
    printf "ggml_hip=ON\\n"
    printf "ggml_hip_graphs=ON\\n"
    printf "ggml_hip_mmq_mfma=ON\\n"
    printf "ggml_hip_no_vmm=ON\\n"
    printf "ggml_hip_rccl=OFF\\n"
    printf "ggml_vulkan=OFF\\n"
    printf "ggml_cuda=OFF\\n"
    printf "ggml_native=ON\\n"
    printf "cpu_target=%s\\n" "${CPU_TARGET:-native}"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"

  STAGED_LP="$OUT:$OUT/lib:$OUT/rocm/lib:$OUT/rocm/lib/rocm_sysdeps/lib:$OUT/rocm/lib/llvm/lib"
  "$OUT/ld-linux-x86-64.so.2" \
    --library-path "$STAGED_LP" \
    "$OUT/llama-server" --help >/dev/null

  rm -rf /out/halo-rocm
  mv "$OUT" /out/halo-rocm
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == gaetan ]]; then
if should_build gaetan "https://github.com/gaetan-puleo/llama-cpp-strix-halo.git" "HEAD" "rocm" "" "$IMAGE_ID"; then
run_builder gaetan '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  export PATH=/opt/rocm/bin:$PATH
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential libssl-dev libvulkan-dev spirv-headers glslang-tools
  CPU_TARGET=$(gcc -Q --help=target -march=native 2>/dev/null | awk '\''$1 == "-march=" { print $2; exit }'\'')
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "ROCm: $(hipconfig --version 2>/dev/null || true)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"

  rm -rf /tmp/rocm-src /out/rocm26.new
  git clone --depth=1 https://github.com/gaetan-puleo/llama-cpp-strix-halo.git /tmp/rocm-src
  cd /tmp/rocm-src
  GAETAN_COMMIT=$(git rev-parse HEAD)
  cmake -B build \
    -DGGML_HIP=ON \
    -DGGML_NATIVE=ON \
    -DAMDGPU_TARGETS=gfx1151 \
    -DGGML_HIP_ROCWMMA_FATTN=ON \
    -DCMAKE_BUILD_TYPE=Release
  cmake --build build --config Release -j'"$JOBS"'
  for f in llama-server llama-cli llama-bench; do test -x "build/bin/$f"; done

  OUT=/out/rocm26.new
  rm -rf "$OUT"
  mkdir -p "$OUT/lib"
  shopt -s nullglob
  cp -f build/bin/llama-server build/bin/llama-cli build/bin/llama-bench "$OUT/"
  build_libs=(build/bin/*.so*)
  ((${#build_libs[@]})) && cp -Pf "${build_libs[@]}" "$OUT/lib/"
  rocm_libs=(/opt/rocm/lib/*.so*)
  ((${#rocm_libs[@]})) && cp -aL "${rocm_libs[@]}" "$OUT/lib/"
  llvm_libs=(/opt/rocm/llvm/lib/*.so*)
  ((${#llvm_libs[@]})) && cp -aL "${llvm_libs[@]}" "$OUT/lib/"
  cp -a /opt/rocm/lib/rocblas "$OUT/lib/"
  cp -aL /lib64/ld-linux-x86-64.so.2 "$OUT/ld-linux-x86-64.so.2"

  export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/llvm/lib:"$OUT/lib":$PWD/build/bin
  declare -A seen=()
  queue=("$OUT/llama-server" "$OUT/llama-cli" "$OUT/llama-bench" "$OUT/lib"/*.so*)
  while ((${#queue[@]})); do
    f="${queue[0]}"; queue=("${queue[@]:1}")
    test -e "$f" || continue
    while read -r lib; do
      lib_real=$(readlink -f "$lib" 2>/dev/null || true)
      [[ -n "$lib_real" && -f "$lib_real" ]] || continue

      dest="$OUT/lib/$(basename "$lib")"
      key="$dest"
      [[ ${seen[$key]+x} ]] && continue
      seen[$key]=1

      dest_real=$(readlink -f "$dest" 2>/dev/null || true)
      if [[ "$lib_real" != "$dest_real" ]]; then
        cp -aL "$lib_real" "$dest"
      fi
      queue+=("$dest")
    done < <(ldd "$f" 2>/dev/null | grep -oE "/[^ (]+" || true)
  done
  {
    printf "repo=https://github.com/gaetan-puleo/llama-cpp-strix-halo.git\n"
    printf "ref=HEAD\n"
    printf "commit=%s\n" "$GAETAN_COMMIT"
    printf "base_image=%s\n" "$BUILD_IMAGE"
    printf "base_image_id=%s\n" "$BUILD_IMAGE_ID"
    printf "base_image_digest=%s\n" "$BUILD_IMAGE_DIGEST"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "rocm=%s\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "ggml_native=ON\n"
    printf "cpu_target=%s\n" "${CPU_TARGET:-native}"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"

  STAGED_LP="$OUT:$OUT/lib:$OUT/rocm/lib:$OUT/rocm/lib/rocm_sysdeps/lib:$OUT/rocm/lib/llvm/lib"
  "$OUT/ld-linux-x86-64.so.2" \
    --library-path "$STAGED_LP" \
    "$OUT/llama-server" --help >/dev/null

  rm -rf /out/rocm
  mv "$OUT" /out/rocm
'
fi
fi

# When both Halo variants are requested, refuse to report success unless they
# were both produced from the one commit resolved above. The Justfile only
# synchronizes the dispatch wrapper after this script returns successfully.
if [[ "$ONLY" == all || "$ONLY" == halo ]]; then
  for halo_engine in halo-vulkan halo-rocm; do
    halo_info="$VOLUME_MOUNT/$halo_engine/BUILD-INFO"
    [[ -f "$halo_info" ]] || {
      echo "Missing Halo build metadata: $halo_info" >&2
      exit 1
    }
    grep -qx "commit=$HALO_COMMIT" "$halo_info" || {
      echo "Halo backends do not share resolved commit $HALO_COMMIT: $halo_info" >&2
      exit 1
    }
  done
  echo "=== halo-box Vulkan and ROCm artifacts agree on $HALO_COMMIT ==="
fi

chmod -R a+rX "$VOLUME_MOUNT" 2>/dev/null || true
echo "Ubuntu 26 engine check/build completed in volume $VOLUME (ONLY=$ONLY)"
