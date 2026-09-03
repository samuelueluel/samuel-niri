#!/usr/bin/env bash
set -Eeuo pipefail

# AMD ROCm 7.14.1-full Ubuntu 26.04 amd64 manifest.
IMAGE="${IMAGE:-docker.io/rocm/dev-ubuntu-26.04@sha256:372e5efcd5c68bed44c4e3d13a57648634f669a34e7e8b4b756985c4e4fa4cdf}"
VOLUME="${VOLUME:-lemonade26-llama}"
JOBS="${JOBS:-64}"
ONLY="${ONLY:-all}"
FORCE="${FORCE:-false}"
case "$FORCE" in
  true|1|force|force=true|--force) FORCE="true" ;;
  *) FORCE="false" ;;
esac

# Vulkan portable driver asset
DRIVER_URL="${DRIVER_URL:-https://github.com/Nathanw1014/strix-halo-llamacpp/releases/download/v0.7.3/strix-halo-llamacpp-vulkan-portable.tar.gz}"

podman volume exists "$VOLUME" || podman volume create "$VOLUME" >/dev/null

VOLUME_MOUNT=$(podman volume inspect "$VOLUME" --format '{{.Mountpoint}}' 2>/dev/null || true)

should_build() {
  local engine="$1"
  local repo="$2"
  local ref="$3"
  local out_sub="$4"

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
    echo "=== $engine ==="
    echo "  Already up to date at latest commit: $local_commit"
    return 1
  fi

  return 0
}

run_builder() {
  local name="$1"
  shift
  echo "=== $name (building bleeding-edge) ==="
  podman run --rm --name "lemonade26-build-$name" \
    -v "$VOLUME:/out:rw,z" \
    "$IMAGE" bash -euc "$*"
}

if [[ "$ONLY" == all || "$ONLY" == nathan ]]; then
if should_build nathan "https://github.com/Nathanw1014/llama.cpp.git" "refs/heads/strix-halo-vulkan" "vulkan"; then
run_builder nathan '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential curl libvulkan-dev glslc glslang-tools spirv-headers spirv-tools libssl-dev
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $(glslc --version 2>&1 | head -1)"
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
    -DVulkan_GLSLC_EXECUTABLE="$(command -v glslc)"
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
    printf "base_image=%s\n" "'"$IMAGE"'"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
    printf "cmake=%s\n" "$(cmake --version | head -1)"
    printf "glslc=%s\n" "$(glslc --version 2>&1 | head -1)"
  } > /out/vulkan26.new/BUILD-INFO
  rm -rf /out/vulkan
  mv /out/vulkan26.new /out/vulkan
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == laurent ]]; then
if should_build laurent "https://github.com/LaurentZuijdwijk/llama.cpp.git" "refs/heads/vulkan/qwen4exp-rocmfpx" "vulkan-laurent"; then
run_builder laurent '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential curl libvulkan-dev glslc glslang-tools spirv-headers spirv-tools libssl-dev
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $(glslc --version 2>&1 | head -1)"

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
    -DVulkan_GLSLC_EXECUTABLE="$(command -v glslc)"
  cmake --build build --config Release -j'"$JOBS"' --target llama-server llama-cli llama-bench
  test -x build/bin/llama-server
  test -x build/bin/llama-cli
  test -x build/bin/llama-bench
  cp -f build/bin/llama-server build/bin/llama-cli build/bin/llama-bench /out/vulkan-laurent26.new/bin/
  {
    printf "repo=https://github.com/LaurentZuijdwijk/llama.cpp.git\n"
    printf "ref=%s\n" "$LAURENT_BRANCH"
    printf "commit=%s\n" "$LAURENT_COMMIT"
    printf "base_image=%s\n" "'"$IMAGE"'"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
    printf "cmake=%s\n" "$(cmake --version | head -1)"
    printf "glslc=%s\n" "$(glslc --version 2>&1 | head -1)"
  } > /out/vulkan-laurent26.new/BUILD-INFO
  rm -rf /out/vulkan-laurent
  mv /out/vulkan-laurent26.new /out/vulkan-laurent
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == myhacsint ]]; then
if should_build myhacsint "https://github.com/myhacsint/llama.cpp.git" "refs/heads/production/strix-halo-qwen4exp-b10685" "vulkan-myhacsint"; then
run_builder myhacsint '
  export DEBIAN_FRONTEND=noninteractive
  export CC=gcc CXX=g++
  apt-get update -qq
  apt-get install -y -qq git cmake ninja-build build-essential curl libvulkan-dev glslc glslang-tools spirv-headers spirv-tools libssl-dev
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "glibc: $(ldd --version | head -1)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "ninja: $(ninja --version 2>&1 | head -1)"
  echo "glslc: $(glslc --version 2>&1 | head -1)"

  rm -rf /tmp/vulkan-portable /tmp/myhacsint-src /out/vulkan-myhacsint.new
  mkdir -p /tmp/vulkan-portable /out/vulkan-myhacsint.new/driver /out/vulkan-myhacsint.new/bin
  curl --fail --location --retry 3 --silent --show-error \
    -o /tmp/vulkan-portable.tar.gz "'"$DRIVER_URL"'"
  tar xzf /tmp/vulkan-portable.tar.gz -C /tmp/vulkan-portable --strip-components=1
  test -f /tmp/vulkan-portable/driver/radeon_icd.x86_64.json
  cp -a /tmp/vulkan-portable/driver/. /out/vulkan-myhacsint.new/driver/

  MYHACSINT_BRANCH="production/strix-halo-qwen4exp-b10685"
  rm -rf /tmp/myhacsint-src
  git clone --branch "$MYHACSINT_BRANCH" --single-branch --depth=1 https://github.com/myhacsint/llama.cpp.git /tmp/myhacsint-src
  cd /tmp/myhacsint-src
  MYHACSINT_COMMIT=$(git rev-parse HEAD)
  cmake -S . -B build-vulkan -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_VULKAN=ON \
    -DGGML_NATIVE=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_BUILD_TESTS=ON \
    -DLLAMA_OPENSSL=ON \
    -DVulkan_GLSLC_EXECUTABLE="$(command -v glslc)"
  cmake --build build-vulkan --config Release --parallel '"$JOBS"' --target llama-server llama-cli llama-bench
  test -x build-vulkan/bin/llama-server
  test -x build-vulkan/bin/llama-cli
  test -x build-vulkan/bin/llama-bench
  cp -f build-vulkan/bin/llama-server build-vulkan/bin/llama-cli build-vulkan/bin/llama-bench /out/vulkan-myhacsint.new/bin/
  {
    printf "repo=https://github.com/myhacsint/llama.cpp.git\n"
    printf "ref=%s\n" "$MYHACSINT_BRANCH"
    printf "commit=%s\n" "$MYHACSINT_COMMIT"
    printf "base_image=%s\n" "'"$IMAGE"'"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
    printf "cmake=%s\n" "$(cmake --version | head -1)"
    printf "ninja=%s\n" "$(ninja --version 2>&1 | head -1)"
    printf "glslc=%s\n" "$(glslc --version 2>&1 | head -1)"
    printf "cmake_flags=-DGGML_VULKAN=ON -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=ON -DLLAMA_OPENSSL=ON\n"
  } > /out/vulkan-myhacsint.new/BUILD-INFO
  rm -rf /out/vulkan-myhacsint
  mv /out/vulkan-myhacsint.new /out/vulkan-myhacsint
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == gaetan ]]; then
if should_build gaetan "https://github.com/gaetan-puleo/llama-cpp-strix-halo.git" "HEAD" "rocm"; then
run_builder gaetan '
  export DEBIAN_FRONTEND=noninteractive
  export PATH=/opt/rocm/bin:$PATH
  apt-get update -qq
  apt-get install -y -qq git cmake build-essential libssl-dev libvulkan-dev glslc spirv-headers glslang-tools
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "ROCm: $(hipconfig --version 2>/dev/null || true)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"
  echo "glslc: $(glslc --version 2>&1 | head -1)"

  rm -rf /tmp/rocm-src /out/rocm26.new
  git clone --depth=1 https://github.com/gaetan-puleo/llama-cpp-strix-halo.git /tmp/rocm-src
  cd /tmp/rocm-src
  GAETAN_COMMIT=$(git rev-parse HEAD)
  cmake -B build \
    -DGGML_HIP=ON \
    -DAMDGPU_TARGETS=gfx1151 \
    -DGGML_HIP_ROCWMMA_FATTN=ON \
    -DCMAKE_BUILD_TYPE=Release
  cmake --build build --config Release -j'"$JOBS"'
  for f in llama-server llama-cli llama-bench; do test -x "build/bin/$f"; done

  OUT=/out/rocm26.new
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
      test -f "$lib" || continue
      [[ ${seen[$lib]+x} ]] && continue
      seen[$lib]=1
      dest="$OUT/lib/$(basename "$lib")"
      if [[ "$lib" != "$dest" ]]; then
        cp -aL "$lib" "$dest"
      fi
      queue+=("$dest")
    done < <(ldd "$f" 2>/dev/null | grep -oE "/[^ (]+" || true)
  done
  {
    printf "repo=https://github.com/gaetan-puleo/llama-cpp-strix-halo.git\n"
    printf "ref=HEAD\n"
    printf "commit=%s\n" "$GAETAN_COMMIT"
    printf "base_image=%s\n" "'"$IMAGE"'"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "rocm=%s\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"
  rm -rf /out/rocm
  mv "$OUT" /out/rocm
'
fi
fi

if [[ "$ONLY" == all || "$ONLY" == ciru ]]; then
if should_build ciru "https://github.com/ciru-ai/Qwen3.8-Flash-CIRU-STRIX-IU4.git" "HEAD" "ciru"; then
run_builder ciru '
  export DEBIAN_FRONTEND=noninteractive
  export PATH=/opt/rocm/bin:$PATH
  apt-get update -qq
  apt-get install -y -qq git cmake ninja-build build-essential libssl-dev
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "ROCm: $(hipconfig --version 2>/dev/null || true)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"

  rm -rf /tmp/ciru-src /out/ciru26.new
  git clone --depth=1 https://github.com/ciru-ai/Qwen3.8-Flash-CIRU-STRIX-IU4.git /tmp/ciru-src
  cd /tmp/ciru-src
  CIRU_COMMIT=$(git rev-parse HEAD)
  cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_HIP=ON \
    -DAMDGPU_TARGETS=gfx1151 \
    -DGGML_HIP_ROCWMMA_FATTN=ON
  ninja -C build -j'"$JOBS"' llama-server llama-cli llama-bench
  for f in llama-server llama-cli llama-bench; do test -x "build/bin/$f"; done

  OUT=/out/ciru26.new
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
      test -f "$lib" || continue
      [[ ${seen[$lib]+x} ]] && continue
      seen[$lib]=1
      dest="$OUT/lib/$(basename "$lib")"
      if [[ "$lib" != "$dest" ]]; then
        cp -aL "$lib" "$dest"
      fi
      queue+=("$dest")
    done < <(ldd "$f" 2>/dev/null | grep -oE "/[^ (]+" || true)
  done
  {
    printf "repo=https://github.com/ciru-ai/Qwen3.8-Flash-CIRU-STRIX-IU4.git\n"
    printf "ref=HEAD\n"
    printf "commit=%s\n" "$CIRU_COMMIT"
    printf "base_image=%s\n" "'"$IMAGE"'"
    printf "os=%s\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\n" "$(ldd --version | head -1)"
    printf "rocm=%s\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "gcc=%s\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"
  rm -rf /out/ciru
  mv "$OUT" /out/ciru
'
fi
fi

chmod -R a+rX "$VOLUME_MOUNT" 2>/dev/null || true
echo "Ubuntu 26 engine check/build completed in volume $VOLUME (ONLY=$ONLY)"
