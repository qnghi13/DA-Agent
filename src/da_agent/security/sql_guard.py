"""First SQL defence: accept exactly one read-only query.

The database engine enforces its own limits as well, so a parser bug here is not enough to get past
both layers.
"""

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

FORBIDDEN_NODES = (
    exp.Alter,
    exp.Attach,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Detach,
    exp.Drop,
    exp.Insert,
    exp.Merge,
    exp.Pragma,
    exp.Set,
    exp.TruncateTable,
    exp.Update,
)
FORBIDDEN_FUNCTIONS = frozenset(
    {"edit", "fts3_tokenizer", "load_extension", "readfile", "writefile", "zipfile"}
)


# DuckDB can read files, URLs, the environment, and its own settings through functions. The engine
# runs with external access off as well; this list stops such queries before they reach it.
DUCKDB_FORBIDDEN_FUNCTIONS = frozenset(
    {"getenv", "glob", "current_setting", "sniff_csv", "query", "query_table", "load_extension"}
)
DUCKDB_FORBIDDEN_PREFIXES = ("read_", "parquet_", "duckdb_", "pragma_", "sqlite_", "iceberg_")
# Table functions that only generate values.
DUCKDB_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})


class SqlRejectedError(ValueError):
    """The query is not allowed. The message is safe to show to the model."""


def _function_name(node: exp.Expr) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return None


def validate_select(sql: str, dialect: str = "sqlite") -> str:
    """Return the query without trailing semicolons, or raise SqlRejectedError."""
    try:
        statements = [statement for statement in parse(sql, read=dialect) if statement is not None]
    except SqlglotError as error:
        raise SqlRejectedError(f"could not parse the query: {error}") from error

    if len(statements) != 1:
        raise SqlRejectedError(f"expected one statement, found {len(statements)}")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise SqlRejectedError("only SELECT queries are allowed")

    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejectedError("only SELECT queries are allowed")
        if _function_name(node) in FORBIDDEN_FUNCTIONS:
            raise SqlRejectedError(f"function not allowed: {_function_name(node)}")
        if dialect == "duckdb":
            _check_duckdb(node)
    return sql.strip().rstrip(";").strip()


def _check_duckdb(node: exp.Expr) -> None:
    if isinstance(node, exp.ReadCSV):
        raise SqlRejectedError("reading files is not allowed; query the dataset's tables")
    name = _function_name(node)
    if name is not None and (
        name in DUCKDB_FORBIDDEN_FUNCTIONS or name.startswith(DUCKDB_FORBIDDEN_PREFIXES)
    ):
        raise SqlRejectedError(f"function not allowed: {name}")
    if isinstance(node, exp.Table):
        source = node.this
        if isinstance(source, exp.Identifier) and not _looks_like_path(source):
            return
        function = _function_name(source) if isinstance(source, exp.Expr) else None
        if function in DUCKDB_TABLE_FUNCTIONS:
            return
        raise SqlRejectedError(
            f"table {node.sql(dialect='duckdb')!r} is not allowed; query the dataset's tables "
            "by name"
        )


def _looks_like_path(identifier: exp.Identifier) -> bool:
    # DuckDB reads a quoted name such as '/etc/passwd' or 'x.csv' as a file.
    name = identifier.name
    return identifier.quoted and ("/" in name or "." in name or ":" in name)
