"""Score a submission file: uv run python -m eval.scorer.cli --answers A --submission S."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from eval.scorer.scoring import load_jsonl, score_submission
from eval.splits.make_splits import load_question_set


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a DataMate submission.")
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument(
        "--details", type=Path, help="write per-question outcomes to this JSON file"
    )
    parser.add_argument("--splits", type=Path, help="splits.json, used together with --set")
    parser.add_argument("--set", dest="question_set", help="score only this question set")
    parser.add_argument("--questions", type=Path, help="questions.jsonl, needed for --set holdout")
    args = parser.parse_args()
    if bool(args.splits) != bool(args.question_set):
        parser.error("--splits and --set must be used together")

    try:
        truths = load_jsonl(args.answers)
        if args.question_set:
            wanted = load_question_set(args.splits, args.question_set, args.questions)
            truths = [truth for truth in truths if truth["id"] in wanted]
        report = score_submission(truths, load_jsonl(args.submission))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    if args.details:
        items = [{**asdict(item), "outcome": item.outcome.value} for item in report.items]
        args.details.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
