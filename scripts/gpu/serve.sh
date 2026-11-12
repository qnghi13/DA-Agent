#!/usr/bin/env bash
# Serve one model with vLLM on a rented GPU box, for DA-Agent eval runs over an SSH tunnel.
# Copy only this file to the box: no data, questions, or answers go there. See docs/eval/gpu.md.
#
#   bash serve.sh <hf-model-id> <family>
#   bash serve.sh Qwen/Qwen3-14B qwen3
#
# family picks the tool-call and reasoning parsers, which must match the model's chat format:
#   qwen3    Qwen3 dense models (hermes tool calls, <think> reasoning)
#   hermes   Qwen2.5 and Hermes-format models
#   mistral  Mistral and Ministral models in the official format
#   granite4 IBM Granite 4.0
#
# Environment (defaults in brackets):
#   TP [number of visible GPUs]  tensor parallel size; the challenge machine has 2 x RTX 5090
#   MAX_MODEL_LEN [32768]        context length; agent prompts stay well under it
#   GPU_MEMORY [0.90]            fraction of GPU memory vLLM may take
#   QUANTIZATION []              for example fp8, to fit a 14B model on one 32 GB card
#   BATCH_INVARIANT [1]          same output alone or in a busy batch (slower; needed for re-runs)
#   PORT [8000]                  bound to 127.0.0.1 only: reach it through an SSH tunnel
#   VLLM_SPEC [vllm]             pip requirement, for example vllm==0.x.y to pin a version
#   EXTRA_ARGS []                more vllm serve flags
set -euo pipefail

model="${1:?usage: serve.sh <hf-model-id> <family>}"
family="${2:?usage: serve.sh <hf-model-id> <family>}"

case "$family" in
  qwen3) parsers=(--enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3) ;;
  hermes) parsers=(--enable-auto-tool-choice --tool-call-parser hermes) ;;
  mistral)
    parsers=(--enable-auto-tool-choice --tool-call-parser mistral
      --tokenizer-mode mistral --config-format mistral --load-format mistral) ;;
  granite4) parsers=(--enable-auto-tool-choice --tool-call-parser granite4) ;;
  *) echo "unknown family: $family (qwen3, hermes, mistral, granite4)" >&2; exit 2 ;;
esac

venv="${VENV:-$HOME/vllm-venv}"
if [ ! -x "$venv/bin/vllm" ]; then
  command -v uv >/dev/null || pip install --quiet uv
  uv venv --python 3.12 "$venv"
  VIRTUAL_ENV="$venv" uv pip install "${VLLM_SPEC:-vllm}"
fi

gpus="$(nvidia-smi --list-gpus | wc -l)"
log_dir="${LOG_DIR:-$HOME/vllm-logs}"
mkdir -p "$log_dir"
# Record what served the run, so a result can be traced to an exact server.
{
  echo "date: $(date -u +%FT%TZ)"
  echo "model: $model ($family)"
  echo "vllm: $("$venv/bin/python" -c 'import vllm; print(vllm.__version__)')"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
} | tee "$log_dir/server-info.txt"

quantization=()
if [ -n "${QUANTIZATION:-}" ]; then quantization=(--quantization "$QUANTIZATION"); fi

# --generation-config vllm ignores the model's sampling defaults, so the request's temperature
# and seed alone decide sampling.
# shellcheck disable=SC2086
VLLM_BATCH_INVARIANT="${BATCH_INVARIANT:-1}" exec "$venv/bin/vllm" serve "$model" \
  --host 127.0.0.1 --port "${PORT:-8000}" \
  --tensor-parallel-size "${TP:-$gpus}" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --gpu-memory-utilization "${GPU_MEMORY:-0.90}" \
  --seed 0 --generation-config vllm \
  "${parsers[@]}" "${quantization[@]}" ${EXTRA_ARGS:-}
