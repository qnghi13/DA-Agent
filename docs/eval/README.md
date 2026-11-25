# Evaluation results

Aggregates only. Raw runs stay in `eval/reports/` (gitignored), because they hold answers and cell
values. How the evaluation works is in [plan §3](../plan.md#3-evaluation).

## Setup

- **Data:** the DataMate benchmark from challenge.zalo.ai, 30 Vietnamese retail shops with messy xlsx, csv, and SQLite
  files, and 3,000 questions with typed answers. The core has no DataMate-specific code. An
  adapter in `eval/datamate/` turns the agent's answer into the benchmark format. The data is
  used for non-commercial, educational purposes under the challenge's rules and is not included
  or redistributed here: no rows, questions, rulebook text, or answers are in the repository.
- **Scoring:** +1 correct, −1 wrong, 0 abstained, so a wrong answer costs more than no answer. A
  separate scorer process is the only code that reads the answers.
- **Splits** (`eval/splits/splits.json`, fixed seed): 24 dev shops and 6 holdout shops,
  balanced by data completeness and file format. Shops read closely during design stay in dev.
  - `regression`: 150 dev questions, stratified by answer type and spread across shops. `smoke`
    (30) is nested in it.
  - `holdout`: 150 questions, 25 from each holdout shop, drawn from question ids only. It ran
    once, at the end, after all tuning.
- **Pipeline:** each shop's files are uploaded with the benchmark's file conventions as the
  description. The model proposes a dataset spec, code builds it in a sandbox, and evals approve
  it without review. The agent then answers with SQL in a sandbox.
- **Agent:** the LangGraph workflow (plan, tool loop, code verification, repair, abstain) on
  Groq `openai/gpt-oss-20b`, at most 60,000 tokens per question.

## Final results (2026-11-09)

| Set | Questions | Correct | Wrong | Abstained | Score | Cost | Latency p50 / p95 |
|---|---|---|---|---|---|---|---|
| `regression` (dev) | 150 | 93 (62%) | 40 (27%) | 17 (11%) | **+53** | $0.378 | 7.2 s / 19.9 s |
| `holdout` | 150 | 90 (60%) | 46 (31%) | 14 (9%) | **+44** | $0.304 | 5.9 s / 15.6 s |

- **Holdout is close to dev.** The score per question is 0.29 on holdout against 0.35 on dev. The
  standard error of each is about 0.07, so the gap is within noise. The work on dev shops did not
  overfit them in a way this sample can detect.
- **Cost:** about $0.002–0.0025 per question, with about 5 model calls and 30,000 tokens.
- **Preparing the 6 holdout shops** (spec proposals, builds, all in the sandbox) cost $0.016. All
  6 built, with every typed value parsed.
- **Stops on holdout:** 136 answered, 8 invalid tool calls, 4 iteration limits, 2 token budgets.
- **Wrong answers** are mostly a wrong value (39 of 46), not a wrong format (7).
- **Weak spots:** table answers (0 of 3 correct on holdout) and lists (6 of 13).

## How the score moved during development

On `bootstrap` (30 questions from one dev shop) unless noted. One run each, so differences of a
few points are noise: the model varies from run to run.

| Run | Change | Correct / wrong / abstained | Score |
|---|---|---|---|
| A | Tool loop on the original SQLite file | | −5 |
| B | Same, on data ingested from the raw files, in the sandbox | 3 / 14 / 13 | −11 |
| B4 | Code removes proven double entries during ingestion | 16 / 3 / 11 | +13 |
| B5 | Row descriptions in `describe_dataset`, status guidance in the prompt | 18 / 4 / 8 | +14 |
| S1 | B5 on `smoke` (30 questions, 19 shops), $0.06 | 13 / 6 / 11 | +7 |
| B7 | LangGraph workflow with plan, verify, and repair | 18 / 7 / 5 | +11 |
| S3 | B7 on `smoke` | 18 / 7 / 5 | +11 |
| B8 | B7 with every verification check | 17 / 7 / 6 | +10 |
| Final | Final code; the `smoke` questions inside the `regression` run | 20 / 6 / 4 | +14 |

On this shop, the largest change came from data preparation, not from the agent: removing double
entries that code can prove took correct answers from 3 to 16 of 30 and wrong ones from 14 to 3.
That is one shop with many double entries, so it shows what clean data can be worth, not an
average. The graph matched the simpler loop's total across three
sets (+31 each) for about 13% more tokens. It is kept for checkpoints, resumption, and
verification routing, not for score.

## Baseline: one-shot text-to-SQL (2026-11-24)

What the agent adds over a plain prompt ([ADR 0048](../adr/0048-oneshot-baseline.md)): the
model reads the schema, the whole rulebook, the date, and the question, writes one query, and
answers from its result. Two calls, no retry, no verification. It runs on the same prepared
datasets, so it already has the data preparation that moved the score most. Groq
`gpt-oss-20b`, `regression`, one run.

| Run | Correct | Wrong | Abstained | Score | Cost | Mean latency |
|---|---|---|---|---|---|---|
| One-shot baseline | 76 (51%) | 36 (24%) | 38 (25%) | +40 | $0.148 | 4.9 s |
| Agent, current code (same day) | 98 (65%) | 37 (25%) | 15 (10%) | **+61** | $0.331 | 11.7 s |
| Agent, `final-regression` (2026-11-09) | 93 (62%) | 40 (27%) | 17 (11%) | +53 | $0.378 | 9.1 s |

- **The agent adds +21.** That is about 2 standard deviations of the difference between two
  single runs (about 10 points on 150 questions, from the pass^k runs), so likely real, though one
  run each. The agent costs 2.2 times as much per question.
- **Most of it is coverage:** the agent answers 135 questions, the baseline 112. When they answer,
  73% of the agent's answers are right against 68% of the baseline's.
- **Why the baseline abstains:** 36 of its 38 abstentions are turns without a usable call, 33 of
  them on the answer turn. Offered only `submit_answer`, the model called another tool, which
  Groq rejects as malformed (23), or replied in text (10): it wanted another query. A recorded
  rerun confirmed it: 23 of its 24 rejected calls were `run_sql`. The agent's extra turns
  recover these.
- **Where the gap is:** numbers (+21 against +37) and lists (6 abstained against 4). Percentages,
  dates, and text score about the same.
- **What it means:** with prepared data and the rules in the prompt, one query already gets two
  thirds of the agent's score. The agent earns the rest mostly by answering more questions, and
  it is also what makes runs resumable and checked.
- **Current code against `final-regression`:** +61 against +53, within the noise of one run each.
  The changes since (code-computed periods, shorthand expansion, answer language of ADR 0047)
  show no harm on this set.

## Garbled tool calls (2026-11-24)

[ADR 0049](../adr/0049-garbled-tool-calls.md): when Groq rejects a call whose name is an offered
tool with `gpt-oss` format tokens attached (`submit_answer<|channel|>commentary`), code reads it
back as that tool's call, and a malformed call is retried once within its turn. Same code
otherwise, same day, `regression`, one run each, recorded.

| Run | Correct | Wrong | Abstained | Score | Stopped on a malformed call | Calls | Cost |
|---|---|---|---|---|---|---|---|
| Agent, before | 98 | 37 | 15 | +61 | 7 | 797 | $0.331 |
| Agent, after | 102 | 35 | 13 | **+67** | 2 | 747 | $0.280 |
| One-shot baseline, before | 76 | 36 | 38 | +40 | 25 | 263 | $0.148 |
| One-shot baseline, after | 75 | 33 | 42 | +42 | 24 | 263 | $0.137 |

- **The mechanism works:** 64 calls were read back in the agent run, runs stopped on a malformed
  call fell from 7 to 2, and the run used 50 fewer model calls (6%).
- **Not from this change:** the agent run also cost 15% less and its mean latency fell from 11.7 s
  to 9.1 s, but the baseline, which this change did not touch, cost 7% less and ran 24% faster the
  same day. Most of that is Groq's load at the time, not the fix.
- **The score:** +6 is within run-to-run noise (about 10 points), so the gain is the stops and
  calls above, not a score claim.
- **The baseline gains nothing:** its rejected calls are a real wish for another query, not a
  garbled name, and it does not get the retry, which is agent behaviour.
- **Still rejected in the agent run (29):** `json` 8, arguments that are not JSON 7, unparsable
  output 6, `run_sql` not offered 4, `answer` 2, others 2. Most were saved by the retry.

## Self-hosted model on one GPU (2026-11-13)

The challenge allows no hosted model, so the agent also runs on an open-weight model served
locally ([GPU runbook](gpu.md)): vLLM 0.33.0, `google/gemma-4-12B-it` in bf16 on one RTX 5090,
with batch-invariant inference. A preflight checked the server first: tool calls parse, and one
prompt gets the same answer alone and in a busy batch. Then `regression` on the same prepared
datasets, 3 questions at a time, one run.

| Run | Correct | Wrong | Abstained | Score | Model cost |
|---|---|---|---|---|---|
| Gemma 4 12B on one RTX 5090 | 99 (66%) | 43 (29%) | 8 (5%) | +56 | none (GPU time) |
| Groq `gpt-oss-20b`, later the same day | 97 (65%) | 41 (27%) | 12 (8%) | +56 | $0.27 |
| Groq `gpt-oss-20b`, `final-regression` (2026-11-09) | 93 (62%) | 40 (27%) | 17 (11%) | +53 | $0.378 |

- **A 12B model matches the hosted 20B one** on this set: the same score as the Groq run of the
  same day, which used about the same number of model calls per question (4.4). One run each,
  so this is a match within noise, not a win.
- **It abstains less and errs more:** 8 abstentions against 12, 43 wrong against 41.
- **Time:** 150 questions took 27.5 minutes of wall time, about 11 seconds per question with 3
  at a time.
- **Not measured yet:** the second GPU, a 4-bit model, and the code changes since.

## Metric definitions (2026-11-15)

What saved definitions ([ADR 0036](../adr/0036-metric-definitions.md)) change, on 20 `regression`
questions in 8 dev shops: 13 that `final-regression` got wrong and that ask for a metric a
definition covers (revenue, order count, cancelled orders), and 7 it got right, to catch harm.
Twelve definitions were read from correct `final-regression` answers to generic questions
("revenue in July?"), named as a shop owner would, and never from answers or holdout shops. A
question never reads a definition saved from its own answer. Both runs: graph agent, Groq
`gpt-oss-20b`, `datasets-dev`, `--metrics-file` only in the second.

| Run | Correct | Wrong | Abstain | Score | Cost |
|---|---:|---:|---:|---:|---:|
| Without definitions | 9 | 8 | 3 | +1 | $0.048 |
| With definitions | 12 | 5 | 3 | +7 | $0.047 |

Six questions improved and two got worse. Neither regression read a definition: one hit Groq's
`functions.submit_answer` tool-name error, the other used none. In 11 of 20 answers, code found a
saved definition in the SQL. Three still-wrong answers used one and failed elsewhere (a grouping
column that does not exist, which customer to rank). One run each, so +6 is a direction, not a
measurement; the standard eval reads no memory and is unchanged.

## Run-to-run noise: pass^k (2026-11-20)

`smoke` (30 questions) run 3 times on the same code (`eval.datamate.passk`, ADR 0040), Groq
`openai/gpt-oss-20b`, graph agent, $0.205 in all.

| Run | Correct | Wrong | Abstained | Score |
|---|---|---|---|---|
| 1 | 20 | 4 | 6 | +16 |
| 2 | 18 | 6 | 6 | +12 |
| 3 | 16 | 6 | 8 | +10 |

- **Score:** mean +12.7, standard deviation 3.1, range +10 to +16, on the same code.
- **pass^3** (right in all 3 runs) 50%, **pass@3** (right at least once) 67%, mean accuracy 60%.
  5 questions are flaky (right in some runs only), 3 always wrong, 4 always abstained.
- **What it means:** the difference between two single runs has a standard deviation of about
  3.1 x 1.41 = 4.3 points on 30 questions. A change measured with one run each needs about +9
  points on `smoke` to be told from noise at two standard deviations. Earlier single-run gains of a
  few points on 20 to 30 questions (such as metric definitions, +1 to +7) are within this noise.
  Changes after the model can be measured without it by replay (ADR 0039).

## Caveats

- One model, one run per set. Run-to-run variance of `gpt-oss-20b` is several points on 30
  questions.
- The benchmark covers one domain. The ingestion pipeline was also checked on 5 synthetic
  uploads (a clinic, English headers, clean names, note rows, totals above the data) for row
  exclusion only, without answer scoring.
- The first holdout launch pointed at the wrong datasets directory, so every shop failed to open
  and no question ran: no model call, and no change before the real run. The eval runner now
  fails such a run instead of scoring it.

## Spend

The whole project, from the first baseline to the final runs, cost about $2.59 in model calls,
plus $0.095 for the metric definitions check.
