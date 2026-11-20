# 0040: pass^k measures how stable eval answers are across runs

**Status:** Accepted, 2026-11-20. Design: [pass^k](../design/2026-11-20-pass-k.md).

**Context:** Groq results vary run to run, so a change of a few points cannot be told from noise.
One run says nothing about which questions the agent answers reliably.

**Decision:**
- `eval/datamate/passk.py` runs `eval.datamate.run` k times as subprocesses, into `run-1..k`, and
  scores each with `eval.scorer.cli --details` in its own process, so neither the agent nor this
  orchestrator process loads answers into the agent.
- `passk.json` reports pass^k (right in every run, as in tau-bench), pass@k (right at least once),
  mean accuracy, the score's mean, sample standard deviation, minimum and maximum, and the ids that
  are flaky, always wrong, or always abstained.
- Run i sets `VLLM_SEED` to `--seed-base + i - 1`, since a fixed seed makes vLLM runs identical.
- The holdout set is refused. Options after `--` go to the eval unchanged, so `--record` keeps a
  cassette per run (ADR 0039).

**Rejected:**
- `--repeat k` inside `run.py`: that file already mixes many concerns, and one folder per run keeps
  the existing resume.

**Consequences:**
- A stopped pass^k run continues: each eval resumes its folder, and finished runs cost nothing.
- k runs cost k times one run on a paid API. On vLLM they cost GPU time only.
- Per-question ids stay in gitignored `eval/reports/`; only aggregates go to `docs/eval/`.
