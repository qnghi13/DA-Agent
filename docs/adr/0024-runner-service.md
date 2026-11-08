# 0024: The Runner runs as its own service when deployed

**Status:** Accepted, 2026-11-08

**Context:** ADR 0005 says only the Runner holds the Docker socket. Until now the worker built a
`Runner` in its own process and called `docker run`, so in practice the worker held the socket.
The worker also holds provider keys and handles model output and uploaded files, which is what
ADR 0005 keeps away from the socket. Compose packaging (Day 20) makes this concrete: whatever
container mounts `/var/run/docker.sock` is root on the host. A second problem appears in
containers: the Runner passes bind-mount paths to the Docker daemon, which reads them as host
paths, not paths inside the Runner's container.

**Decision:**
- `python -m da_agent.runner` serves the Runner over HTTP. It accepts three typed requests:
  `POST /v1/query` (`SqlRun`), `/v1/ingest` (`IngestRun`), and `/v1/build` (`BuildRun`). Python
  runs are not exposed, because nothing in the worker uses them. Every request needs a bearer
  token shared with the worker (`DA_AGENT_RUNNER_TOKEN`, at least 32 characters), compared in
  constant time.
- The worker uses `RunnerClient` when `DA_AGENT_RUNNER_URL` is set. It has the same `query`,
  `ingest`, and `build` methods as `Runner`, so the pipeline and the graph do not change. A
  Runner that cannot be reached becomes an `error` result, which the model or the job retry
  logic already handles. An output directory outside the worker's staging root is rejected.
- Without `DA_AGENT_RUNNER_URL`, the worker keeps the in-process Runner. That mode is for local
  development and tests only.
- With `DA_AGENT_DATA_VOLUME` set, the Runner mounts data into sandboxes as
  `type=volume,volume-subpath=...` from that named volume instead of bind mounts. The API, worker,
  and Runner mount the same volume at the same path, so paths in results mean the same thing in
  each container. This needs Docker Engine 26 or later.
- In Compose, only the Runner mounts the socket, and the Runner has no provider keys and no
  database URL. Its port is reachable only on the internal network.

**Consequences:**
- A compromised worker can still ask for queries and builds, but cannot choose an image, a mount,
  or a flag, and cannot reach the socket.
- A compromised Runner is still a host compromise. The Runner is small, takes only typed input,
  and does not parse uploads itself: parsing happens in the sandbox.
- Each query adds one local HTTP round trip, small next to a container start (about 0.7 s).
- Sandbox, worker, and API write to the same volume, so they run with the same user id
  (`DA_AGENT_SANDBOX_USER`), which lets the worker move and delete what a sandbox wrote.

**Rejected:**
- Keep the socket in the worker and accept the risk: the simplest, but it gives up the boundary
  ADR 0005 and the threat model rely on.
- A Docker socket proxy in front of the worker: the proxy can block API paths, but
  `containers/create` still accepts any mount, so the worker could still mount the host's root.
- A message queue between worker and Runner: the calls are synchronous and short, and Postgres
  already queues jobs. HTTP keeps it to one request per sandbox.
- Mounting the host data directory at the same absolute path in every container: it works, but it
  ties the deployment to one host path and fails on Docker Desktop, where the daemon runs in a VM.
