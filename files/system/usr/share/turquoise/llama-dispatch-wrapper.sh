#!/usr/bin/env bash
# /opt/lemonade/llama/llama-server
# Transparent dispatch wrapper for Lemonade Server.
# Existing paths remain unchanged; halo-box variants are opt-in with
# --force-halo-vulkan or --force-halo-rocm.

# Prioritize llama-server for kernel OOM killer termination over desktop session apps
echo 1000 > /proc/self/oom_score_adj 2>/dev/null || true

ENGINE="nathan"
CLEAN_CMD=()
HALO_ENV=()
HALO_VULKAN_REQUESTED=0
HALO_ROCM_REQUESTED=0
LEGACY_ENGINE=""
LEGACY_NONDEFAULT=0
LEGACY_ROCM_REQUESTED=0
LEGACY_LAURENT_REQUESTED=0

die() {
  echo "llama-dispatch-wrapper: $*" >&2
  exit 2
}

# Keep the experiment surface bounded to backend/runtime tuning variables. The
# loader can pass these as: --halo-env NAME=VALUE or --halo-env=NAME=VALUE.
add_halo_env() {
  local assignment="$1"
  local key
  [[ "$assignment" == *=* ]] || die "--halo-env requires NAME=VALUE"
  [[ "$assignment" != *$'\n'* && "$assignment" != *$'\r'* ]] || die "--halo-env cannot contain newlines"
  key="${assignment%%=*}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid --halo-env variable: $key"
  case "$key" in
    GGML_VK_*|GGML_HIP_*|HIP_LAUNCH_BLOCKING|HIP_VISIBLE_DEVICES|ROCR_VISIBLE_DEVICES|HSA_OVERRIDE_GFX_VERSION|AMD_SERIALIZE_KERNEL)
      ;;
    *)
      die "unsupported --halo-env variable: $key"
      ;;
  esac
  HALO_ENV+=("$assignment")
}

while (($#)); do
  arg="$1"
  shift
  case "$arg" in
    --halo-env)
      (($#)) || die "--halo-env requires NAME=VALUE"
      add_halo_env "$1"
      shift
      ;;
    --halo-env=*)
      add_halo_env "${arg#--halo-env=}"
      ;;
    --force-halo-vulkan)
      HALO_VULKAN_REQUESTED=1
      ;;
    --force-halo-rocm)
      HALO_ROCM_REQUESTED=1
      ;;
    --force-rocm)
      LEGACY_ENGINE="gaetan"
      LEGACY_NONDEFAULT=1
      LEGACY_ROCM_REQUESTED=1
      ;;
    --force-laurent)
      LEGACY_ENGINE="laurent"
      LEGACY_NONDEFAULT=1
      LEGACY_LAURENT_REQUESTED=1
      ;;
    --force-vulkan)
      # Canonical Nathan is the default; do not let it overwrite an explicit
      # non-default request when Lemonade reorders normalized arguments.
      ;;
    *)
      CLEAN_CMD+=("$arg")
      ;;
  esac
done

if (( LEGACY_ROCM_REQUESTED && LEGACY_LAURENT_REQUESTED )); then
  die "conflicting legacy backend flags: --force-rocm and --force-laurent"
fi
if (( HALO_VULKAN_REQUESTED && HALO_ROCM_REQUESTED )); then
  die "conflicting Halo backend flags: --force-halo-vulkan and --force-halo-rocm"
fi
if (( (HALO_VULKAN_REQUESTED || HALO_ROCM_REQUESTED) && LEGACY_NONDEFAULT )); then
  die "conflicting Halo and legacy backend flags"
fi
if (( HALO_VULKAN_REQUESTED )); then
  ENGINE="halo-vulkan"
elif (( HALO_ROCM_REQUESTED )); then
  ENGINE="halo-rocm"
elif [[ -n "$LEGACY_ENGINE" ]]; then
  ENGINE="$LEGACY_ENGINE"
fi

# Nathan's Flash recipe still contributes --tensor-read-lazy auto. The
# halo-box fork uses --ngram-on-disk for the Qwen3.8 Flash PLE and does not
# expose tensor-read-lazy, so remove that Nathan-only pair for Halo runs.
if [[ "$ENGINE" == halo-vulkan || "$ENGINE" == halo-rocm ]]; then
  HALO_CLEAN_CMD=()
  skip_next=0
  for arg in "${CLEAN_CMD[@]}"; do
    if (( skip_next )); then
      skip_next=0
      continue
    fi
    case "$arg" in
      --tensor-read-lazy)
        skip_next=1
        ;;
      --tensor-read-lazy=*)
        ;;
      *)
        HALO_CLEAN_CMD+=("$arg")
        ;;
    esac
  done
  CLEAN_CMD=("${HALO_CLEAN_CMD[@]}")
fi

for assignment in "${HALO_ENV[@]}"; do
  export "$assignment"
done
export TURQUOISE_LLM_ENGINE="$ENGINE"

case "$ENGINE" in
  halo-rocm)
    # The fork documents an async HIP correctness issue on gfx1151 for batched
    # inference. Blocking is the safe default; set HIP_LAUNCH_BLOCKING=0 via
    # --halo-env only when deliberately testing the faster/less safe path.
    export HIP_LAUNCH_BLOCKING="${HIP_LAUNCH_BLOCKING:-${HALO_ROCM_HIP_LAUNCH_BLOCKING:-1}}"
    if [[ -f /opt/lemonade/llama/halo-rocm/ld-linux-x86-64.so.2 ]]; then
      exec /opt/lemonade/llama/halo-rocm/ld-linux-x86-64.so.2 \
        --library-path "/opt/lemonade/llama/halo-rocm:/opt/lemonade/llama/halo-rocm/lib:/opt/lemonade/llama/halo-rocm/rocm/lib:/opt/lemonade/llama/halo-rocm/rocm/lib/rocm_sysdeps/lib:/opt/lemonade/llama/halo-rocm/rocm/lib/llvm/lib" \
        /opt/lemonade/llama/halo-rocm/llama-server "${CLEAN_CMD[@]}"
    else
      export LD_LIBRARY_PATH="/opt/lemonade/llama/halo-rocm/lib:/opt/lemonade/llama/halo-rocm:/opt/rocm/lib:/opt/rocm/lib/llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      exec /opt/lemonade/llama/halo-rocm/llama-server "${CLEAN_CMD[@]}"
    fi
    ;;
  halo-vulkan)
    # halo-box Vulkan/RADV engine. The portable ICD is staged next to the
    # binary, just as for the existing Nathan and Laurent Vulkan engines.
    export VK_ICD_FILENAMES="/opt/lemonade/llama/halo-vulkan/driver/radeon_icd.x86_64.json"
    export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
    export LD_LIBRARY_PATH="/opt/lemonade/llama/halo-vulkan/bin:/opt/lemonade/llama/halo-vulkan/driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    exec /opt/lemonade/llama/halo-vulkan/bin/llama-server-real "${CLEAN_CMD[@]}"
    ;;
  gaetan)
    # Existing Gaetan Puleo ROCm HIP engine (Strix Halo gfx1151).
    if [[ -f /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 ]]; then
      exec /opt/lemonade/llama/rocm/ld-linux-x86-64.so.2 \
        --library-path "/opt/lemonade/llama/rocm:/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib:/opt/lemonade/llama/rocm/rocm/lib/rocm_sysdeps/lib:/opt/lemonade/llama/rocm/rocm/lib/llvm/lib" \
        /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
    else
      export LD_LIBRARY_PATH="/opt/lemonade/llama/rocm/lib:/opt/lemonade/llama/rocm:/opt/rocm/lib:/opt/rocm/lib/llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      exec /opt/lemonade/llama/rocm/llama-server "${CLEAN_CMD[@]}"
    fi
    ;;
  laurent)
    # Existing Laurent Zuijdwijk Vulkan RADV engine (Adaptive DFlash2 + ROCmFP4).
    export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan-laurent/driver/radeon_icd.x86_64.json"
    export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
    export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan-laurent/bin:/opt/lemonade/llama/vulkan-laurent/driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
    exec /opt/lemonade/llama/vulkan-laurent/bin/llama-server "${CLEAN_CMD[@]}"
    ;;
  nathan)
    # Existing Nathanw1014 Vulkan RADV engine (strix-halo-vulkan).
    export VK_ICD_FILENAMES="/opt/lemonade/llama/vulkan/driver/radeon_icd.x86_64.json"
    export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
    export LD_LIBRARY_PATH="/opt/lemonade/llama/vulkan/bin:/opt/lemonade/llama/vulkan/driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export GGML_VK_MMID_ROWLISTS=1 GGML_VK_MMID_SMALLN=1 GGML_VK_MMID_BM64=1 GGML_VK_MMID_WAVE32=1 GGML_VK_MMID_F16B=1 GGML_VK_MMID_M128=1 GGML_VK_FA_WAVE32=1 GGML_VK_FA_DEQUANT=1 GGML_VK_MAX_NODES_PER_SUBMIT=64
    if [[ -f /opt/lemonade/llama/vulkan/bin/llama-server-real ]]; then
      exec /opt/lemonade/llama/vulkan/bin/llama-server-real "${CLEAN_CMD[@]}"
    else
      exec /opt/lemonade/llama/vulkan/bin/llama-server "${CLEAN_CMD[@]}"
    fi
    ;;
  *)
    die "unknown engine: $ENGINE"
    ;;
esac
