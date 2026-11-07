# ruff: noqa: S608 - this module builds SQL text on purpose. Identifiers go through q() and
# literals through lit(); tests cover hostile column names and values.
"""SQL helpers shared by raw profiling and the clean build: quoting and local number parsing."""


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


SHORTHAND = r"^(-?)(\d+)(tr|triệu|k)(\d{1,3})$"


def number_sql(text: str) -> str:
    """SQL for a DOUBLE from local number text: 22,000 / 1.350.000 / 25000k / 578.000đ / 1.5tr.

    Without a suffix, groups of three digits mean thousands whatever the separator, so 22,000 can
    never become 22, and one separator without three digits after it is a decimal point: 2,5.
    Before a suffix, one separator is always a decimal point: 151,036tr is 151.036 million.
    Shorthand such as 1tr270 (1,270,000), 1k5 (1,500), and -2tr250 (a refund) is read too.
    """
    compact = f"regexp_replace(lower(trim({text})), '\\s+', '', 'g')"
    bare = f"regexp_replace({compact}, '(vnđ|vnd|đồng|đ)$', '')"
    multiplier = (
        f"CASE WHEN regexp_matches({bare}, '(k|nghìn|ngàn)$') THEN 1000 "
        f"WHEN regexp_matches({bare}, '(tr|triệu)$') THEN 1000000 ELSE 1 END"
    )
    digits = f"regexp_replace({bare}, '(k|nghìn|ngàn|tr|triệu)$', '')"
    # With a suffix, one separator is a decimal point: 151,036tr is 151.036 million.
    suffixed = f"({multiplier}) > 1 AND regexp_matches({digits}, '^-?\\d+[.,]\\d+$')"
    normal = (
        f"CASE WHEN {suffixed} THEN replace({digits}, ',', '.') "
        f"WHEN regexp_matches({digits}, '^-?\\d{{1,3}}(,\\d{{3}})+(\\.\\d+)?$') "
        f"THEN replace({digits}, ',', '') "
        f"WHEN regexp_matches({digits}, '^-?\\d{{1,3}}(\\.\\d{{3}})+(,\\d+)?$') "
        f"THEN replace(replace({digits}, '.', ''), ',', '.') "
        f"WHEN regexp_matches({digits}, '^-?\\d+[.,]\\d+$') THEN replace({digits}, ',', '.') "
        f"ELSE {digits} END"
    )
    sign = f"CASE WHEN regexp_extract({bare}, {lit(SHORTHAND)}, 1) = '-' THEN -1 ELSE 1 END"
    major = f"TRY_CAST(regexp_extract({bare}, {lit(SHORTHAND)}, 2) AS DOUBLE)"
    unit = f"regexp_extract({bare}, {lit(SHORTHAND)}, 3)"
    minor = f"TRY_CAST(rpad(regexp_extract({bare}, {lit(SHORTHAND)}, 4), 3, '0') AS DOUBLE)"
    shorthand = (
        f"({sign}) * CASE WHEN {unit} = 'k' THEN {major} * 1000 + {minor} "
        f"ELSE {major} * 1000000 + {minor} * 1000 END"
    )
    return (
        f"(CASE WHEN regexp_matches({bare}, {lit(SHORTHAND)}) THEN {shorthand} "
        f"ELSE TRY_CAST({normal} AS DOUBLE) * {multiplier} END)"
    )


def filled_sql(column: str) -> str:
    """SQL that is true when a raw text cell holds something other than blanks."""
    return f"({q(column)} IS NOT NULL AND trim({q(column)}) <> '')"


def shape_sql(columns: list[str], filled: list[str]) -> str:
    """SQL that is true for rows whose filled cells are exactly `filled`, out of `columns`."""
    chosen = set(filled)
    return " AND ".join(filled_sql(c) if c in chosen else f"NOT {filled_sql(c)}" for c in columns)


def filled_count_sql(columns: list[str]) -> str:
    return "(" + " + ".join(f"CAST({filled_sql(c)} AS INTEGER)" for c in columns) + ")"


MAX_SUM_COLUMNS = 20


def sums_above_sql(table: str, columns: list[str], shape: list[str], rare: list[list[str]]) -> str:
    """SQL counting rows of `shape` whose value in one of its number columns is non-zero and
    equals the sum of that column over the record rows above it, back to the previous row of the
    same shape in the same source. Rows of any rare shape do not count as records."""
    is_shape = shape_sql(columns, shape)
    is_rare = " OR ".join(f"({shape_sql(columns, other)})" for other in rare) or "false"
    numbers = shape[:MAX_SUM_COLUMNS]
    values = ", ".join(f"{number_sql(q(c))} AS v{i}" for i, c in enumerate(numbers))
    sums = ", ".join(f"sum(v{i}) AS s{i}" for i in range(len(numbers)))
    match = " OR ".join(
        f"(g.v{i} IS NOT NULL AND g.v{i} <> 0 AND abs(g.v{i} - s.s{i}) < 0.5)"
        for i in range(len(numbers))
    )
    return (
        f"WITH t AS (SELECT _source, CAST(_row AS BIGINT) AS r, ({is_shape}) AS is_shape, "
        f"({is_rare}) AS is_rare, {values} FROM {table}), "
        "g AS (SELECT *, coalesce(sum(CAST(is_shape AS INTEGER)) OVER (PARTITION BY _source "
        "ORDER BY r ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS grp FROM t), "
        f"s AS (SELECT _source, grp, {sums} FROM g WHERE NOT is_rare GROUP BY _source, grp) "
        f"SELECT count(*) FROM g JOIN s ON g._source = s._source AND g.grp = s.grp "
        f"WHERE g.is_shape AND ({match})"
    )


def signature_sql(value: str) -> str:
    """SQL for the coarse form of a value: digit runs become 9 and other runs become A, so
    "HD00021" is "A9" and a sentence is "A A A". Identifiers share one form."""
    digits = f"regexp_replace(trim({value}), '[0-9]+', '9', 'g')"
    return f"regexp_replace({digits}, '[^0-9\\s]+', 'A', 'g')"
