"""Fixed-window rate limits in Postgres, so every API process shares one count per bucket.

A bucket names who is limited, such as `key:<id>` or `authfail:<address>`. A window counts hits
from its start; the upsert is atomic, so concurrent requests are all counted. See docs/adr/0022.
"""

import psycopg


class RateLimiter:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def hit(self, bucket: str, *, limit: int, window_seconds: int) -> float | None:
        """Count one hit. Returns None if it is within the limit, or seconds until the window
        ends."""
        row = self._conn.execute(
            """
            INSERT INTO rate_counters (bucket, window_start, hits)
            VALUES (%s, to_timestamp(floor(extract(epoch FROM now()) / %s) * %s), 1)
            ON CONFLICT (bucket, window_start) DO UPDATE SET hits = rate_counters.hits + 1
            RETURNING hits,
                extract(epoch FROM window_start + make_interval(secs => %s) - now())::float8
            """,
            (bucket, window_seconds, window_seconds, window_seconds),
        ).fetchone()
        if row is not None and row[0] == 1:
            # A new window: drop this bucket's old ones, which are never read again.
            self._conn.execute(
                "DELETE FROM rate_counters WHERE bucket = %s "
                "AND window_start < now() - interval '1 day'",
                (bucket,),
            )
        return None if row is None or row[0] <= limit else max(row[1], 1.0)

    def exceeded(self, bucket: str, *, limit: int, window_seconds: int) -> float | None:
        """Without counting a hit: seconds until the window ends if the limit is already reached."""
        row = self._conn.execute(
            "SELECT hits, extract(epoch FROM window_start + make_interval(secs => %s) - now())"
            "::float8 FROM rate_counters WHERE bucket = %s "
            "AND window_start = to_timestamp(floor(extract(epoch FROM now()) / %s) * %s)",
            (window_seconds, bucket, window_seconds, window_seconds),
        ).fetchone()
        return None if row is None or row[0] < limit else max(row[1], 1.0)
