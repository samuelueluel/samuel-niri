#!/usr/bin/env bash
# /opt/lemonade/llama/llama-server
# Transparent dispatch wrapper for Lemonade Server

IS_MOE=0
for arg in "$@"; do
  if [[ "$arg" == *"MoE"* || "$arg" == *"moe"* || "$arg" == *"35B"* || "$arg" == *"Flash"* || "$arg" == *"flash"* || "$arg" == *"DeepSeek"* ]]; then
    IS_MOE=1
    break
  fi
done

if [[ $IS_MOE -eq 1 ]]; then
  # MoE -> Nathanw1014 Vulkan RADV engine
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver"
  export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
  exec /opt/lemonade/llama/vulkan/bin/llama-server "$@"
else
  # Dense -> Gaëtan Puleo ROCm 7.14 engine
  export LD_LIBRARY_PATH="/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/rocm/lib:/opt/lemonade/llama/rocm/rocm/llvm/lib"
  exec /opt/lemonade/llama/rocm/llama-server "$@"
fi
