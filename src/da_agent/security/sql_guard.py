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
    return sql.strip().rstrip(";").strip()
