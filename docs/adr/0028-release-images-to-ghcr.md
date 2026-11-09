# 0028: Release images to GHCR from version tags

**Status:** Accepted, 2026-11-10

**Context:** The plan asks for CI/CD, and hosting on a VPS is postponed, so "deploy" means letting
someone run a released version without building it. Until now `docker compose up --build` built
four images locally (app, runner, sandbox, web), which takes several minutes and needs the source.

**Decision:**
- **Trigger:** pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`. Nothing is published
  from branches or pull requests.
- **Order:** the CI workflow runs first (it is callable with `workflow_call`). Then each image is
  built and pushed to `ghcr.io/<owner>/da-agent{,-runner,-sandbox,-web}`, tagged with the version
  and, unless it is a pre-release such as `0.2.0-rc.1`, `latest`. A last job pulls those images
  and runs `deploy/smoke.sh` on them, so a release is checked as published, not as built.
- **Tooling:** only the Docker CLI on the runner (`docker login`, `docker buildx build --push`), so
  no third-party action receives the registry token. The token is the job's `GITHUB_TOKEN` with
  `packages: write` in the publish job only.
- **Supply chain:** each image carries SLSA provenance (`mode=max`), an SBOM, and OCI labels for
  its source repository, version, and commit.
- **Running a release:** `compose.yml` reads `DA_AGENT_IMAGE` (default `da-agent`) and
  `DA_AGENT_TAG` (default `compose`). Both unset, nothing changes; set, `docker compose pull` and
  `up --no-build` run the published images, including the sandbox image the runner starts.
- **Platform:** `linux/amd64` only.

**Consequences:** A release takes about the time of CI plus four parallel builds. New GHCR
packages are private: the owner makes them public once, in the package settings. The version
lives in the tag; `pyproject.toml` and `web/package.json` are not checked against it. Images are
not signed with cosign; provenance is attached but not verified on pull.

**Rejected:** `docker/build-push-action` and friends (convenient, but four more pinned actions
that see the token for no capability we need); publishing on every push to `main` (images nobody
asked for, and `latest` would move with each merge); Docker Hub (another account and secret,
where GHCR uses the repository's own token); multi-arch builds (QEMU makes the arm64 build slow,
and no target needs it yet).
