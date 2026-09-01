#!/usr/bin/env bash
set -Eeuo pipefail

# Pinned AMD ROCm 7.14.1-full Ubuntu 26.04 amd64 manifest.
IMAGE="${IMAGE:-docker.io/rocm/dev-ubuntu-26.04@sha256:372e5efcd5c68bed44c4e3d13a57648634f669a34e7e8b4b756985c4e4fa4cdf}"
VOLUME="${VOLUME:-lemonade26-llama}"
JOBS="${JOBS:-64}"
ONLY="${ONLY:-all}"
# Pinned source commits: these match the currently deployed production engines.
# A migration rebuild must not silently follow a moving branch head.
# Nathan: df1671a03f746d7c657d4242fd75b9fba98afd38
# Laurent: 5e085d123eead2e89b5c19f824fccb05727da6a2
# Gaetan: 860c828363988d3e4b3d5c2b701dcba3d7b9f26c
# CIRU: baba5e0617ac40aa88b9ba96f4b90e584caec64e
# Pinned release asset; do not silently follow a changing /latest URL.
DRIVER_URL="${DRIVER_URL:-https://github.com/Nathanw1014/strix-halo-llamacpp/releases/download/v0.7.3/strix-halo-llamacpp-vulkan-portable.tar.gz}"

podman volume exists "$VOLUME" || podman volume create "$VOLUME" >/dev/null

run_builder() {
  local name="$1"
  shift
  echo "=== $name ==="
  podman run --rm --name "lemonade26-build-$name" \
    -v "$VOLUME:/out:rw,z" \
    "$IMAGE" bash -euc "$*"
}

if [[ "$ONLY" == all || "$ONLY" == nathan ]]; then
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

  NATHAN_EXPECTED=df1671a03f746d7c657d4242fd75b9fba98afd38
  rm -rf /tmp/vulkan-src
  git init -q /tmp/vulkan-src
  git -C /tmp/vulkan-src remote add origin https://github.com/Nathanw1014/llama.cpp.git
  git -C /tmp/vulkan-src fetch -q --depth=1 origin "$NATHAN_EXPECTED"
  git -C /tmp/vulkan-src checkout -q --detach FETCH_HEAD
  cd /tmp/vulkan-src
  NATHAN_COMMIT=$(git rev-parse HEAD)
  test "$NATHAN_COMMIT" = "$NATHAN_EXPECTED"
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
    printf "repo=https://github.com/Nathanw1014/llama.cpp.git\\n"
    printf "ref=strix-halo-vulkan\\n"
    printf "commit=%s\\n" "$NATHAN_COMMIT"
    printf "base_image=%s\\n" "'"$IMAGE"'"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
    printf "cmake=%s\\n" "$(cmake --version | head -1)"
    printf "glslc=%s\\n" "$(glslc --version 2>&1 | head -1)"
  } > /out/vulkan26.new/BUILD-INFO
  rm -rf /out/vulkan
  mv /out/vulkan26.new /out/vulkan
'
fi

if [[ "$ONLY" == all || "$ONLY" == laurent ]]; then
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
  LAURENT_EXPECTED=5e085d123eead2e89b5c19f824fccb05727da6a2
  rm -rf /tmp/laurent-src
  git init -q /tmp/laurent-src
  git -C /tmp/laurent-src remote add origin https://github.com/LaurentZuijdwijk/llama.cpp.git
  git -C /tmp/laurent-src fetch -q --depth=1 origin "$LAURENT_EXPECTED"
  git -C /tmp/laurent-src checkout -q --detach FETCH_HEAD
  cd /tmp/laurent-src
  LAURENT_COMMIT=$(git rev-parse HEAD)
  test "$LAURENT_COMMIT" = "$LAURENT_EXPECTED"
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
    printf "repo=https://github.com/LaurentZuijdwijk/llama.cpp.git\\n"
    printf "ref=%s\\n" "$LAURENT_BRANCH"
    printf "commit=%s\\n" "$LAURENT_COMMIT"
    printf "base_image=%s\\n" "'"$IMAGE"'"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
    printf "cmake=%s\\n" "$(cmake --version | head -1)"
    printf "glslc=%s\\n" "$(glslc --version 2>&1 | head -1)"
  } > /out/vulkan-laurent26.new/BUILD-INFO
  rm -rf /out/vulkan-laurent
  mv /out/vulkan-laurent26.new /out/vulkan-laurent
'
fi

if [[ "$ONLY" == all || "$ONLY" == gaetan ]]; then
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

  GAETAN_EXPECTED=860c828363988d3e4b3d5c2b701dcba3d7b9f26c
  rm -rf /tmp/rocm-src /out/rocm26.new
  git init -q /tmp/rocm-src
  git -C /tmp/rocm-src remote add origin https://github.com/gaetan-puleo/llama-cpp-strix-halo.git
  git -C /tmp/rocm-src fetch -q --depth=1 origin "$GAETAN_EXPECTED"
  git -C /tmp/rocm-src checkout -q --detach FETCH_HEAD
  cd /tmp/rocm-src
  GAETAN_COMMIT=$(git rev-parse HEAD)
  test "$GAETAN_COMMIT" = "$GAETAN_EXPECTED"
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
    printf "repo=https://github.com/gaetan-puleo/llama-cpp-strix-halo.git\\n"
    printf "ref=HEAD\\n"
    printf "commit=%s\\n" "$GAETAN_COMMIT"
    printf "base_image=%s\\n" "'"$IMAGE"'"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "rocm=%s\\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"
  rm -rf /out/rocm
  mv "$OUT" /out/rocm
'
fi

if [[ "$ONLY" == all || "$ONLY" == ciru ]]; then
run_builder ciru '
  export DEBIAN_FRONTEND=noninteractive
  export PATH=/opt/rocm/bin:$PATH
  apt-get update -qq
  apt-get install -y -qq git cmake ninja-build build-essential libssl-dev
  echo "OS: $(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
  echo "ROCm: $(hipconfig --version 2>/dev/null || true)"
  echo "gcc: $(gcc --version | head -1)"
  echo "cmake: $(cmake --version | head -1)"

  CIRU_EXPECTED=baba5e0617ac40aa88b9ba96f4b90e584caec64e
  rm -rf /tmp/ciru-src /out/ciru26.new
  git init -q /tmp/ciru-src
  git -C /tmp/ciru-src remote add origin https://github.com/ciru-ai/Qwen3.8-Flash-CIRU-STRIX-IU4.git
  git -C /tmp/ciru-src fetch -q --depth=1 origin "$CIRU_EXPECTED"
  git -C /tmp/ciru-src checkout -q --detach FETCH_HEAD
  cd /tmp/ciru-src
  CIRU_COMMIT=$(git rev-parse HEAD)
  test "$CIRU_COMMIT" = "$CIRU_EXPECTED"
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
    printf "repo=https://github.com/ciru-ai/Qwen3.8-Flash-CIRU-STRIX-IU4.git\\n"
    printf "ref=HEAD\\n"
    printf "commit=%s\\n" "$CIRU_COMMIT"
    printf "base_image=%s\\n" "'"$IMAGE"'"
    printf "os=%s\\n" "$(. /etc/os-release; printf "%s %s" "$PRETTY_NAME" "$VERSION_ID")"
    printf "glibc=%s\\n" "$(ldd --version | head -1)"
    printf "rocm=%s\\n" "$(hipconfig --version 2>/dev/null || true)"
    printf "gcc=%s\\n" "$(gcc --version | head -1)"
  } > "$OUT/BUILD-INFO"
  rm -rf /out/ciru
  mv "$OUT" /out/ciru
'
fi

echo "Ubuntu 26 candidate engine build(s) completed in volume $VOLUME (ONLY=$ONLY)"
