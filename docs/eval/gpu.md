# Evaluating local models on a rented GPU

The challenge allows at most 15B parameters in total, offline. To compare such models, vLLM serves
one model on a rented GPU, and everything else runs on the development machine as usual: the
agent, the Docker sandbox, the data, and the scorer. An SSH tunnel connects the two.

```
dev machine                                         rented GPU box
  eval.datamate.prepare / run  --vllm:<model>-->  ssh tunnel  -->  vLLM on 127.0.0.1:8000
  sandbox containers, train/, public_test/, scorer
```

This keeps the sandbox design unchanged, and no data file, question, answer, or rulebook is
copied to the box. Prompts do travel to it, as they did to Groq. vLLM does not log request
contents unless asked to.

## 1. Rent

- Prefer **2 × RTX 5090**, the challenge's machine: timings and batch-invariant output then match
  the submission. CUDA 12.8 or newer, about 100 GB of disk for two or three 14B models.
- A 14B model in bf16 takes about 30 GB, so one 32 GB card holds it only with `QUANTIZATION=fp8`.
  A single 48–96 GB card also works for accuracy runs, but its timings do not transfer.
- Note the hourly price: GPU hours are this phase's spend, and each run reports them.

## 2. Serve a model

Copy `scripts/gpu/serve.sh` to the box and run it in `tmux`:

```bash
bash serve.sh Qwen/Qwen3-14B qwen3          # family picks the tool-call and reasoning parsers
```

The script installs vLLM in a venv on first use, binds to 127.0.0.1 only, sets a fixed seed and
`VLLM_BATCH_INVARIANT=1`, and writes the vLLM version and GPUs to `~/vllm-logs/server-info.txt`.
Copy that file into the run's notes. Families and options are listed at the top of the script.
Gemma 4 and Phi-4 have no vLLM tool-call parser yet, so they cannot drive the agent's tools.

## 3. Tunnel and preflight

On the dev machine:

```bash
ssh -N -L 8000:127.0.0.1:8000 -p <port> root@<host>        # keep it open
export VLLM_BASE_URL=http://localhost:8000/v1
uv run python -m eval.gpu.preflight --routes vllm:Qwen/Qwen3-14B
```

Preflight uses synthetic prompts only. It fails when the model does not call an offered tool in
the parser's format, or when one prompt answers differently alone and amid a busy batch, which
would break the challenge's re-run check (±0.03). It also reports output tokens per second at 16
requests at once. Do not start an eval run on a failed preflight.

Model options go through the environment, with no code change:

| Variable | Use |
|---|---|
| `VLLM_EXTRA_BODY` | JSON merged into each request, for example `{"chat_template_kwargs": {"enable_thinking": false}}` to turn Qwen3's thinking off |
| `VLLM_MIN_OUTPUT_TOKENS` | floor under each request's output cap; thinking counts toward it, so use about 8192 with thinking on |
| `VLLM_SEED` | request seed, default 0 |

## 4. Runs, in order

Each model or setting gets its own output directories, named after it, for example `q14-nothink`.

**Prepare the dev shops** with the local model, so ingestion is measured too. The challenge's
shops are new, so ingestion runs inside the container:

```bash
uv run python -m eval.datamate.prepare --data-dir train --routes vllm:Qwen/Qwen3-14B \
    --output-dir eval/reports/gpu-q14-nothink-prepare --max-cost 0.01
uv run python -m eval.ingestion.exclusions --labels eval/ingestion/labels/datamate_dev.jsonl \
    --datasets eval/reports/gpu-q14-nothink-prepare/datasets
```

A local model costs nothing in the ledger. `--max-cost 0` would skip every shop, since the
limit counts as reached at zero, so pass a small positive limit; token limits per shop still apply. Compare `summary.json` (built, problems, parse rates) and exclusion precision and
recall with `datasets-dev`.

**Answer the `regression` set** on those datasets:

```bash
uv run python -m eval.datamate.run --data-dir train --set regression --agent graph \
    --routes vllm:Qwen/Qwen3-14B --datasets-dir eval/reports/gpu-q14-nothink-prepare/datasets \
    --output-dir eval/reports/gpu-q14-nothink-regression --max-total-tokens 20000000 --workers 8
```

`--workers` runs that many questions at once, which a local server needs to be used well. Start
at 8 and raise it while `metrics.json` shows `wall_seconds` falling. Each query also starts a
sandbox container on the dev machine, so its CPU can become the limit before the GPU. With
thinking on, raise `--max-tokens-per-question` (default 60,000), since thinking counts toward it. To separate the model from
the datasets, also run the same set on `eval/reports/datasets-dev`.

**Time a whole test split** with the best setting. Public-test questions have no answers here, so
the run is not scored; it measures wall time and how often the agent abstains or fails:

```bash
uv run python -m eval.datamate.prepare --data-dir public_test --all-shops \
    --routes vllm:Qwen/Qwen3-14B --output-dir eval/reports/gpu-q14-public-prepare --max-cost 0.01
uv run python -m eval.datamate.run --data-dir public_test --all-questions --agent graph \
    --routes vllm:Qwen/Qwen3-14B --datasets-dir eval/reports/gpu-q14-public-prepare/datasets \
    --output-dir eval/reports/gpu-q14-public --max-total-tokens 100000000 --workers 16
```

The challenge gives 6 hours on 2 × 5090 for model loading, ingestion, and every question.

## 5. What to record

For each run, in `docs/eval/` as aggregates only: model and family, thinking on or off, vLLM
version, GPUs, preflight result, score and correct / wrong / abstained, stop reasons, wall time,
p50 and p95 latency, and GPU hours times the hourly price.

## 6. Tear down

Destroy the instance, with its disk, when the runs end. Nothing on it is needed afterwards:
the results are on the dev machine.
