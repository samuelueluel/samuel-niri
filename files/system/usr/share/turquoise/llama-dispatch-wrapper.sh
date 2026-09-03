#!/usr/bin/env bash
# /opt/lemonade/llama/llama-server
# Transparent dispatch wrapper for Lemonade Server
# All models default to Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)

# Prioritize llama-server for kernel OOM killer termination over desktop session apps
echo 1000 > /proc/self/oom_score_adj 2>/dev/null || true

USE_ROCM=0
USE_LAURENT=0
USE_CIRU=0
USE_MYHACSINT=0
CLEAN_CMD=()

for arg in "$@"; do
  if [[ "$arg" == *"--force-myhacsint"* ]]; then
    USE_MYHACSINT=1
    USE_CIRU=0
    USE_ROCM=0
    USE_LAURENT=0
  elif [[ "$arg" == *"--force-ciru"* ]]; then
    USE_MYHACSINT=0
    USE_CIRU=1
  elif [[ "$arg" == *"--force-rocm"* ]]; then
    USE_MYHACSINT=0
    USE_ROCM=1
  elif [[ "$arg" == *"--force-laurent"* ]]; then
    USE_MYHACSINT=0
    USE_LAURENT=1
  elif [[ "$arg" == *"--force-vulkan"* ]]; then
    USE_MYHACSINT=0
    USE_ROCM=0
    USE_LAURENT=0
    USE_CIRU=0
  else
    CLEAN_CMD+=("$arg")
  fi
done

if [[ $USE_MYHACSINT -eq 1 ]]; then
  # myhacsint Qwen4Exp production snapshot (shared Q8_0 MTP path)
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan-myhacsint/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan-myhacsint/bin:/opt/lemonade/llama/vulkan-myhacsint/driver"
  exec /opt/lemonade/llama/vulkan-myhacsint/bin/llama-server "${CLEAN_CMD[@]}"
elif [[ $USE_CIRU -eq 1 ]]; then
  # CIRU ROCm HIP engine (Qwen3.8-Flash-CIRU-STRIX-IU4 / Strix Halo gfx1151)
  export GGML_CUDA_Q41_MOE_FORCE_J=32
  export GGML_QWEN4EXP_PLE_WORKERS=16
  export GGML_QWEN4EXP_PLE_STRICT_SHA=0
  export ROCBLAS_USE_HIPBLASLT=1
  if [[ -f /opt/lemonade/llama/ciru/ld-linux-x86-64.so.2 ]]; then
    exec /opt/lemonade/llama/ciru/ld-linux-x86-64.so.2 \
      --library-path "/opt/lemonade/llama/ciru:/opt/lemonade/llama/ciru/lib:/opt/lemonade/llama/ciru/rocm/lib:/opt/lemonade/llama/ciru/rocm/lib/rocm_sysdeps/lib:/opt/lemonade/llama/ciru/rocm/lib/llvm/lib" \
      /opt/lemonade/llama/ciru/llama-server "${CLEAN_CMD[@]}"
  else
    export LD_LIBRARY_PATH="/opt/lemonade/llama/ciru/lib:/opt/lemonade/llama/ciru:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:$LD_LIBRARY_PATH"
    exec /opt/lemonade/llama/ciru/llama-server "${CLEAN_CMD[@]}"
  fi
elif [[ $USE_ROCM -eq 1 ]]; then
  # Gaetan Puleo ROCm HIP engine (Strix Halo gfx1151)
  if [[ -f /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 ]]; then
    exec /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 \
      --library-path "/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib/rocm_sysdeps/lib:/opt/lemonade/llama/rocm/rocm/lib/llvm/lib" \
      /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
  else
    export LD_LIBRARY_PATH="/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:$LD_LIBRARY_PATH"
    exec /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
  fi
elif [[ $USE_LAURENT -eq 1 ]]; then
  # Laurent Zuijdwijk Vulkan RADV engine (Adaptive DFlash2 + ROCmFP4)
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan-laurent/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan-laurent/bin:/opt/lemonade/llama/vulkan-laurent/driver"
  export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
  exec /opt/lemonade/llama/vulkan-laurent/bin/llama-server "${CLEAN_CMD[@]}"
else
  # Nathanw1014 Vulkan RADV engine (strix-halo-vulkan)
  export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json"
  export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
  export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver"
  export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
  if [[ -f /opt/lemonade/llama/vulkan/bin/llama-server-real ]]; then
    exec /opt/lemonade/llama/vulkan/bin/llama-server-real "${CLEAN_CMD[@]}"
  else
    exec /opt/lemonade/llama/vulkan/bin/llama-server "${CLEAN_CMD[@]}"
  fi
fi
