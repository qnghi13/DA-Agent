# Sandbox image: runs one request and exits. Security comes from the flags the Runner sets
# (no network, read-only root, no capabilities, limits), not from this image alone.
FROM python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY da_sandbox/ /app/da_sandbox/
RUN python -m compileall -q /app && chmod -R a-w /app

USER 65534:65534
ENTRYPOINT ["python", "-I", "/app/da_sandbox/__main__.py"]
