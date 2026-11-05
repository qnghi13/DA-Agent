# DA-Agent

An open-source agent that analyses uploaded CSV, Excel, and SQLite files. You upload data with a
free-form description and ask questions. It answers with results you can trace back to the queries
that produced them.

> **Status: early development. Nothing runs yet.** The design is documented, and application code has
> not been written.

## Planned features

- Handles messy, unknown spreadsheets through a reviewable dataset specification.
- Runs generated SQL and Python in an isolated sandbox.
- Verifies answers with code checks, and abstains when the data is insufficient.
- Resumes runs after a crash, and enforces hard cost limits.
- Evaluation suite, with tracing through OpenTelemetry and Phoenix.
- Self-hosted with Docker Compose.

## Documentation

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
- [Decision records](docs/adr/README.md)
- [Plan and roadmap](docs/plan.md)
- [Current progress](docs/progress.md)
- [Learning notes, in Vietnamese](docs/learning/README.md)

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run pytest
uv run ruff check .
```

## License

[Apache-2.0](LICENSE)
