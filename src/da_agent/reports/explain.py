"""How an answer was calculated, read from its SQL. See docs/adr/0035.

Every fact here comes from the query's syntax tree, so the explanation cannot claim a filter the
query does not apply. Parts this reader cannot put in words are named in `unexplained`, and the
explanation is then incomplete: a partial reading is never shown as the whole. The web writes the
sentences; this module returns facts only, so translating the UI does not touch it.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from da_agent.agent.answer import AgentResult
from da_agent.agent.verify import date_bound, evidence_indices

logger = logging.getLogger(__name__)

AGGREGATES: dict[type[exp.Expr], str] = {
    exp.Sum: "sum",
    exp.Count: "count",
    exp.Avg: "avg",
    exp.Min: "min",
    exp.Max: "max",
    exp.Median: "median",
}
# Wrappers that change how a value is shown, not what it means.
TRANSPARENT = (exp.Round, exp.Cast, exp.TryCast, exp.Paren)
ARITHMETIC = {exp.Add: "add", exp.Sub: "subtract", exp.Mul: "multiply", exp.Div: "divide"}
COMPARISONS = {
    exp.EQ: "equals",
    exp.NEQ: "not_equals",
    exp.GT: "greater",
    exp.GTE: "at_least",
    exp.LT: "less",
    exp.LTE: "at_most",
}
DATE_CONDITIONS = (exp.Between, exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
FLIPPED = {"greater": "less", "at_least": "at_most", "less": "greater", "at_most": "at_least"}
FORMAT_UNITS = {"%Y": "year", "%Y-%m": "month", "%Y-%m-%d": "day"}
EXTRACT_UNITS = {
    "hour": "hour",
    "year": "year",
    "quarter": "quarter",
    "month": "month_of_year",
    "dow": "weekday",
}
FUNCTION_UNITS: dict[type[exp.Expr], str] = {
    exp.Year: "year",
    exp.Quarter: "quarter",
    exp.Month: "month_of_year",
    exp.DayOfWeek: "weekday",
}


@dataclass(frozen=True)
class Labels:
    """Names as people know them: tables and columns by their source in the uploaded files."""

    tables: dict[str, str] = field(default_factory=dict)
    columns: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_spec(cls, spec: dict[str, Any] | None) -> Labels:
        tables: dict[str, str] = {}
        columns: dict[str, dict[str, str]] = {}
        for table in (spec or {}).get("tables", []):
            name = str(table.get("name", ""))
            tables[name] = str(table.get("source") or name)
            columns[name] = {
                str(c.get("name")): str(c.get("source") or c.get("name"))
                for c in table.get("columns", [])
            }
        return cls(tables=tables, columns=columns)

    def table(self, name: str) -> str:
        return self.tables.get(name, self.tables.get(name.lower(), name))

    def column(self, name: str, tables: list[str]) -> str:
        for table in tables:
            known = {k.lower(): v for k, v in self.columns.get(table, {}).items()}
            if name.lower() in known:
                return known[name.lower()]
        return name


@dataclass(frozen=True)
class Explanation:
    tables: list[str] = field(default_factory=list)
    joins: list[dict[str, Any]] = field(default_factory=list)
    measures: list[dict[str, Any]] = field(default_factory=list)
    shown: list[dict[str, Any]] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    having: list[dict[str, Any]] = field(default_factory=list)
    order: list[dict[str, Any]] = field(default_factory=list)
    limit: int | None = None
    distinct_rows: bool = False
    unexplained: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unexplained

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"complete": self.complete}


def _ilike(match: str) -> str:
    """ILIKE ignores case on top of whatever the subject already ignores."""
    return "ignore_case_and_accents" if "accents" in match else "ignore_case"


class _Unexplained(Exception):
    """A part of the query this reader cannot put in words."""

    def __init__(self, part: str) -> None:
        super().__init__(part)
        self.part = part


class _Reader:
    def __init__(self, select: exp.Select, labels: Labels) -> None:
        self.labels = labels
        self.aliases: dict[str, str] = {}
        self.tables: list[str] = []
        self.unexplained: list[str] = []
        self.select = select

    # -- names --------------------------------------------------------------------------------

    def _register(self, table: exp.Table) -> str:
        name = table.name
        self.tables.append(name)
        self.aliases[name.lower()] = name
        if table.alias:
            self.aliases[table.alias.lower()] = name
        return name

    def _column_label(self, column: exp.Column) -> str:
        qualifier = column.table.lower()
        scope = [self.aliases[qualifier]] if qualifier in self.aliases else self.tables
        return self.labels.column(column.name, scope)

    def _label_by_name(self, name: str) -> str:
        return self.labels.column(name, self.tables)

    def _miss(self, part: str) -> None:
        if part not in self.unexplained:
            self.unexplained.append(part)

    # -- values -------------------------------------------------------------------------------

    @staticmethod
    def _literal(node: exp.Expr) -> Any:
        while isinstance(node, exp.Cast | exp.TryCast | exp.Paren):
            node = node.this
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            value = _Reader._literal(node.this)
            return -value
        if isinstance(node, exp.Boolean):
            return bool(node.this)
        if isinstance(node, exp.Literal):
            if node.is_string:
                return str(node.this)
            number = float(node.this)
            return int(number) if number.is_integer() and "." not in str(node.this) else number
        raise _Unexplained("condition")

    def _unit(self, node: exp.Expr) -> tuple[exp.Expr, str | None]:
        """A time bucket around a column, as (column node, unit)."""
        if isinstance(node, exp.DateTrunc | exp.TimestampTrunc):
            unit = str(node.args.get("unit") or "").lower().strip("'")
            return node.this, unit or None
        if isinstance(node, exp.TimeToStr):
            column, fmt = node.this, node.args.get("format")
            if isinstance(column, exp.Literal):  # strftime('%Y-%m', d) in DuckDB's other order
                column, fmt = fmt, column
            text = fmt.this if isinstance(fmt, exp.Literal) else None
            if column is not None and text in FORMAT_UNITS:
                return column, FORMAT_UNITS[str(text)]
            raise _Unexplained("expression")
        if isinstance(node, exp.Extract):
            unit = node.this.name.lower() if isinstance(node.this, exp.Var) else ""
            if unit in EXTRACT_UNITS:
                return node.expression, EXTRACT_UNITS[unit]
            raise _Unexplained("expression")
        for kind, unit in FUNCTION_UNITS.items():
            if isinstance(node, kind):
                return node.this, unit
        if isinstance(node, exp.Cast | exp.TryCast | exp.Date | exp.TsOrDsToDate):
            target = node.args.get("to")
            if target is None or target.is_type(exp.DataType.Type.DATE):
                return node.this, "day"
        return node, None

    # -- expressions --------------------------------------------------------------------------

    def _resolve(self, node: exp.Expr) -> exp.Expr:
        """An ordinal or a select alias in GROUP BY or ORDER BY, as the selected expression."""
        items = self.select.expressions
        if isinstance(node, exp.Literal) and not node.is_string:
            position = int(node.this)
            if 1 <= position <= len(items):
                return items[position - 1]
        if isinstance(node, exp.Column) and not node.table:
            for item in items:
                if isinstance(item, exp.Alias) and item.alias.lower() == node.name.lower():
                    return item
        return node

    def expr(self, node: exp.Expr, name: str | None = None) -> dict[str, Any]:
        if isinstance(node, exp.Alias):
            return self.expr(node.this, node.alias)
        if isinstance(node, exp.Window):
            raise _Unexplained("window")
        if isinstance(node, exp.Subquery | exp.Select):
            raise _Unexplained("subquery")
        if isinstance(node, TRANSPARENT):
            unit_node, unit = self._unit(node)
            if unit is None:
                return self.expr(node.this, name)
            return self._column(unit_node, unit, name)
        if isinstance(node, exp.Coalesce):
            return self.expr(node.this, name)
        if isinstance(node, exp.Filter):
            inner = self.expr(node.this, name)
            if inner["kind"] != "aggregate":
                raise _Unexplained("expression")
            where = node.expression
            condition = where.this if isinstance(where, exp.Where) else where
            return inner | {"condition": inner["condition"] + self.conditions(condition)}
        for kind, func in AGGREGATES.items():
            if isinstance(node, kind):
                return self._aggregate(node, func, name)
        for kind, op in ARITHMETIC.items():
            if isinstance(node, kind):
                return {
                    "kind": "arithmetic",
                    "op": op,
                    "left": self.expr(node.this),
                    "right": self.expr(node.expression),
                    "name": name,
                }
        if isinstance(node, exp.Literal) and not node.is_string:
            return {"kind": "number", "value": self._literal(node), "name": name}
        if isinstance(node, exp.Star):
            return {"kind": "all", "name": name}
        unit_node, unit = self._unit(node)
        return self._column(unit_node, unit, name)

    def _column(self, node: exp.Expr, unit: str | None, name: str | None) -> dict[str, Any]:
        while isinstance(node, exp.Paren):
            node = node.this
        if not isinstance(node, exp.Column) or isinstance(node.this, exp.Star):
            raise _Unexplained("expression")
        found = {"kind": "column", "column": self._column_label(node), "unit": unit}
        return found | ({"name": name} if name is not None else {})

    def _aggregate(self, node: exp.Expr, func: str, name: str | None) -> dict[str, Any]:
        argument = node.this
        distinct = isinstance(argument, exp.Distinct)
        if distinct:
            if len(argument.expressions) != 1:
                raise _Unexplained("expression")
            argument = argument.expressions[0]
        condition: list[dict[str, Any]] = []
        of: dict[str, Any] | None
        if argument is None or isinstance(argument, exp.Star):
            of = None
        elif isinstance(argument, exp.Case):
            of, condition = self._case(argument, func)
        else:
            of = self.expr(argument)
            if of["kind"] == "number" and func == "count":
                of = None
        found = {
            "kind": "aggregate",
            "func": func,
            "of": of,
            "distinct": distinct,
            "condition": condition,
        }
        return found | ({"name": name} if name is not None else {})

    def _case(
        self, case: exp.Case, func: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """SUM(CASE WHEN c THEN x ELSE 0 END) and COUNT(CASE WHEN c THEN 1 END): x where c."""
        ifs = case.args.get("ifs") or []
        default = case.args.get("default")
        if case.this is not None or len(ifs) != 1:
            raise _Unexplained("expression")
        neutral = default is None or isinstance(default, exp.Null)
        if not neutral and func == "sum":
            neutral = isinstance(default, exp.Literal) and self._literal(default) == 0
        if not neutral:
            raise _Unexplained("expression")
        branch = ifs[0]
        then = self.expr(branch.args["true"])
        of = None if func == "count" and then["kind"] == "number" else then
        return of, self.conditions(branch.this)

    # -- conditions ---------------------------------------------------------------------------

    def conditions(self, node: exp.Expr | None) -> list[dict[str, Any]]:
        if node is None:
            return []
        parts: list[exp.Expr] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, exp.Paren):
                stack.append(current.this)
            elif isinstance(current, exp.And):
                stack += [current.expression, current.this]
            else:
                parts.append(current)
        found: list[dict[str, Any]] = []
        periods: dict[str, dict[str, Any]] = {}
        for part in parts:
            bound = date_bound(part) if isinstance(part, DATE_CONDITIONS) else None
            if bound is not None and not part.find(exp.Subquery):
                self._merge_period(bound, found, periods)
                continue
            found.append(self._condition(part))
        return found

    def _merge_period(
        self,
        bound: tuple[str, Any, Any],
        found: list[dict[str, Any]],
        periods: dict[str, dict[str, Any]],
    ) -> None:
        column, start, end = bound
        current = periods.get(column)
        if current is None:
            current = {
                "of": {"kind": "column", "column": self._label_by_name(column), "unit": None},
                "op": "period",
                "values": [None, None],
                "match": "exact",
            }
            periods[column] = current
            found.append(current)
        low, high = current["values"]
        if start is not None:
            text = start.isoformat()
            current["values"][0] = text if low is None else max(low, text)
        if end is not None:
            text = end.isoformat()
            current["values"][1] = text if high is None else min(high, text)

    def _subject(self, node: exp.Expr) -> tuple[dict[str, Any], str]:
        """The left side of a condition, and how it matches: lower() or upper() ignores case,
        strip_accents() ignores accents."""
        case = accents = False
        while True:
            if isinstance(node, exp.Lower | exp.Upper):
                case, node = True, node.this
            elif isinstance(node, exp.Anonymous) and node.name.lower() == "strip_accents":
                if len(node.expressions) != 1:
                    raise _Unexplained("condition")
                accents, node = True, node.expressions[0]
            else:
                break
        match = {
            (False, False): "exact",
            (True, False): "ignore_case",
            (False, True): "ignore_accents",
            (True, True): "ignore_case_and_accents",
        }[(case, accents)]
        return self.expr(node), match

    def _condition(self, node: exp.Expr) -> dict[str, Any]:
        if node.find(exp.Subquery, exp.Select):
            raise _Unexplained("subquery")
        if isinstance(node, exp.Not):
            inner = self._condition(node.this)
            negated = {
                "in": "not_in",
                "is_blank": "is_not_blank",
                "equals": "not_equals",
                "is_true": "is_false",
            }
            if inner["op"] not in negated:
                raise _Unexplained("condition")
            return inner | {"op": negated[inner["op"]]}
        if isinstance(node, exp.Or):
            return self._either(node)
        if isinstance(node, exp.Column):  # a yes/no column used as the condition itself
            return self._filter(self.expr(node), "is_true", [], "exact")
        if isinstance(node, exp.Is):
            subject, match = self._subject(node.this)
            if not isinstance(node.expression, exp.Null):
                raise _Unexplained("condition")
            return self._filter(subject, "is_blank", [], match)
        if isinstance(node, exp.In):
            subject, match = self._subject(node.this)
            values = [self._literal(v) for v in node.expressions]
            return self._filter(subject, "in", values, match)
        if isinstance(node, exp.Between):
            subject, match = self._subject(node.this)
            values = [self._literal(node.args["low"]), self._literal(node.args["high"])]
            return self._filter(subject, "between", values, match)
        if isinstance(node, exp.Like | exp.ILike):
            subject, match = self._subject(node.this)
            if isinstance(node, exp.ILike):
                match = _ilike(match)
            return self._like(subject, node, match)
        for kind, op in COMPARISONS.items():
            if isinstance(node, kind):
                return self._compare(node, op)
        raise _Unexplained("condition")

    def _compare(self, node: exp.Expr, op: str) -> dict[str, Any]:
        left, right = node.this, node.expression
        value: Any
        try:
            value = self._literal(right)
        except _Unexplained:
            try:
                value = self._literal(left)
                left = right
                op = FLIPPED.get(op, op)
            except _Unexplained:  # two columns compared, such as total > amount_paid
                value = {k: v for k, v in self.expr(right).items() if k != "name"}
        subject, match = self._subject(left)
        return self._filter(subject, op, [value], match)

    def _like(self, subject: dict[str, Any], node: exp.Expr, match: str) -> dict:
        pattern = self._literal(node.expression)
        if not isinstance(pattern, str):
            raise _Unexplained("condition")
        core = pattern.strip("%")
        if "%" in core or "_" in core:
            raise _Unexplained("condition")
        starts, ends = pattern.startswith("%"), pattern.endswith("%")
        op = {(True, True): "contains", (False, True): "starts_with", (True, False): "ends_with"}
        return self._filter(subject, op.get((starts, ends), "equals"), [core], match)

    def _either(self, node: exp.Or) -> dict[str, Any]:
        """a = 1 OR a = 2 reads as a in (1, 2), and LIKEs of one kind on one column as one filter
        with several values. Any other OR is left to the SQL tab."""
        parts: list[exp.Expr] = []
        stack: list[exp.Expr] = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, exp.Paren):
                stack.append(current.this)
            elif isinstance(current, exp.Or):
                stack += [current.expression, current.this]
            else:
                parts.append(current)
        read = [self._condition(part) for part in parts]
        first = read[0]
        same = all(r["of"] == first["of"] and r["match"] == first["match"] for r in read)
        ops = {r["op"] for r in read}
        if not same:
            raise _Unexplained("condition")
        if ops <= {"equals", "in"}:
            op = "in"
        elif len(ops) == 1 and ops <= {"contains", "starts_with", "ends_with"}:
            op = first["op"]
        else:
            raise _Unexplained("condition")
        values = [v for r in read for v in r["values"]]
        return self._filter(first["of"], op, values, first["match"])

    @staticmethod
    def _filter(of: dict[str, Any], op: str, values: list[Any], match: str) -> dict:
        of = {k: v for k, v in of.items() if k != "name"}
        return {"of": of, "op": op, "values": values, "match": match}

    # -- clauses ------------------------------------------------------------------------------

    def _try(self, read: Any, *args: Any) -> Any:
        try:
            return read(*args)
        except _Unexplained as missing:
            self._miss(missing.part)
            return None

    def _joins(self) -> list[dict[str, Any]]:
        found = []
        for join in self.select.args.get("joins") or []:
            target = join.this
            if not isinstance(target, exp.Table):
                self._miss("subquery")
                continue
            name = self._register(target)
            kind = (join.side or join.kind or "inner").lower()
            pairs = self._try(self._join_pairs, join.args.get("on"), join.args.get("using"))
            found.append({"table": self.labels.table(name), "kind": kind, "on": pairs or []})
        return found

    def _join_pairs(self, on: exp.Expr | None, using: list[exp.Expr] | None) -> list[list[str]]:
        if using:
            return [[self._label_by_name(u.name)] * 2 for u in using]
        if on is None:
            return []
        pairs = []
        stack = [on]
        while stack:
            current = stack.pop()
            if isinstance(current, exp.And):
                stack += [current.expression, current.this]
            elif (
                isinstance(current, exp.EQ)
                and isinstance(current.this, exp.Column)
                and isinstance(current.expression, exp.Column)
            ):
                pairs.append(
                    [self._column_label(current.this), self._column_label(current.expression)]
                )
            else:
                raise _Unexplained("join")
        return list(reversed(pairs)) if len(pairs) > 1 else pairs

    def read(self) -> Explanation:
        select = self.select
        source = select.args.get("from_") or select.args.get("from")
        tables: list[str] = []
        if source is not None:
            if isinstance(source.this, exp.Table):
                tables.append(self.labels.table(self._register(source.this)))
            else:
                self._miss("subquery")
        joins = self._joins()
        measures: list[dict[str, Any]] = []
        shown: list[dict[str, Any]] = []
        for item in select.expressions:
            found = self._try(self.expr, item)
            if found is None:
                continue
            (measures if item.find(*AGGREGATES) else shown).append(found)
        where = select.args.get("where")
        filters = self._try(self.conditions, where.this if where else None) or []
        group = select.args.get("group")
        groups = []
        for node in group.expressions if group else []:
            found = self._try(self.expr, self._resolve(node))
            if found is not None:
                groups.append({k: v for k, v in found.items() if k != "name"})
        having = select.args.get("having")
        having_filters = self._try(self.conditions, having.this if having else None) or []
        order = []
        ordered = select.args.get("order")
        for node in ordered.expressions if ordered else []:
            found = self._try(self.expr, self._resolve(node.this))
            if found is not None:
                order.append({"by": found, "descending": bool(node.args.get("desc"))})
        return Explanation(
            tables=tables,
            joins=joins,
            measures=measures,
            shown=shown,
            filters=filters,
            groups=groups,
            having=having_filters,
            order=order,
            limit=self._limit(),
            distinct_rows=bool(select.args.get("distinct")),
            unexplained=self.unexplained,
        )

    def _limit(self) -> int | None:
        limit = self.select.args.get("limit")
        value = limit.expression if limit is not None else None
        if isinstance(value, exp.Literal) and not value.is_string:
            return int(value.this)
        return None


def explain_sql(sql: str, labels: Labels | None = None, dialect: str = "duckdb") -> Explanation:
    """What a query does, as facts a person can read without knowing SQL."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return Explanation(unexplained=["parse"])
    if isinstance(tree, exp.SetOperation):
        return Explanation(unexplained=["set_operation"])
    if not isinstance(tree, exp.Select):
        return Explanation(unexplained=["parse"])
    unexplained = []
    if tree.find(exp.With):
        unexplained.append("with")
    if tree.find(exp.Subquery):
        unexplained.append("subquery")
    if tree.find(exp.Window):
        unexplained.append("window")
    found = _Reader(tree, labels or Labels()).read()
    parts = list(dict.fromkeys(unexplained + found.unexplained))
    return Explanation(**{**asdict(found), "unexplained": parts})


def explain_result(result: AgentResult, labels: Labels, dialect: str = "duckdb") -> list[dict]:
    """The answer's evidence queries, each explained, with its position in `result.queries`.
    Empty when there is no answer: nothing was concluded, so there is nothing to explain."""
    if result.answer is None:
        return []
    return [
        {"query_index": index} | _safe_explain(result.queries[index].sql, labels, dialect)
        for index in evidence_indices(result.answer, result.queries)
    ]


def _safe_explain(sql: str, labels: Labels, dialect: str) -> dict[str, Any]:
    """A bug in this reader must not break showing the run: it marks the query unexplained, and
    the SQL tab still shows the query."""
    try:
        return explain_sql(sql, labels, dialect).to_json()
    except Exception:
        logger.exception("could not explain a query")
        return Explanation(unexplained=["error"]).to_json()
