#!/usr/bin/env bash
# /opt/lemonade/llama/llama-server
# Transparent dispatch wrapper for Lemonade Server
# All models default to Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)

USE_ROCM=0
CLEAN_CMD=()

for arg in "$@"; do
  for word in $arg; do
    if [[ "$word" == *"--force-rocm"* ]]; then
      USE_ROCM=1
    elif [[ "$word" == *"--force-vulkan"* ]]; then
      USE_ROCM=0
    else
      CLEAN_CMD+=("$word")
    fi
  done
done

if [[ $USE_ROCM -eq 1 ]]; then
  # Gaëtan Puleo ROCm 7.14 HIP engine
  export LD_LIBRARY_PATH="/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/rocm/lib:/opt/lemonade/llama/rocm/rocm/llvm/lib"
  exec /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
else
  # Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver"
  export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
  exec /opt/lemonade/llama/vulkan/bin/llama-server "${CLEAN_CMD[@]}"
fi
