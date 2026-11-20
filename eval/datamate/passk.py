"""Run the DataMate eval k times and report how stable the answers are (ADR 0040).

    uv run --env-file .env python -m eval.datamate.passk --k 3 --data-dir train --set smoke \\
        --output-dir eval/reports/passk-smoke -- --routes groq:openai/gpt-oss-20b \\
        --max-total-tokens 2000000

Options after `--` go to eval.datamate.run unchanged. Each run writes OUTPUT/run-i and resumes
there when stopped. Scoring runs in a separate process, so this process never loads the answers.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from eval.datamate.run import DEFAULT_SPLITS
from eval.splits.make_splits import HOLDOUT_SET

REPO_ROOT = Path(__file__).resolve().parents[2]
POINTS = {"correct": 1, "wrong": -1, "abstain": 0}


def summarize(runs: Sequence[dict[str, str]], seeds: Sequence[int] = ()) -> dict[str, Any]:
    """pass^k (right in every run), pass@k (right in at least one), and the score's spread.

    Each run maps question id to outcome: correct, wrong, or abstain. A question missing from a
    run counts as abstained there."""
    if len(runs) < 2:
        raise ValueError("pass^k needs at least two runs")
    ids = sorted(set().union(*runs))
    outcomes = {id_: [run.get(id_, "abstain") for run in runs] for id_ in ids}
    per_run = []
    for run in runs:
        counts = [run.get(id_, "abstain") for id_ in ids]
        per_run.append(
            {
                "score": sum(POINTS[outcome] for outcome in counts),
                "correct": counts.count("correct"),
                "wrong": counts.count("wrong"),
                "abstained": counts.count("abstain"),
            }
        )
    scores = [run["score"] for run in per_run]
    n = len(ids) or 1
    return {
        "k": len(runs),
        "seeds": list(seeds),
        "questions": len(ids),
        "runs": per_run,
        "score_mean": statistics.mean(scores),
        "score_std": statistics.stdev(scores),
        "score_min": min(scores),
        "score_max": max(scores),
        "pass_hat_k": sum(all(o == "correct" for o in row) for row in outcomes.values()) / n,
        "pass_at_k": sum(any(o == "correct" for o in row) for row in outcomes.values()) / n,
        "mean_accuracy": sum(run["correct"] for run in per_run) / (n * len(runs)),
        "flaky": {
            id_: row
            for id_, row in outcomes.items()
            if "correct" in row and any(o != "correct" for o in row)
        },
        "always_wrong": [id_ for id_, row in outcomes.items() if set(row) == {"wrong"}],
        "always_abstain": [id_ for id_, row in outcomes.items() if set(row) == {"abstain"}],
    }


def run_passk(
    output_dir: Path,
    *,
    k: int,
    seed_base: int,
    run_one: Callable[[Path, int], None],
    score_one: Callable[[Path], dict[str, str]],
) -> dict[str, Any]:
    """Run and score k times into output_dir/run-1..k, then write output_dir/passk.json."""
    seeds = [seed_base + i for i in range(k)]
    runs = []
    for i, seed in enumerate(seeds, start=1):
        run_dir = output_dir / f"run-{i}"
        run_one(run_dir, seed)
        runs.append(score_one(run_dir))
    summary = summarize(runs, seeds)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "passk.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def _run_eval(args: argparse.Namespace, rest: list[str], run_dir: Path, seed: int) -> None:
    command = [
        sys.executable, "-m", "eval.datamate.run",
        "--data-dir", str(args.data_dir), "--set", args.question_set,
        "--splits", str(args.splits), "--no-score", *rest, "--output-dir", str(run_dir),
    ]  # fmt: skip
    # Only vLLM reads the seed; a fixed one would make every run the same.
    env = {**os.environ, "VLLM_SEED": str(seed)}
    print(f"run {run_dir.name} (seed {seed})", file=sys.stderr, flush=True)
    finished = subprocess.run(  # noqa: S603 - our own module and arguments
        command, env=env, cwd=REPO_ROOT, check=False, stdout=subprocess.DEVNULL
    )
    if finished.returncode != 0:
        raise ValueError(f"{run_dir.name} failed with exit code {finished.returncode}")


def _score(args: argparse.Namespace, run_dir: Path) -> dict[str, str]:
    submission = run_dir / "submission.jsonl"
    if not submission.exists():
        submission.touch()
    details = run_dir / "score_details.json"
    finished = subprocess.run(  # noqa: S603 - our own module and arguments
        [
            sys.executable,
            "-m",
            "eval.scorer.cli",
            "--answers",
            str(args.data_dir / "answers.jsonl"),
            "--submission",
            str(submission),
            "--splits",
            str(args.splits),
            "--set",
            args.question_set,
            "--questions",
            str(args.data_dir / "questions.jsonl"),
            "--details",
            str(details),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if finished.returncode != 0:
        raise ValueError(f"scorer failed on {run_dir.name}: {finished.stderr.strip()}")
    items = json.loads(details.read_text(encoding="utf-8"))
    return {item["id"]: item["outcome"] for item in items}


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, rest = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(description="Run the eval k times and report pass^k.")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--set", dest="question_set", required=True)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-base", type=int, default=0, help="run i uses VLLM_SEED base+i-1")
    args = parser.parse_args(argv)
    if args.question_set == HOLDOUT_SET:
        parser.error("the holdout set runs once, at the end; pass^k never runs it")
    if args.k < 2:
        parser.error("--k must be at least 2")
    if "--output-dir" in rest:
        parser.error("give --output-dir before --, not to the eval")
    try:
        summary = run_passk(
            args.output_dir,
            k=args.k,
            seed_base=args.seed_base,
            run_one=lambda run_dir, seed: _run_eval(args, rest, run_dir, seed),
            score_one=lambda run_dir: _score(args, run_dir),
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    shown = {key: value for key, value in summary.items() if key not in ("flaky", "always_wrong")}
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
