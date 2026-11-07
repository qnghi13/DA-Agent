"""The join fan-out check: a sum over rows that a join repeats counts them more than once.

Joining orders to their lines and summing the order total adds each order once per line. The
check measures this instead of guessing it from key names: for each SELECT that aggregates across
a join, it runs one probe with the same FROM, JOIN, and WHERE, and counts how often each table's
rows appear (count of rowid) against how many distinct rows there are. Filters and anti-joins are
respected because they are part of the probe.

An aggregate is inflated only when every table it reads repeats. A lookup value repeated once per
line, as in sum(line.qty * product.price), is fine, because the line rows do not repeat.

Probes only read, run in the same sandbox as the agent's queries, and a probe that fails is not a
problem: the check is a guard, not a gate.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from da_agent.agent.tools import Dataset
from da_agent.storage.sqlite_dataset import QueryError

MAX_PROBES = 4
# Clauses that do not change which rows the join produces.
DROPPED = ("group", "having", "order", "limit", "offset", "qualify", "distinct", "windows")


@dataclass(frozen=True)
class _Aggregate:
    text: str
    aliases: frozenset[str]


@dataclass(frozen=True)
class _Probe:
    sql: str
    aliases: list[str]
    tables: dict[str, str]
    aggregates: list[_Aggregate]


def _sources(select: exp.Select, schema: dict[str, set[str]], ctes: set[str]) -> dict[str, str]:
    """Aliases of the base tables a SELECT joins, mapped to table names. Subqueries and CTEs are
    left out: they have no row ids."""
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


def _read_by(
    aggregate: exp.Expression, sources: dict[str, str], schema: dict[str, set[str]]
) -> frozenset[str] | None:
    """The aliases an aggregate reads, or None when one cannot be placed on a base table."""
    if isinstance(aggregate.this, exp.Star):
        return frozenset(sources)
    aliases: set[str] = set()
    for column in aggregate.find_all(exp.Column):
        qualifier = column.table.lower()
        if qualifier:
            if qualifier not in sources:
                return None
            aliases.add(qualifier)
            continue
        owners = [a for a, table in sources.items() if column.name.lower() in schema[table]]
        if len(owners) != 1:
            return None
        aliases.add(owners[0])
    return frozenset(aliases) or None


def _aggregates(
    select: exp.Select, sources: dict[str, str], schema: dict[str, set[str]]
) -> list[_Aggregate]:
    """Sums, averages, and counts in the SELECT list that repeated rows would change."""
    found = []
    for projection in select.expressions:
        for node in projection.find_all(exp.Sum, exp.Avg, exp.Count):
            if node.find_ancestor(exp.Select) is not select or isinstance(node.parent, exp.Window):
                continue
            if isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct):
                continue
            aliases = _read_by(node, sources, schema)
            if aliases:
                found.append(_Aggregate(node.sql(), aliases))
    return found


def _probe(
    select: exp.Select, tree: exp.Expression, sources: dict[str, str], dialect: str
) -> tuple[str, list[str]]:
    probe = select.copy()
    for key in DROPPED:
        probe.set(key, None)
    aliases = sorted(sources)
    columns: list[exp.Expression] = []
    for n, alias in enumerate(aliases):
        rowid = exp.column("rowid", table=alias)
        columns.append(exp.alias_(exp.Count(this=rowid), f"seen_{n}"))
        distinct = exp.Count(this=exp.Distinct(expressions=[rowid.copy()]))
        columns.append(exp.alias_(distinct, f"rows_{n}"))
    probe.set("expressions", columns)
    with_ = tree.args.get("with_")
    if probe.args.get("with_") is None and with_ is not None:
        probe.set("with_", with_.copy())
    return probe.sql(dialect=dialect), aliases


def fanout_probes(sql: str, dialect: str, schema: dict[str, set[str]]) -> list[_Probe]:
    """One probe for each SELECT that aggregates columns of base tables across a join."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return []
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    probes = []
    for select in tree.find_all(exp.Select):
        if not select.args.get("joins"):
            continue
        sources = _sources(select, schema, ctes)
        aggregates = _aggregates(select, sources, schema)
        if not aggregates:
            continue
        probe_sql, aliases = _probe(select, tree, sources, dialect)
        probes.append(_Probe(probe_sql, aliases, sources, aggregates))
    return probes


def _problem(probe: _Probe, row: list[int]) -> str | None:
    seen = {alias: row[2 * n] for n, alias in enumerate(probe.aliases)}
    rows = {alias: row[2 * n + 1] for n, alias in enumerate(probe.aliases)}
    for aggregate in probe.aggregates:
        if all(seen[a] > rows[a] for a in aggregate.aliases):
            alias = min(aggregate.aliases)
            table = probe.tables[alias]
            return (
                f"the join in the query behind the answer repeats rows of {table}: they appear "
                f"{seen[alias]} times for {rows[alias]} rows, so {aggregate.text} counts some rows "
                f"more than once; join {table} only on a key that is unique on the other side, or "
                "aggregate the other table first and join the result"
            )
    return None


def fanout_problems(sqls: list[str], dataset: Dataset, dialect: str) -> list[str]:
    """Problems for the queries behind an answer whose joins repeat the rows they aggregate."""
    schema = {t.name.lower(): {c.name.lower() for c in t.columns} for t in dataset.describe()}
    probes = [probe for sql in sqls for probe in fanout_probes(sql, dialect, schema)]
    for probe in probes[:MAX_PROBES]:
        try:
            result = dataset.query(probe.sql)
        except QueryError:
            continue
        if result.rows and (problem := _problem(probe, result.rows[0])):
            return [problem]
    return []
