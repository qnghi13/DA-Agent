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

## Baseline: one-shot text-to-SQL (2026-11-24)

What the agent adds over a plain prompt ([ADR 0048](../adr/0048-oneshot-baseline.md)): the
model reads the schema, the whole rulebook, the date, and the question, writes one query, and
answers from its result. Two calls, no retry, no verification. It runs on the same prepared
datasets, so it already has the data preparation that moved the score most. Groq
`gpt-oss-20b`, `regression`, one run.

| Run | Correct | Wrong | Abstained | Score | Cost | Mean latency |
|---|---|---|---|---|---|---|
| One-shot baseline | 76 (51%) | 36 (24%) | 38 (25%) | +40 | $0.148 | 4.9 s |
| Agent, `final-regression` (2026-11-09) | 93 (62%) | 40 (27%) | 17 (11%) | +53 | $0.378 | 9.1 s |

- **Equally accurate when it answers:** 68% of the baseline's answers are right, against 70% of
  the agent's. The agent's gain is coverage: it answers 133 questions, the baseline 112.
- **Why the baseline abstains:** 36 of its 38 abstentions are turns without a usable call, 33 of
  them on the answer turn. Offered only `submit_answer`, the model called another tool, which
  Groq rejects as malformed (23), or replied in text (10): it wanted another query. The agent's
  extra turns and nudges recover these.
- **Where the gap is:** numbers (+21 against +33) and lists (6 abstained against 1). Percentages,
  dates, and text score the same.
- **How sure:** +13 is about 1.3 standard deviations of the difference between two single runs
  (about 10 points on 150 questions, from the pass^k runs), and the agent ran on older code. It
  is a direction, not a measurement. The agent costs 2.6 times as much per question.
- **What it means:** with prepared data and the rules in the prompt, a good model gets most of
  the way in one query. The agent earns its cost by answering more questions, not by answering
  them better; it is also what makes runs resumable and checked.

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
