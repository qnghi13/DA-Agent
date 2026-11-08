# 0025: Browsers sign in with one-time links and keep a cookie session; keys stay for scripts

**Status:** Accepted, 2026-11-09

**Context:** The plan reached a workspace with an API key and ruled out sign-up and login. In the
browser that means pasting a long key into every new tab: the UI keeps it in `sessionStorage`, so
closing the tab loses it, and any script injected into the page can read it. The operator
dashboard (ADR 0026) also needs a role that is not tied to one workspace, which keys do not have.
The deployment must still come up with `docker compose up`, without an OAuth app or a mail server.

**Decision:**
- **Keys for machines, sessions for people.** API keys keep working unchanged for curl, scripts,
  and CI. Browsers use a session cookie instead.
- **Login links.** A link carries a one-time token: 256 random bits, stored only as a SHA-256
  hash, valid for 15 minutes, used once (`UPDATE ... SET used_at = now() WHERE used_at IS NULL
  AND expires_at > now() RETURNING`). The token is in the URL fragment (`/login#token=...`), so it
  never reaches nginx or API access logs or a `Referer` header; the UI posts it to
  `POST /v1/auth/redeem`. A link is for one workspace (`member`) or for the operator (`operator`).
- **Where links come from.**
  - `python -m da_agent.admin login-link --workspace ID` or `--operator`: the first way in.
  - `POST /v1/auth/links` in the UI ("Invite or sign in another device"): a member makes a member
    link for its own workspace; an operator can make operator links. Shown once, audited.
  - `POST /v1/auth/session` with a valid API key as bearer: exchanges the key for a member
    session, so a key can still be pasted once, but the browser never stores it.
- **Sessions.** A `sessions` row holds the hash of a 256-bit token, the role, the workspace (null
  for the operator), creation, last use, and revocation. Cookie `da_session`: `HttpOnly`,
  `SameSite=Strict`, `Path=/`, and `Secure` with the `__Host-` prefix when `DA_AGENT_PUBLIC_URL`
  is https (the prefix requires `Path=/`). Sessions expire after 30 days without use and 90 days
  after creation. The expiry slides at most once per hour, so a busy session does not write on
  every request.
- **CSRF.** Besides `SameSite=Strict`, a request authenticated by cookie with an unsafe method
  must have an `Origin` equal to `DA_AGENT_PUBLIC_URL`'s origin. Bearer requests are not ambient
  and skip this check.
- **One caller type.** The `identity` dependency accepts a bearer key or a member session and
  returns the same caller (workspace, key id or session id). Rate limits are per key or per
  session, and failed tokens count towards the existing per-address limit. An operator session
  gets 404 on workspace endpoints, and a member gets 404 on `/v1/ops`, as for any unknown row.
- **Leaving.** `POST /v1/auth/logout` revokes the session and clears the cookie.
  `admin revoke-sessions --workspace ID` or `--operator` revokes all of them.

**Consequences:**
- The browser holds no secret that page scripts can read. A stolen cookie still works until it
  is revoked or expires, so revocation and expiry exist from the start.
- The fetch-based SSE client stays, since cookies go with `fetch` too; reports can now open as
  normal links.
- New tables `login_links` and `sessions`, and an `audit_log.session_id` column. Tests on Postgres
  cover replayed and expired links, cross-workspace access with a session, the operator and member
  split, the `Origin` check, logout, and revocation.
- The plan's "no login" line and non-goals change: still no passwords, sign-up, or SSO.

**Rejected:**
- Email and password: needs sign-up, password reset, and therefore a mail server, for a
  self-hosted tool with few users.
- SSO through oauth2-proxy: the easiest sign-in for users, but every deployment needs an OAuth
  app, a domain, and one more service. It can sit in front later; sessions stay underneath.
- Login links sent by email: the same as above without the CLI step, but needs SMTP.
- Keeping the key in `localStorage`: survives the tab, but any XSS reads the key, and the key is
  long-lived and also works outside the browser.
- JWTs: cannot be revoked without a server-side list, which is what the `sessions` table already
  is.
