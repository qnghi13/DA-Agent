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
| B | Same, on data ingested from the raw files, in the sandbox | | −11 |
| B4 | Code removes proven double entries during ingestion | | +13 |
| B5 | Row descriptions in `describe_dataset`, status guidance in the prompt | 18 / 4 / 8 | +14 |
| S1 | B5 on `smoke` (30 questions, 19 shops), $0.06 | 13 / 6 / 11 | +7 |
| B7 | LangGraph workflow with plan, verify, and repair | 18 / 7 / 5 | +11 |
| S3 | B7 on `smoke` | 18 / 7 / 5 | +11 |
| B8 | B7 with every verification check | 17 / 7 / 6 | +10 |
| Final | Final code; the `smoke` questions inside the `regression` run | 20 / 6 / 4 | +14 |

The largest gain came from data preparation, not from the agent. Removing double entries that
code can prove took B from −11 to +13. The graph matched the simpler loop's total across three
sets (+31 each) for about 13% more tokens. It is kept for checkpoints, resumption, and
verification routing, not for score.

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

The whole project, from the first baseline to the final runs, cost about $2.59 in model calls.
