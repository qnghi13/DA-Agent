"""Metric definitions read from a query's SQL, and matched against later queries.

A person saves a definition from an answer they trust (design 2026-11-15-metric-definitions). The
definition is the measure and the row filters of one query, read from its syntax tree, so it never
claims a filter the query did not apply. The period, grouping, ordering, and limit are left out:
"revenue" is computed the same way in May and in June.

Measures and filters are kept as canonical SQL, so two spellings of one condition compare equal:
qualifiers and aliases dropped, identifiers normalised, `!=` and `<>` alike, COUNT(1) as COUNT(*).
A condition inside the aggregate (FILTER, or a one-branch CASE) is a filter like any WHERE one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from da_agent.agent.verify import date_bound
from da_agent.reports.explain import (
    AGGREGATES,
    DATE_CONDITIONS,
    TRANSPARENT,
    Labels,
    explain_sql,
)

_AGGREGATE_TYPES = tuple(AGGREGATES)


@dataclass(frozen=True)
class Definition:
    """What a metric computes: on `table`, the `measure`, over rows that pass every filter."""

    table: str
    measure: str
    filters: list[str] = field(default_factory=list)
    # The ADR 0035 facts for the measure and the filters, labelled for people.
    facts: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "measure": self.measure,
            "filters": list(self.filters),
            "facts": self.facts,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Definition:
        return cls(
            table=str(data["table"]),
            measure=str(data["measure"]),
            filters=[str(f) for f in data.get("filters", [])],
            facts=dict(data.get("facts") or {}),
        )


def _parse(sql: str, dialect: str) -> exp.Select | None:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    return tree if isinstance(tree, exp.Select) else None


def _canonical(node: exp.Expression, dialect: str) -> str:
    node = node.copy()
    for column in node.find_all(exp.Column):
        column.set("table", None)
    for count in node.find_all(exp.Count):
        argument = count.this
        if isinstance(argument, exp.Literal) and not argument.is_string:
            count.set("this", exp.Star())
    return normalize_identifiers(node, dialect=dialect).sql(dialect=dialect, identify=True)


def _unwrap(node: exp.Expression) -> exp.Expression:
    """The value under an alias and wrappers that only change how it is shown."""
    while isinstance(node, (exp.Alias, exp.Coalesce, *TRANSPARENT)):
        node = node.this
    return node


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    parts, stack = [], [node]
    while stack:
        current = stack.pop()
        if isinstance(current, exp.Paren):
            stack.append(current.this)
        elif isinstance(current, exp.And):
            stack += [current.expression, current.this]
        else:
            parts.append(current)
    return parts


def _is_period(node: exp.Expression) -> bool:
    return isinstance(node, DATE_CONDITIONS) and date_bound(node) is not None


def _split(node: exp.Expression) -> tuple[exp.Expression, list[exp.Expression]]:
    """An aggregate with its condition taken out: SUM(x) FILTER (WHERE c) and
    SUM(CASE WHEN c THEN x ELSE 0 END) are both (SUM(x), [c]). Anything else is (node, [])."""
    if isinstance(node, exp.Filter) and isinstance(node.this, _AGGREGATE_TYPES):
        where = node.expression
        condition = where.this if isinstance(where, exp.Where) else where
        return node.this, _conjuncts(condition)
    if isinstance(node, _AGGREGATE_TYPES) and isinstance(node.this, exp.Case):
        case = node.this
        ifs = case.args.get("ifs") or []
        default = case.args.get("default")
        neutral = default is None or isinstance(default, exp.Null)
        if isinstance(node, exp.Sum) and isinstance(default, exp.Literal):
            neutral = neutral or default.this in {"0", "0.0"}
        if case.this is None and len(ifs) == 1 and neutral:
            base = node.copy()
            base.set("this", ifs[0].args["true"].copy())
            return base, _conjuncts(ifs[0].this)
    return node, []


def _single_table(select: exp.Select) -> str | None:
    """The table a plain single-table SELECT reads, or None for anything else."""
    if select.find(exp.With, exp.Subquery, exp.Window, exp.Join):
        return None
    source = select.args.get("from_") or select.args.get("from")
    if source is None or not isinstance(source.this, exp.Table):
        return None
    return source.this.name


def metric_candidates(
    sql: str, labels: Labels, visible: dict[str, set[str]], dialect: str = "duckdb"
) -> list[Definition]:
    """Each aggregate measure of a plain single-table query, as a definition. Empty when the
    query has a join, subquery, CTE, window, or set operation, or when any part of it cannot be
    explained in words. A measure that reads a column not in `visible` is left out."""
    select = _parse(sql, dialect)
    if select is None or (table := _single_table(select)) is None:
        return []
    explanation = explain_sql(sql, labels, dialect)
    if explanation.unexplained:
        return []
    where = select.args.get("where")
    row_filters = [c for c in _conjuncts(where.this if where else None) if not _is_period(c)]
    filter_facts = [f for f in explanation.filters if f.get("op") != "period"]
    items = [item for item in select.expressions if item.find(*_AGGREGATE_TYPES)]
    if len(items) != len(explanation.measures) or len(row_filters) != len(filter_facts):
        return []  # the explanation and the tree disagree; never pair the wrong facts
    allowed = {column.lower() for column in visible.get(table, set())}
    found = []
    for item, measure_fact in zip(items, explanation.measures, strict=True):
        base, inner = _split(_unwrap(item))
        inner_facts = list(measure_fact.get("condition") or []) if inner else []
        fact = measure_fact | {"condition": []} if inner else measure_fact
        fact = {k: v for k, v in fact.items() if k != "name"}
        definition = Definition(
            table=table,
            measure=_canonical(base, dialect),
            filters=[_canonical(c, dialect) for c in [*inner, *row_filters]],
            facts={"measure": fact, "filters": [*inner_facts, *filter_facts]},
        )
        if {c for _, c in definition_columns(definition, dialect)} <= allowed:
            found.append(definition)
    return found


def definition_columns(definition: Definition, dialect: str = "duckdb") -> set[tuple[str, str]]:
    """The (table, column) pairs a definition reads, lower-cased."""
    columns = set()
    for text in [definition.measure, *definition.filters]:
        try:
            node = sqlglot.parse_one(text, read=dialect)
        except sqlglot.errors.SqlglotError:
            continue
        columns |= {(definition.table.lower(), c.name.lower()) for c in node.find_all(exp.Column)}
    return columns


def _tables(select: exp.Select) -> set[str]:
    return {table.name.lower() for table in select.find_all(exp.Table)}


def uses_metric(sql: str, definition: Definition, dialect: str = "duckdb") -> bool:
    """True when the query computes the definition's measure on its table with every one of its
    filters, in WHERE or inside the aggregate. Extra filters, a period, or a grouping are fine."""
    select = _parse(sql, dialect)
    if select is None or definition.table.lower() not in _tables(select):
        return False
    where = select.args.get("where")
    outer = {_canonical(c, dialect) for c in _conjuncts(where.this if where else None)}
    wanted = set(definition.filters)
    for item in select.expressions:
        for node in item.find_all(exp.Filter, *_AGGREGATE_TYPES):
            if isinstance(node.parent, exp.Filter):
                continue  # read through the FILTER around it
            candidates = [(node, [])]
            base, inner = _split(node)
            if inner:
                candidates.append((base, inner))
            for measure, conditions in candidates:
                if _canonical(measure, dialect) != definition.measure:
                    continue
                present = outer | {_canonical(c, dialect) for c in conditions}
                if wanted <= present:
                    return True
    return False
