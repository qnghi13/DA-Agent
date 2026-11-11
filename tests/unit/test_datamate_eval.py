"""DataMate eval adapter and runner, on a small synthetic shop."""

import json
import sqlite3
from pathlib import Path

import pytest

from da_agent.agent.answer import AgentAnswer, AgentResult
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.llm.types import LLMRequest, LLMResponse, PermanentLLMError, ToolCall, Usage
from eval.datamate.adapter import (
    ShopContext,
    UnsupportedShopError,
    agent_rulebook,
    open_shop,
    rulebook_documents,
    to_submission,
)
from eval.datamate.run import RunConfig, run_questions, score_run, select_questions

CANARY = "ANSWER-CANARY-7731"


class ScriptedAdapter:
    provider = "fake"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def answer(value: int) -> LLMResponse:
    call = ToolCall(
        id="c", name="submit_answer", arguments={"kind": "number", "value_json": str(value)}
    )
    return LLMResponse(tool_calls=[call], usage=Usage(input_tokens=40, output_tokens=10))


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "train"
    shop = root / "data" / "biz_1"
    shop.mkdir(parents=True)
    (root / "data" / "biz_2").mkdir()
    (root / "data" / "biz_2" / "sales.xlsx").write_bytes(b"not used")
    (root / "data" / "biz_2" / "README.md").write_text("Shop 2", encoding="utf-8")
    (root / "data" / "biz_9").mkdir()
    (root / "rulebook.md").write_text("Revenue rule", encoding="utf-8")
    (root / "calendar.csv").write_text("date,holiday\n2026-01-01,New Year\n", encoding="utf-8")
    (shop / "README.md").write_text("Shop 1 notes", encoding="utf-8")
    connection = sqlite3.connect(shop / "pos.sqlite")
    connection.execute("CREATE TABLE khach_hang (TenGoi TEXT, cccd TEXT, SoDienThoai TEXT)")
    connection.execute("INSERT INTO khach_hang VALUES ('An', 'SECRET-ID', 'SECRET-PHONE')")
    connection.commit()
    connection.close()
    questions = [
        {"id": "q1", "business_id": "biz_1", "question": "One?", "as_of_date": "2026-01-02"},
        {"id": "q2", "business_id": "biz_1", "question": "Two?", "as_of_date": "2026-01-03"},
        {"id": "q3", "business_id": "biz_2", "question": "Three?", "as_of_date": "2026-01-04"},
        {"id": "q9", "business_id": "biz_9", "question": "Held out?", "as_of_date": "2026-01-05"},
    ]
    (root / "questions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in questions), encoding="utf-8"
    )
    truths = [
        {"id": "q1", "type": "number", "answer": 1, "alias": [CANARY]},
        {"id": "q2", "type": "number", "answer": 2, "alias": [CANARY]},
    ]
    (root / "answers.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in truths), encoding="utf-8"
    )
    return root


@pytest.fixture
def splits(tmp_path: Path) -> Path:
    path = tmp_path / "splits.json"
    path.write_text(
        json.dumps(
            {
                "seed": 7,
                "dev_shops": ["biz_1", "biz_2"],
                "holdout_shops": ["biz_9"],
                "question_sets": {
                    "good": ["q1", "q2"],
                    "mixed": ["q1", "q3"],
                    "leaky": ["q1", "q9"],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def make_llm(script: list) -> tuple[LLMClient, CostLedger, ScriptedAdapter]:
    adapter = ScriptedAdapter(script)
    ledger = CostLedger()
    client = LLMClient([Route(adapter, "m")], ledger, sleep=lambda _: None, max_retries=0)
    return client, ledger, adapter


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("kind", "value", "expected_type"),
    [
        ("number", 5, "number"),
        ("percent", 1.5, "percent"),
        ("date", "2026-01-01", "date"),
        ("boolean", True, "boolean"),
        ("text", "An", "string"),
        ("list", [1], "list"),
        ("table", {"columns": ["a"], "rows": [[1]]}, "table"),
    ],
)
def test_to_submission_maps_answer_kinds(kind: str, value: object, expected_type: str) -> None:
    result = AgentResult(answer=AgentAnswer(kind=kind, value=value))

    assert to_submission("q1", result) == {"id": "q1", "type": expected_type, "answer": value}


def test_to_submission_maps_unanswerable_with_a_string_reason() -> None:
    result = AgentResult(answer=AgentAnswer(kind="unanswerable", reason="No data"))

    assert to_submission("q1", result) == {
        "id": "q1",
        "type": "unanswerable",
        "reason": "No data",
    }


def test_to_submission_omits_a_missing_reason() -> None:
    result = AgentResult(answer=AgentAnswer(kind="unanswerable"))

    assert to_submission("q1", result) == {"id": "q1", "type": "unanswerable"}


def test_to_submission_abstains_without_an_answer() -> None:
    result = AgentResult(stop_reason="iteration_limit")

    assert to_submission("q1", result) == {"id": "q1", "type": "abstain"}


def test_open_shop_provides_documents_calendar_and_hides_personal_data(data_dir: Path) -> None:
    shop = open_shop(data_dir, "biz_1")

    names = {document.name for document in shop.documents}
    assert names == {"rulebook.md", "rulebook-topics.md", "README.md"}
    assert shop.dataset.query("SELECT holiday FROM calendar").rows == [["New Year"]]
    columns = {c.name for table in shop.dataset.describe() for c in table.columns}
    assert "TenGoi" in columns
    assert "cccd" not in columns
    assert "SoDienThoai" not in columns


def test_open_shop_rejects_shops_without_a_sqlite_file(data_dir: Path) -> None:
    with pytest.raises(UnsupportedShopError, match="biz_2"):
        open_shop(data_dir, "biz_2")


def test_select_questions_filters_by_set_and_limit(data_dir: Path, splits: Path) -> None:
    assert [q["id"] for q in select_questions(data_dir, splits, "good")] == ["q1", "q2"]
    assert [q["id"] for q in select_questions(data_dir, splits, "good", limit=1)] == ["q1"]


def test_select_questions_refuses_holdout_shops(data_dir: Path, splits: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        select_questions(data_dir, splits, "leaky")


def test_run_writes_submission_results_and_metrics(data_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    llm, ledger, _ = make_llm([answer(1), answer(99)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]

    metrics = run_questions(questions, data_dir, llm, ledger, RunConfig(output))

    assert read_jsonl(output / "submission.jsonl") == [
        {"id": "q1", "type": "number", "answer": 1},
        {"id": "q2", "type": "number", "answer": 99},
    ]
    results = read_jsonl(output / "results.jsonl")
    assert results[0]["id"] == "q1"
    assert results[0]["stop_reason"] == "answered"
    assert results[0]["total_tokens"] == 50
    assert "latency_seconds" in results[0]
    assert metrics["attempted"] == 2
    assert metrics["total_tokens"] == 100
    assert metrics["cost_usd"] == 0
    assert metrics["stop_reasons"] == {"answered": 2}
    assert json.loads((output / "metrics.json").read_text(encoding="utf-8")) == metrics


def test_run_scores_in_a_separate_step_through_the_scorer(
    data_dir: Path, splits: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out"
    llm, ledger, _ = make_llm([answer(1), answer(99)])
    questions = select_questions(data_dir, splits, "good")
    run_questions(questions, data_dir, llm, ledger, RunConfig(output))

    summary = score_run(data_dir / "answers.jsonl", output, splits, "good")

    assert (summary["correct"], summary["wrong"], summary["score"]) == (1, 1, 0)
    assert json.loads((output / "score.json").read_text(encoding="utf-8")) == summary


def test_unsupported_shops_are_skipped_and_reported(
    data_dir: Path, splits: Path, tmp_path: Path
) -> None:
    llm, ledger, adapter = make_llm([answer(1)])
    questions = select_questions(data_dir, splits, "mixed")

    metrics = run_questions(questions, data_dir, llm, ledger, RunConfig(tmp_path / "out"))

    assert metrics["attempted"] == 1
    assert metrics["skipped_unsupported"] == ["q3"]
    assert len(adapter.requests) == 1


def test_a_run_that_opens_no_shop_fails_instead_of_scoring_zero(
    data_dir: Path, splits: Path, tmp_path: Path
) -> None:
    llm, ledger, _ = make_llm([])
    questions = select_questions(data_dir, splits, "good")

    def no_dataset(shop: str) -> ShopContext:
        raise UnsupportedShopError(f"{shop} has no prepared dataset")

    with pytest.raises(ValueError, match="no shop could be opened"):
        run_questions(
            questions, data_dir, llm, ledger, RunConfig(tmp_path / "out"), open_shop_fn=no_dataset
        )


def test_resume_skips_finished_questions(data_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]
    llm, ledger, _ = make_llm([answer(1)])
    run_questions(questions[:1], data_dir, llm, ledger, RunConfig(output))

    llm, ledger, adapter = make_llm([answer(2)])
    metrics = run_questions(questions, data_dir, llm, ledger, RunConfig(output))

    assert len(adapter.requests) == 1
    assert [row["id"] for row in read_jsonl(output / "submission.jsonl")] == ["q1", "q2"]
    assert metrics["attempted"] == 2
    assert metrics["total_tokens"] == 100


def test_total_token_limit_stops_the_run_with_a_clear_status(
    data_dir: Path, tmp_path: Path
) -> None:
    llm, ledger, adapter = make_llm([answer(1), answer(2)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]

    metrics = run_questions(
        questions, data_dir, llm, ledger, RunConfig(tmp_path / "out", max_total_tokens=50)
    )

    assert metrics["stopped"] == "total_token_limit"
    assert metrics["attempted"] == 1
    assert len(adapter.requests) == 1


def test_question_budget_abstains_and_the_run_continues(data_dir: Path, tmp_path: Path) -> None:
    run_sql = LLMResponse(
        tool_calls=[ToolCall(id="c", name="run_sql", arguments={"sql": "SELECT 1"})],
        usage=Usage(input_tokens=40, output_tokens=10),
    )
    llm, ledger, _ = make_llm([run_sql, answer(2)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]

    run_questions(
        questions, data_dir, llm, ledger, RunConfig(tmp_path / "out", max_tokens_per_question=50)
    )

    rows = read_jsonl(tmp_path / "out" / "submission.jsonl")
    assert rows[0] == {"id": "q1", "type": "abstain"}
    assert rows[1]["answer"] == 2


def test_tokens_of_failed_questions_count_toward_reports_and_limits(
    data_dir: Path, tmp_path: Path
) -> None:
    run_sql = LLMResponse(
        tool_calls=[ToolCall(id="c", name="run_sql", arguments={"sql": "SELECT 1"})],
        usage=Usage(input_tokens=40, output_tokens=10),
    )
    llm, ledger, adapter = make_llm([run_sql, PermanentLLMError("too large"), answer(2)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]

    metrics = run_questions(
        questions, data_dir, llm, ledger, RunConfig(tmp_path / "out", max_total_tokens=50)
    )

    assert metrics["total_tokens"] == 50
    assert metrics["stopped"] == "total_token_limit"
    assert len(adapter.requests) == 2


def test_repeated_provider_failures_stop_the_run_and_stay_retryable(
    data_dir: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out"
    llm, ledger, adapter = make_llm([PermanentLLMError("daily limit")] * 5)
    questions = read_jsonl(data_dir / "questions.jsonl")[:2]

    metrics = run_questions(
        questions, data_dir, llm, ledger, RunConfig(output, max_consecutive_failures=1)
    )

    assert metrics["stopped"] == "provider_failures"
    assert metrics["failed"] == ["q1"]
    assert "daily limit" in metrics["failure_details"]["q1"]
    assert len(adapter.requests) == 1
    # Failed questions are not written, so a later run can retry them.
    assert not (output / "submission.jsonl").exists() or not read_jsonl(output / "submission.jsonl")


@pytest.mark.security
def test_answers_and_personal_data_never_reach_the_model(data_dir: Path, tmp_path: Path) -> None:
    describe = LLMResponse(tool_calls=[ToolCall(id="d", name="describe_dataset")])
    llm, ledger, adapter = make_llm([describe, answer(1)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]

    run_questions(questions, data_dir, llm, ledger, RunConfig(tmp_path / "out"))

    sent = " ".join(request.model_dump_json() for request in adapter.requests)
    assert CANARY not in sent
    assert "SECRET" not in sent
    assert "cccd" not in sent


@pytest.mark.security
def test_a_run_works_with_no_answers_file_present(data_dir: Path, tmp_path: Path) -> None:
    (data_dir / "answers.jsonl").unlink()
    llm, ledger, _ = make_llm([answer(1)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]

    metrics = run_questions(questions, data_dir, llm, ledger, RunConfig(tmp_path / "out"))

    assert metrics["attempted"] == 1


RULEBOOK = """\
## 7. Không trả lời được
Nộp `{"type": "unanswerable", "reason": "…"}`.
- Không có dữ liệu khác với bằng 0.

Trường `reason` không bắt buộc và không chấm tự động. Nếu có, `reason` phải là chuỗi.

## 9. Đáp án và chấm điểm
Mỗi dòng `submission.jsonl`: `{"id": "..."}`. Đúng **+1**, sai **\u22121**.

**Mỗi type có một format duy nhất; sai format là sai.** Tiền là số nguyên VND.
- **Làm tròn:** tiền ra số lẻ làm tròn về đồng.
"""


def test_agent_rulebook_drops_submission_and_scoring_mechanics() -> None:
    trimmed = agent_rulebook(RULEBOOK)

    assert "submission.jsonl" not in trimmed
    assert "+1" not in trimmed
    assert "chấm tự động" not in trimmed


def test_agent_rulebook_keeps_rules_that_shape_the_answer() -> None:
    trimmed = agent_rulebook(RULEBOOK)

    for rule in ("Không có dữ liệu khác với bằng 0", "Tiền là số nguyên VND", "Làm tròn"):
        assert rule in trimmed


TOPIC_RULEBOOK = """\
# Rulebook
Today is as_of_date.

## 1. Đơn hàng và doanh thu
Doanh thu không tính đơn huỷ.

## 7. Không trả lời được
Không có dữ liệu khác với bằng 0.

## 9. Đáp án và chấm điểm
Tiền là số nguyên VND.
"""


def test_rulebook_keeps_answer_rules_inline_and_topics_on_demand() -> None:
    inline, topics = rulebook_documents(TOPIC_RULEBOOK)

    assert not inline.on_demand
    assert topics.on_demand
    for rule in ("Today is as_of_date", "khác với bằng 0", "số nguyên VND"):
        assert rule in inline.text
    assert "Doanh thu không tính đơn huỷ" in topics.text
    assert "Doanh thu không tính đơn huỷ" not in inline.text


def test_holdout_set_is_refused_without_the_explicit_flag(data_dir: Path, splits: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        select_questions(data_dir, splits, "holdout")


def test_holdout_set_samples_only_holdout_questions_when_asked(
    data_dir: Path, splits: Path
) -> None:
    questions = select_questions(data_dir, splits, "holdout", holdout=True)

    assert [q["id"] for q in questions] == ["q9"]


def test_holdout_flag_does_not_open_holdout_shops_to_dev_sets(data_dir: Path, splits: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        select_questions(data_dir, splits, "leaky", holdout=True)


def test_holdout_run_is_scored_on_the_same_sample(
    data_dir: Path, splits: Path, tmp_path: Path
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "submission.jsonl").write_text(
        '{"id": "q9", "type": "number", "answer": 9}\n', encoding="utf-8"
    )
    answers = tmp_path / "answers.jsonl"
    answers.write_text(
        '{"id": "q9", "type": "number", "answer": 9, "alias": []}\n'
        '{"id": "q1", "type": "number", "answer": 1, "alias": []}\n',
        encoding="utf-8",
    )

    summary = score_run(
        answers, output, splits, "holdout", questions_path=data_dir / "questions.jsonl"
    )

    assert (summary["total"], summary["score"]) == (1, 1)


def test_prepare_selects_holdout_shops_only_when_asked(splits: Path) -> None:
    from eval.datamate.prepare import select_shops

    assert select_shops(splits, None, holdout=True) == ["biz_9"]
    with pytest.raises(ValueError, match="unknown"):
        select_shops(splits, ["biz_1"], holdout=True)


def test_prepare_refuses_holdout_and_unknown_shops(splits: Path) -> None:
    from eval.datamate.prepare import select_shops

    holdout = json.loads(splits.read_text(encoding="utf-8"))["holdout_shops"]
    with pytest.raises(ValueError, match="holdout"):
        select_shops(splits, [holdout[0]])
    with pytest.raises(ValueError, match="unknown"):
        select_shops(splits, ["biz_999"])


def test_prepare_takes_every_shop_of_another_split(splits: Path, tmp_path: Path) -> None:
    """A test split has its own shops, which the training splits file does not list."""
    from eval.datamate.prepare import shops_in

    for shop in ("dev_002", "dev_001"):
        (tmp_path / "data" / shop).mkdir(parents=True)
    (tmp_path / "data" / "notes.txt").write_text("not a shop", encoding="utf-8")

    assert shops_in(tmp_path, splits) == ["dev_001", "dev_002"]


def test_prepare_never_takes_training_holdout_shops_from_a_directory(
    splits: Path, tmp_path: Path
) -> None:
    from eval.datamate.prepare import shops_in

    holdout = json.loads(splits.read_text(encoding="utf-8"))["holdout_shops"]
    (tmp_path / "data" / holdout[0]).mkdir(parents=True)
    with pytest.raises(ValueError, match="holdout"):
        shops_in(tmp_path, splits)


def test_uploads_leave_out_windows_download_markers(tmp_path: Path) -> None:
    from eval.ingestion.prepare import copy_upload

    source = tmp_path / "shop"
    source.mkdir()
    (source / "Sales.xlsx").write_bytes(b"x")
    (source / "Sales.xlsx:Zone.Identifier").write_text("[ZoneTransfer]", encoding="utf-8")

    copy_upload(source, tmp_path / "upload")

    assert sorted(p.name for p in (tmp_path / "upload").iterdir()) == ["Sales.xlsx"]


def test_prepare_describes_shops_with_the_rulebook_file_conventions(tmp_path: Path) -> None:
    from eval.datamate.prepare import file_conventions

    (tmp_path / "rulebook.md").write_text(
        "# R\n\n## 7. Khác\nx\n\n## 8. Cách ghi trong file\nÔ để trống: giống dòng đầu nhóm.\n"
        "\n## 9. Đáp án\ny\n",
        encoding="utf-8",
    )

    text = file_conventions(tmp_path)

    assert "giống dòng đầu nhóm" in text
    assert "Đáp án" not in text


def test_the_calendar_travels_with_each_upload_and_gets_typed_columns(tmp_path: Path) -> None:
    from da_agent.ingestion.spec import DatasetSpec, TableSpec
    from eval.datamate.prepare import with_calendar
    from eval.ingestion.prepare import copy_upload

    source = tmp_path / "shop"
    source.mkdir()
    (source / "sales.csv").write_text("a\n1\n", encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("date,holiday\n2026-01-01,New Year\n", encoding="utf-8")

    copy_upload(source, tmp_path / "upload", extra_files=[calendar])
    spec = with_calendar(DatasetSpec(tables=[TableSpec(source="sales", name="sales")]))

    assert sorted(p.name for p in (tmp_path / "upload").iterdir()) == ["calendar.csv", "sales.csv"]
    [table] = [t for t in spec.tables if t.source == "calendar"]
    assert {c.name: c.type for c in table.columns}["date"] == "date"
    assert with_calendar(spec) == spec


def test_a_prepared_shop_queries_its_clean_dataset_through_the_runner(
    data_dir: Path, tmp_path: Path
) -> None:
    from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
    from da_agent.runner.runner import SqlResult
    from eval.datamate.adapter import open_prepared_shop

    datasets = tmp_path / "datasets"
    (datasets / "biz_1").mkdir(parents=True)
    report = BuildReport(
        tables=[TableBuild(name="sales", source="s", rows=3,
                           columns=[ColumnBuild(name="amount", source="Tiền", type="money")])]
    )  # fmt: skip
    (datasets / "biz_1" / "build_report.json").write_text(report.model_dump_json(), "utf-8")

    class FakeRunner:
        def __init__(self) -> None:
            self.requests: list = []

        def query(self, request):
            self.requests.append(request)
            return SqlResult(status="ok", columns=["n"], rows=[[3]])

    runner = FakeRunner()
    shop = open_prepared_shop(data_dir, "biz_1", datasets, runner)

    assert {d.name for d in shop.documents} == {"rulebook.md", "rulebook-topics.md", "README.md"}
    assert [t.name for t in shop.dataset.describe()] == ["sales"]
    assert shop.dataset.query("SELECT count(*) AS n FROM sales").rows == [[3]]
    assert runner.requests[0].dataset_id == "biz_1"
    with pytest.raises(UnsupportedShopError, match="biz_2"):
        open_prepared_shop(data_dir, "biz_2", datasets, runner)


def test_run_opens_shops_with_the_given_opener(data_dir: Path, tmp_path: Path) -> None:
    llm, ledger, _ = make_llm([answer(1)])
    questions = read_jsonl(data_dir / "questions.jsonl")
    opened: list[str] = []

    def opener(shop: str):
        opened.append(shop)
        if shop != "biz_1":
            raise UnsupportedShopError(shop)
        return open_shop(data_dir, shop)

    metrics = run_questions(
        questions[:1] + questions[2:3], data_dir, llm, ledger, RunConfig(tmp_path / "out"),
        open_shop_fn=opener,
    )  # fmt: skip

    assert opened == ["biz_1", "biz_2"]
    assert metrics["attempted"] == 1 and metrics["skipped_unsupported"] == ["q3"]


def test_select_questions_can_keep_only_some_shops(data_dir: Path, splits: Path) -> None:
    assert select_questions(data_dir, splits, "good", shops=["biz_2"]) == []
    assert [q["id"] for q in select_questions(data_dir, splits, "good", shops=["biz_1"])] == [
        "q1",
        "q2",
    ]


def test_results_keep_tool_errors_for_later_diagnosis(data_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    bad_sql = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="run_sql", arguments={"sql": "SELECT nope FROM x"})],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    llm, ledger, _ = make_llm([bad_sql, answer(1)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]

    run_questions(questions, data_dir, llm, ledger, RunConfig(output))

    [row] = read_jsonl(output / "results.jsonl")
    assert row["failed_tools"] == 1
    assert len(row["errors"]) == 1
    assert "nope" in row["errors"][0] or "x" in row["errors"][0]


def test_the_graph_agent_plans_queries_and_answers(data_dir: Path, tmp_path: Path) -> None:
    def tool(name: str, arguments: dict) -> LLMResponse:
        call = ToolCall(id=f"c_{name}", name=name, arguments=arguments)
        return LLMResponse(tool_calls=[call], usage=Usage(input_tokens=40, output_tokens=10))

    script = [
        tool("submit_plan", {"steps": ["count"]}),
        tool("run_sql", {"sql": "SELECT count(*) AS n FROM khach_hang"}),
        answer(1),
    ]
    llm, ledger, adapter = make_llm(script)
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]

    metrics = run_questions(
        questions, data_dir, llm, ledger, RunConfig(tmp_path / "out", agent="graph")
    )

    assert metrics["stop_reasons"] == {"answered": 1}
    assert read_jsonl(tmp_path / "out" / "submission.jsonl")[0]["answer"] == 1
    assert [tool.name for tool in adapter.requests[0].tools] == ["submit_plan"]


def test_graph_runs_keep_threads_apart_across_output_dirs() -> None:
    from eval.datamate.run import thread_id

    assert thread_id(Path("eval/reports/a"), "q1") != thread_id(Path("eval/reports/b"), "q1")


def test_graph_runs_can_write_a_report_per_question_with_charts(
    data_dir: Path, tmp_path: Path
) -> None:
    def tool(name: str, arguments: dict) -> LLMResponse:
        call = ToolCall(id=f"c_{name}", name=name, arguments=arguments)
        return LLMResponse(tool_calls=[call], usage=Usage(input_tokens=40, output_tokens=10))

    script = [
        tool("submit_plan", {"steps": ["count"]}),
        tool("run_sql", {"sql": "SELECT count(*) AS n FROM khach_hang"}),
        answer(1),
    ]
    llm, ledger, adapter = make_llm(script)
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]
    config = RunConfig(tmp_path / "out", agent="graph", reports=True)

    run_questions(questions, data_dir, llm, ledger, config)

    page = (tmp_path / "out" / "reports" / "q1.html").read_text(encoding="utf-8")
    assert 'href="#q1-r1-c1"' in page
    assert "make_chart" in [tool.name for tool in adapter.requests[1].tools]


def test_reports_need_the_graph_agent(data_dir: Path, tmp_path: Path) -> None:
    llm, ledger, _ = make_llm([])

    with pytest.raises(ValueError, match="graph"):
        run_questions([], data_dir, llm, ledger, RunConfig(tmp_path / "out", reports=True))
