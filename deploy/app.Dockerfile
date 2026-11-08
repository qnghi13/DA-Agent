# API, worker, and Runner images. Build from the repository root:
#   docker build -f deploy/app.Dockerfile --target app -t da-agent:local .
#   docker build -f deploy/app.Dockerfile --target runner -t da-agent-runner:local .
# Works with the legacy builder too, so no RUN --mount or heredocs.
FROM ghcr.io/astral-sh/uv@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
# docker:29.1.3-cli, for the Docker client binary only.
FROM docker@sha256:4fa0ee1f3a7e4354c4ea34558b6d4ee32859baf4973d4c8ccc8e7fe3dd730c04 AS docker-cli

# python:3.12-slim, the same base as the sandbox image.
FROM python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    DA_AGENT_DATA_DIR=/data
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable \
    && rm /usr/local/bin/uv \
    && rm -rf /app/src
# One user for the API, the worker, and sandboxes (DA_AGENT_SANDBOX_USER), so files written on the
# shared data volume can be moved and deleted by the worker. The volume copies this ownership.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads /data/staging /data/datasets \
    && chown -R 10001:10001 /data
USER 10001:10001

# The Runner holds the Docker socket (ADR 0024), which already means root on the host, so it runs
# as root to open the socket whatever its group id is on that host.
FROM app AS runner
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
USER root
