# 0009: Uploaded files are parsed inside the sandbox

**Status:** Accepted, 2026-11-06

**Context:** The first design parsed uploads in the Worker, which also holds provider keys, database
credentials, and network access. Spreadsheet and SQLite parsers have had exploitable bugs (XML
external entities, zip bombs, crafted database files). One bad file could leak keys or crash the
Worker for every user.

**Decision:** Untrusted bytes never touch a process that holds secrets. Reading, table detection,
profiling, and loading run in a sandbox container started by the Runner, with the upload mounted
read-only. The Worker receives only bounded results: profiles, parse reports, and the normalized
dataset file. Parser code stays a plain library, so unit tests call it directly on trusted fixtures.

**Consequences:**
- Generated code, SQL, and file parsing share one boundary and one set of escape tests.
- The Runner and sandbox are built before ingestion, at the start of Days 3–6.
- About a second of container start per job, and debugging needs container logs.
- The Runner reads at most `max_output_bytes` of output and discards the rest, so a flood of output
  cannot exhaust its memory.

**Rejected:**
- Parse in the Worker: simplest, but a parser bug reaches the keys.
- Parse in a subprocess without secrets in its environment: a process of the same user can still
  read its parent's environment through `/proc`, and it keeps network access.
