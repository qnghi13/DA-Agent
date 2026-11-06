"""The DuckDB engine layer: limits that hold even when a query gets past the SQL guard."""

from pathlib import Path

import duckdb
import pytest

from da_agent.storage.duckdb_query import run_query

pytestmark = pytest.mark.security
CANARY = "ENGINE-CANARY-9081"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "dataset.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE sales AS SELECT range AS id, range * 10 AS amount, "
                           "repeat('x', 500) AS note FROM range(100)")  # fmt: skip
    (tmp_path / "secret.csv").write_text(f"a\n{CANARY}\n", encoding="utf-8")
    return path


def test_a_query_returns_bounded_rows_and_cells(database: Path) -> None:
    result = run_query(database, "SELECT id, amount, note FROM sales ORDER BY id", max_rows=5)

    assert result["columns"] == ["id", "amount", "note"]
    assert len(result["rows"]) == 5 and result["rows"][1][:2] == [1, 10]
    assert result["truncated"] is True
    assert len(result["rows"][0][2]) <= 201


def test_dates_and_decimals_become_json_values(database: Path) -> None:
    result = run_query(database, "SELECT DATE '2025-03-01' AS d, 1.5::DECIMAL(10, 2) AS x")

    assert result["rows"] == [["2025-03-01", 1.5]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('{dir}/secret.csv')",
        "SELECT * FROM '{dir}/secret.csv'",
        "SELECT content FROM read_text('{dir}/secret.csv')",
        "SELECT * FROM glob('{dir}/*')",
        "ATTACH '{dir}/other.duckdb' AS other",
        "COPY sales TO '{dir}/out.csv'",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "CREATE TABLE stolen AS SELECT 1",
        "DELETE FROM sales",
    ],
)
def test_the_engine_blocks_files_writes_and_settings_without_the_guard(
    database: Path, sql: str
) -> None:
    result = run_query(database, sql.format(dir=database.parent))

    assert "error" in result
    assert CANARY not in str(result)
    assert not (database.parent / "out.csv").exists()
    assert run_query(database, "SELECT count(*) FROM sales")["rows"] == [[100]]


def test_errors_are_short_messages_the_model_can_act_on(database: Path) -> None:
    result = run_query(database, "SELECT missing FROM sales")

    assert "missing" in result["error"] and len(result["error"]) <= 500
