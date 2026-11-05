# 0005: Docker sandbox behind a Runner service

**Status:** Accepted, 2026-11-05

**Context:** The agent runs generated Python and SQL. Access to the Docker socket is equivalent to
root on the host, so it must not sit in a service that handles provider keys and untrusted input.

**Decision:** A small Runner service alone holds the socket and accepts typed run requests. Each
sandbox has no network, a read-only filesystem, a non-root user, no capabilities, resource limits, and
one dataset mounted read-only.

**Consequences:**
- A compromised Worker can request runs, but cannot choose mounts or flags.
- Container startup adds latency.
- Runc shares the host kernel, so this is not a hard boundary. gVisor stays optional and the limit is
  documented.

**Rejected:**
- `exec` or subprocess in the Worker: no real isolation.
- Hosted code execution services: adds a paid external dependency and does not work offline.
- Requiring gVisor: stronger, but harder to install for people self-hosting.
