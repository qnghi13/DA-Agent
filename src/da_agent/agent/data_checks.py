"""Checks that probe the data behind an answer. See docs/architecture.md, Verification.

Each check reads the evidence SQL with sqlglot and, for each SELECT that reads base tables, runs
one probe with the same FROM, JOIN, and WHERE. Filters, anti-joins, and periods are respected
because they are part of the probe. Probes only read and run where the agent's queries run. A
probe that fails is not a problem: these checks are guards, not gates.

- Fan-out: joining orders to their lines and summing the order total adds each order once per
  line. The probe counts how often each table's rows appear (count of rowid) against how many
  distinct rows there are. An aggregate is inflated only when every table it reads repeats, so a
  lookup price repeated once per line passes.
- Unreadable values: a value the build could not parse is empty in the clean table and listed in
  _quarantine by source row. A sum over it leaves it out without saying so. The probe counts such
  rows inside the rows the query reads, so a bad value in another month does not matter.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from da_agent.agent.tools import Dataset
from da_agent.storage.sqlite_dataset import QueryError

MAX_PROBES = 4
QUARANTINE = "_quarantine"
# Clauses that do not change which rows the FROM, JOIN, and WHERE produce.
DROPPED = ("group", "having", "order", "limit", "offset", "qualify", "distinct", "windows")
Schema = dict[str, set[str]]


@dataclass(frozen=True)
class _Select:
    tree: exp.Expression
    node: exp.Select
    # Aliases of the base tables it reads, mapped to table names.
    sources: dict[str, str]


def _schema(dataset: Dataset) -> Schema:
    return {t.name.lower(): {c.name.lower() for c in t.columns} for t in dataset.describe()}


def _sources(select: exp.Select, schema: Schema, ctes: set[str]) -> dict[str, str]:
    """Subqueries and CTEs are left out: they have no row ids or source rows."""
    found: dict[str, str] = {}
    from_ = select.args.get("from_")
    nodes = [from_.this] if from_ is not None else []
    nodes += [join.this for join in select.args.get("joins") or []]
    for node in nodes:
        if isinstance(node, exp.Table) and not node.args.get("db"):
            name = node.name.lower()
            if name in schema and name not in ctes:
                found[node.alias_or_name.lower()] = name
    return found


def _selects(sql: str, dialect: str, schema: Schema) -> Iterator[_Select]:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for select in tree.find_all(exp.Select):
        sources = _sources(select, schema, ctes)
        if sources:
            yield _Select(tree, select, sources)


def _owner(column: exp.Column, sources: dict[str, str], schema: Schema) -> str | None:
    """The alias a column belongs to, or None when it is not on one base table."""
    qualifier = column.table.lower()
    if qualifier:
        return qualifier if qualifier in sources else None
    owners = [a for a, table in sources.items() if column.name.lower() in schema[table]]
    return owners[0] if len(owners) == 1 else None


def _probe_sql(select: _Select, columns: list[exp.Expression], dialect: str) -> str:
    """The SELECT with its FROM, JOIN, and WHERE, and the given columns."""
    probe = select.node.copy()
    for key in DROPPED:
        probe.set(key, None)
    probe.set("expressions", columns)
    with_ = select.tree.args.get("with_")
    if probe.args.get("with_") is None and with_ is not None:
        probe.set("with_", with_.copy())
    return probe.sql(dialect=dialect)


def _first_row(dataset: Dataset, sql: str) -> list | None:
    try:
        result = dataset.query(sql)
    except QueryError:
        return None
    return result.rows[0] if result.rows else None


# Fan-out


@dataclass(frozen=True)
class _Aggregate:
    text: str
    aliases: frozenset[str]


def _read_by(aggregate: exp.Expression, sources: dict[str, str], schema: Schema) -> frozenset[str]:
    """The aliases an aggregate reads; empty when one cannot be placed on a base table."""
    if isinstance(aggregate.this, exp.Star):
        return frozenset(sources)
    aliases = set()
    for column in aggregate.find_all(exp.Column):
        alias = _owner(column, sources, schema)
        if alias is None:
            return frozenset()
        aliases.add(alias)
    return frozenset(aliases)


def _aggregates(select: _Select, schema: Schema) -> list[_Aggregate]:
    """Sums, averages, and counts in the SELECT list that repeated rows would change."""
    found = []
    for projection in select.node.expressions:
        for node in projection.find_all(exp.Sum, exp.Avg, exp.Count):
            if node.find_ancestor(exp.Select) is not select.node:
                continue
            if isinstance(node.parent, exp.Window):
                continue
            if isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct):
                continue
            aliases = _read_by(node, select.sources, schema)
            if aliases:
                found.append(_Aggregate(node.sql(), aliases))
    return found


def _fanout_problem(
    select: _Select, aggregates: list[_Aggregate], dataset: Dataset, dialect: str
) -> str | None:
    aliases = sorted(select.sources)
    columns: list[exp.Expression] = []
    for n, alias in enumerate(aliases):
        rowid = exp.column("rowid", table=alias)
        columns.append(exp.alias_(exp.Count(this=rowid), f"seen_{n}"))
        distinct = exp.Count(this=exp.Distinct(expressions=[rowid.copy()]))
        columns.append(exp.alias_(distinct, f"rows_{n}"))
    row = _first_row(dataset, _probe_sql(select, columns, dialect))
    if row is None:
        return None
    seen = {alias: row[2 * n] or 0 for n, alias in enumerate(aliases)}
    rows = {alias: row[2 * n + 1] or 0 for n, alias in enumerate(aliases)}
    for aggregate in aggregates:
        if all(seen[a] > rows[a] for a in aggregate.aliases):
            alias = min(aggregate.aliases)
            table = select.sources[alias]
            return (
                f"the join in the query behind the answer repeats rows of {table}: they appear "
                f"{seen[alias]} times for {rows[alias]} rows, so {aggregate.text} counts some rows "
                f"more than once; join {table} only on a key that is unique on the other side, or "
                "aggregate the other table first and join the result"
            )
    return None


def fanout_problems(sqls: list[str], dataset: Dataset, dialect: str) -> list[str]:
    """Problems for the queries behind an answer whose joins repeat the rows they aggregate."""
    schema = _schema(dataset)
    probes = 0
    for sql in sqls:
        for select in _selects(sql, dialect, schema):
            if not select.node.args.get("joins"):
                continue
            aggregates = _aggregates(select, schema)
            if not aggregates or probes >= MAX_PROBES:
                continue
            probes += 1
            if problem := _fanout_problem(select, aggregates, dataset, dialect):
                return [problem]
    return []


# Unreadable values


def _in_quarantine(alias: str, table: str, column: str) -> exp.Expression:
    """Counts the rows whose value of the column could not be read."""
    quarantined = exp.and_(
        exp.column("table_name", table="__q").eq(exp.Literal.string(table)),
        exp.column("column_name", table="__q").eq(exp.Literal.string(column)),
        exp.column("_source", table="__q").eq(exp.column("_source", table=alias)),
        exp.column("_row", table="__q").eq(exp.column("_row", table=alias)),
    )
    found = exp.select("1").from_(exp.to_table(QUARANTINE).as_("__q")).where(quarantined)
    flag = (
        exp.Case().when(exp.Exists(this=found), exp.Literal.number(1)).else_(exp.Literal.number(0))
    )
    return exp.Sum(this=flag)


def _unreadable_read(select: _Select, unparsed: set[tuple[str, str]], schema: Schema) -> list:
    """(alias, table, column) for each column with unreadable values that the SELECT reads."""
    found = []
    for column in select.node.find_all(exp.Column):
        if column.find_ancestor(exp.Select) is not select.node:
            continue
        alias = _owner(column, select.sources, schema)
        if alias is None:
            continue
        key = (alias, select.sources[alias], column.name.lower())
        if key[1:] in unparsed and key not in found:
            found.append(key)
    return found


def unreadable_problems(sqls: list[str], dataset: Dataset, dialect: str) -> list[str]:
    """Problems for the queries behind an answer that read rows whose values could not be read
    at load time."""
    tables = dataset.describe()
    unparsed = {(t.name.lower(), c.name.lower()) for t in tables for c in t.columns if c.unparsed}
    if not unparsed:
        return []
    schema = _schema(dataset)
    probes = 0
    for sql in sqls:
        for select in _selects(sql, dialect, schema):
            read = _unreadable_read(select, unparsed, schema)
            if not read or probes >= MAX_PROBES:
                continue
            probes += 1
            columns = [_in_quarantine(*key) for key in read]
            row = _first_row(dataset, _probe_sql(select, columns, dialect))
            for (_, table, column), count in zip(read, row or [], strict=False):
                if count:
                    return [
                        f"{count} rows that the query behind the answer reads have "
                        f"{table}.{column} values that could not be read when the data was "
                        "loaded, so the query treats them as empty and its result leaves them out"
                    ]
    return []
