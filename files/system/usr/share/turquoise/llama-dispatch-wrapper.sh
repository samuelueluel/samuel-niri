#!/usr/bin/env bash
# /opt/lemonade/llama/llama-server
# Transparent dispatch wrapper for Lemonade Server
# All models default to Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)

USE_ROCM=0
CLEAN_CMD=()

for arg in "$@"; do
  if [[ "$arg" == *"--force-rocm"* ]]; then
    USE_ROCM=1
  elif [[ "$arg" == *"--force-vulkan"* ]]; then
    USE_ROCM=0
  else
    CLEAN_CMD+=("$arg")
  fi
done

if [[ $USE_ROCM -eq 1 ]]; then
  # Gaetan Puleo ROCm HIP engine (Strix Halo gfx1151)
  if [[ -f /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 ]]; then
    exec /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 \
      --library-path "/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib/rocm_sysdeps/lib:/opt/lemonade/llama/rocm/rocm/lib/llvm/lib" \
      /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
  else
    export LD_LIBRARY_PATH="/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:$LD_LIBRARY_PATH"
    exec /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
  fi
else
  # Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver"
  export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
  exec /opt/lemonade/llama/vulkan/bin/llama-server "${CLEAN_CMD[@]}"
fi
