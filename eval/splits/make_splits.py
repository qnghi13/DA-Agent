"""Split shops into dev and holdout, and draw question sets from dev only.

Regenerate with: uv run python -m eval.splits.make_splits --data-dir train

The committed splits.json is the source of truth. It holds only ids, so it reveals no answers or
answer types. Regenerate it only for a deliberate reason, because changing it invalidates baselines.
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.scorer.scoring import load_answer_types, load_jsonl

SEED = 20261105
HOLDOUT_SIZE = 6
SMOKE_SIZE = 30
REGRESSION_SIZE = 150
BOOTSTRAP_SHOP = "biz_123"
HOLDOUT_SET = "holdout"
HOLDOUT_SAMPLE_SIZE = 150
DEFAULT_OUTPUT = Path(__file__).with_name("splits.json")

# We read these shops closely while designing the project, so they cannot give an unseen result.
ALREADY_INSPECTED = frozenset({"biz_107", "biz_110", "biz_113", "biz_123", "biz_124"})

# Shops with much less data have far fewer questions, which makes the count a usable proxy.
LIMITED_DATA_SHARE = 0.5


@dataclass(frozen=True)
class Shop:
    id: str
    completeness: str
    file_format: str


@dataclass(frozen=True)
class Question:
    id: str
    shop: str
    kind: str


def _allocate(total: int, sizes: Mapping, minimum: int = 0) -> dict:
    """Split total across groups in proportion to size, using the largest-remainder method."""
    pool = sum(sizes.values())
    shares = {key: total * size / pool for key, size in sizes.items()}
    quotas = {key: min(sizes[key], max(minimum, int(shares[key]))) for key in sizes}
    by_remainder = sorted(sizes, key=lambda key: (quotas[key] - shares[key], str(key)))
    while sum(quotas.values()) < total:
        for key in by_remainder:
            if quotas[key] < sizes[key] and sum(quotas.values()) < total:
                quotas[key] += 1
    while sum(quotas.values()) > total:
        largest = max(sorted(quotas, key=str), key=lambda key: quotas[key] - shares[key])
        quotas[largest] -= 1
    return quotas


def assign_shops(
    shops: Iterable[Shop],
    holdout_size: int,
    seed: int,
    protected: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Return sorted (dev, holdout) shop ids. Holdout mirrors the mix of eligible shops."""
    all_shops = sorted(shops, key=lambda shop: shop.id)
    eligible = [shop for shop in all_shops if shop.id not in protected]
    if holdout_size > len(eligible):
        raise ValueError(f"holdout of {holdout_size} exceeds {len(eligible)} eligible shops")

    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for shop in eligible:
        strata[(shop.completeness, shop.file_format)].append(shop.id)

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    quotas = _allocate(holdout_size, {key: len(ids) for key, ids in strata.items()})
    holdout = {
        id_ for key in sorted(strata) for id_ in rng.sample(sorted(strata[key]), quotas[key])
    }
    dev = [shop.id for shop in all_shops if shop.id not in holdout]
    return dev, sorted(holdout)


def _spread_across_shops(questions: Sequence[Question], rng: random.Random) -> list[Question]:
    """Order questions so that taking any prefix covers shops as evenly as possible."""
    by_shop: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        by_shop[question.shop].append(question)
    shops = sorted(by_shop)
    rng.shuffle(shops)
    for shop in shops:
        rng.shuffle(by_shop[shop])

    ordered: list[Question] = []
    while len(ordered) < len(questions):
        for shop in shops:
            if by_shop[shop]:
                ordered.append(by_shop[shop].pop())
    return ordered


def sample_questions(questions: Iterable[Question], size: int, seed: int) -> list[str]:
    """Sample ids stratified by answer type and spread across shops. Rare types keep one."""
    pool = sorted(questions, key=lambda question: question.id)
    if size > len(pool):
        raise ValueError(f"sample of {size} exceeds the pool of {len(pool)} questions")

    by_kind: dict[str, list[Question]] = defaultdict(list)
    for question in pool:
        by_kind[question.kind].append(question)

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    quotas = _allocate(size, {kind: len(items) for kind, items in by_kind.items()}, minimum=1)
    chosen: list[str] = []
    for kind in sorted(by_kind):
        ordered = _spread_across_shops(by_kind[kind], rng)
        chosen.extend(question.id for question in ordered[: quotas[kind]])
    return sorted(chosen)


def build_question_sets(
    questions: Iterable[Question],
    dev_shops: Iterable[str],
    bootstrap_shop: str,
    seed: int,
    smoke: int,
    regression: int,
) -> dict[str, list[str]]:
    """Build the sets from dev shops only. Smoke is nested in regression for easy comparison."""
    dev = set(dev_shops)
    pool = [question for question in questions if question.shop in dev]
    by_id = {question.id: question for question in pool}

    regression_ids = sample_questions(pool, regression, seed)
    smoke_ids = sample_questions([by_id[id_] for id_ in regression_ids], smoke, seed)
    bootstrap_pool = [question for question in pool if question.shop == bootstrap_shop]
    return {
        "bootstrap": sample_questions(bootstrap_pool, smoke, seed),
        "smoke": smoke_ids,
        "regression": regression_ids,
    }


def holdout_question_ids(
    splits_path: Path, questions_path: Path, size: int = HOLDOUT_SAMPLE_SIZE
) -> list[str]:
    """The one-time holdout sample: questions from holdout shops, spread evenly across shops.

    It reads only question ids and shop ids, never answers, so the eval run can draw it too. The
    seed comes from splits.json, so the run and the scorer always get the same sample."""
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    holdout = set(splits["holdout_shops"])
    pool = [
        Question(row["id"], row["business_id"], kind="any")
        for row in load_jsonl(questions_path)
        if row["business_id"] in holdout
    ]
    return sample_questions(pool, min(size, len(pool)), splits["seed"])


def load_question_set(path: Path, name: str, questions_path: Path | None = None) -> set[str]:
    """Load one named question set. Holdout is not stored as a set: it is drawn from the questions
    file, so asking for it needs questions_path."""
    if name == HOLDOUT_SET:
        if questions_path is None:
            raise ValueError("the holdout set needs the questions file")
        return set(holdout_question_ids(path, questions_path))
    sets = json.loads(path.read_text(encoding="utf-8"))["question_sets"]
    if name not in sets:
        raise ValueError(f"unknown question set {name!r}, expected one of {sorted(sets)}")
    return set(sets[name])


def _describe_shops(data_dir: Path, question_counts: Counter[str]) -> list[Shop]:
    threshold = max(question_counts.values()) * LIMITED_DATA_SHARE
    shops = []
    for directory in sorted(path for path in (data_dir / "data").iterdir() if path.is_dir()):
        suffixes = Counter(
            path.suffix.lstrip(".").lower()
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".csv", ".xlsx", ".sqlite"}
        )
        completeness = "limited" if question_counts[directory.name] < threshold else "full"
        shops.append(Shop(directory.name, completeness, suffixes.most_common(1)[0][0]))
    return shops


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the eval splits.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = load_jsonl(args.data_dir / "questions.jsonl")
    kinds = load_answer_types(args.data_dir / "answers.jsonl")
    questions = [Question(row["id"], row["business_id"], kinds[row["id"]]) for row in rows]

    shops = _describe_shops(args.data_dir, Counter(question.shop for question in questions))
    dev, holdout = assign_shops(shops, HOLDOUT_SIZE, SEED, ALREADY_INSPECTED)
    sets = build_question_sets(questions, dev, BOOTSTRAP_SHOP, SEED, SMOKE_SIZE, REGRESSION_SIZE)

    output = {
        "seed": SEED,
        "dev_shops": dev,
        "holdout_shops": holdout,
        "question_sets": sets,
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(dev)} dev, {len(holdout)} holdout shops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
