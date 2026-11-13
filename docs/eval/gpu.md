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

- **One RTX 5090** (32 GB) is enough. A 14B model in bf16 takes about 30 GB and leaves no room
  for the KV cache, so on one card run the official FP8 checkpoint (`Qwen/Qwen3-14B-FP8`, about
  16 GB) or an 8B model in bf16. Plan the submission with FP8 too, as two replicas, one per
  card of the challenge's 2 × 5090: each replica is then the same server measured here, and one
  card's wall time is a safe upper bound (about twice the submission's).
- CUDA 12.8 or newer (Blackwell needs it). Any PyTorch template with CUDA 12.8+ works, since
  `serve.sh` installs vLLM itself. Disk: about 100 GB (16 GB per FP8 14B model, 16 GB for an
  8B in bf16, 10–15 GB for vLLM and PyTorch, plus the template).
- Enable SSH with a public key. Port 8000 stays closed: vLLM listens on 127.0.0.1 and is reached
  through the tunnel.
- Note the hourly price: GPU hours are this phase's spend, and each run reports them.

Models to compare, in order, on one 5090:

| Model | Family | Weights | Notes |
|---|---|---|---|
| `google/gemma-4-12B-it` | `gemma4` | about 24 GB (bf16) | 11.95B params. Little KV cache left: `--workers` 2–3 |
| `google/gemma-4-12B-it-qat-w4a16-ct` | `gemma4` | about 8 GB (4-bit, Google's QAT) | the same model with room for more workers |
| `Qwen/Qwen3-14B-FP8` | `qwen3` | about 16 GB | thinking off (`VLLM_EXTRA_BODY`), then on |

Reports in 2026 said vLLM ran Gemma 4 12B through its generic Transformers backend rather than a
native implementation, which is slower; the server log names the backend. Gemma is not on vLLM's
list of models tested for batch invariance, so the preflight repeat check decides whether it can
meet the re-run rule. Gemma 4 may also write a tool call as plain text under long prompts: watch
`invalid_tool_call` and no-answer stops in the `regression` run.

## 2. Serve a model

Copy `scripts/gpu/serve.sh` to the box and run it in `tmux`:

```bash
bash serve.sh Qwen/Qwen3-14B-FP8 qwen3      # family picks the tool-call and reasoning parsers
```

On a vLLM template the script uses the `vllm` already installed; on a PyTorch template it
installs the latest vLLM in a venv on first use (5–10 min), which brings its own PyTorch. Gemma 4
needs a recent vLLM (its parser appeared around 0.19), so check the version it prints. It binds to 127.0.0.1 only, sets a fixed seed and
`VLLM_BATCH_INVARIANT=1`, and writes the vLLM version and GPUs to `~/vllm-logs/server-info.txt`.
Copy that file into the run's notes. Families and options are listed at the top of the script.
Gemma 4 uses the `gemma4` family. Gemma 3 and Phi-4 have no vLLM tool-call parser, so they
cannot drive the agent's tools through vLLM.

## 3. Tunnel and preflight

On the dev machine:

```bash
ssh -N -L 8000:127.0.0.1:8000 -p <port> root@<host>        # keep it open
export VLLM_BASE_URL=http://localhost:8000/v1
uv run python -m eval.gpu.preflight --routes vllm:Qwen/Qwen3-14B-FP8
```

Preflight uses synthetic prompts only. It fails when the model does not call an offered tool in
the parser's format, or when one prompt answers differently alone and amid a busy batch, which
would break the challenge's re-run check (±0.03). It also reports output tokens per second at 16
requests at once. Do not start an eval run on a failed preflight. vLLM lists batch invariance as tested on
bf16 models, not FP8 ones, so the repeat check matters most there: if FP8 fails it, try the
8B model in bf16.

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
uv run python -m eval.datamate.prepare --data-dir train --routes vllm:Qwen/Qwen3-14B-FP8 \
    --output-dir eval/reports/gpu-q14-nothink-prepare --max-cost 0.01 --workers 4
uv run python -m eval.ingestion.exclusions --labels eval/ingestion/labels/datamate_dev.jsonl \
    --datasets eval/reports/gpu-q14-nothink-prepare/datasets
```

A local model costs nothing in the ledger. `--max-cost 0` would skip every shop, since the
limit counts as reached at zero, so pass a small positive limit; token limits per shop still apply. Compare `summary.json` (built, problems, parse rates) and exclusion precision and
recall with `datasets-dev`.

**Answer the `regression` set** on those datasets:

```bash
uv run python -m eval.datamate.run --data-dir train --set regression --agent graph \
    --routes vllm:Qwen/Qwen3-14B-FP8 --datasets-dir eval/reports/gpu-q14-nothink-prepare/datasets \
    --output-dir eval/reports/gpu-q14-nothink-regression --max-total-tokens 20000000 --workers 4
```

`--workers` runs that many questions at once, which a local server needs to be used well. On one
5090 with an FP8 14B model, about 12 GB is left for the KV cache, roughly 75,000 tokens, and an
agent turn can send 10,000–15,000: start at 4 and raise it while `metrics.json` shows
`wall_seconds` falling. `EXTRA_ARGS="--kv-cache-dtype fp8"` doubles the cache, at some accuracy
risk, so measure it as its own setting. Each query also starts a
sandbox container on the dev machine, so its CPU can become the limit before the GPU. With
thinking on, raise `--max-tokens-per-question` (default 60,000), since thinking counts toward it. To separate the model from
the datasets, also run the same set on `eval/reports/datasets-dev`.

**Time a whole test split** with the best setting. Public-test questions have no answers here, so
the run is not scored; it measures wall time and how often the agent abstains or fails:

```bash
uv run python -m eval.datamate.prepare --data-dir public_test --all-shops \
    --routes vllm:Qwen/Qwen3-14B-FP8 --output-dir eval/reports/gpu-q14-public-prepare --max-cost 0.01 --workers 4
uv run python -m eval.datamate.run --data-dir public_test --all-questions --agent graph \
    --routes vllm:Qwen/Qwen3-14B-FP8 --datasets-dir eval/reports/gpu-q14-public-prepare/datasets \
    --output-dir eval/reports/gpu-q14-public --max-total-tokens 100000000 --workers 4
```

The challenge gives 6 hours on 2 × 5090 for model loading, ingestion, and every question.

## Session plan and time (one 5090)

From `final-regression`: a question takes about 5.4 model calls, 30,000 input and 2,700 output
tokens; the longest prompt is about 11,000 tokens. Preparing the 24 dev shops took 37 calls and
about 490,000 tokens. Decode speed dominates: a 12B model in bf16 decodes roughly 30–50 tokens
per second per request on one 5090, so the estimates below are rough, to be replaced by the
first measurement.

| Step | Estimate |
|---|---|
| Install vLLM and download a 24 GB model | 20–30 min, once per model |
| Preflight | 2–5 min |
| `regression --limit 20` on `datasets-dev`, to catch harness problems early | about 10 min |
| `regression` (150) on `datasets-dev` | 45–90 min in bf16 with 3 workers; less in 4-bit |
| `prepare` of the 24 dev shops with `--workers 4`, then the exclusion score | 15–30 min |

About 2–3 GPU hours per model; three models fit in a 6–8 hour rental. Run the agent on
`datasets-dev` first: it isolates the model's answering from its ingestion, and ingestion only
matters once a model answers well. At the same speed, the public test's 1,000 questions would
take about 6–7 hours on one card, about half on the challenge's two; time it with `--limit 100`
and extrapolate rather than running it all.

## 5. What to record

For each run, in `docs/eval/` as aggregates only: model and family, thinking on or off, vLLM
version, GPUs, preflight result, score and correct / wrong / abstained, stop reasons, wall time,
p50 and p95 latency, and GPU hours times the hourly price.

## 6. Tear down

Destroy the instance, with its disk, when the runs end. Nothing on it is needed afterwards:
the results are on the dev machine.
