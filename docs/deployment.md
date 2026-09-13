# Deployment security boundary

Shepherd RM `0.1.0` is deployed as the published container image with PostgreSQL
17. The repository's `compose.yaml` exposes PostgreSQL and uses development
credentials for local testing; it is not a production deployment definition.

## Network and TLS

Terminate TLS at an operator-managed reverse proxy or ingress. Expose only the
proxy to untrusted networks. Bind Shepherd RM's port 8000 and PostgreSQL's port
5432 to private container or operator-controlled networks; never publish
PostgreSQL directly to the internet.

The application does not use client IP addresses for authorization or rate-limit
identity. Login limits use a normalized username digest, and secret-access
limits use the authenticated principal ID, so forwarded client-address headers do
not affect these security controls. Do not trust or forward arbitrary client
headers beyond what the selected proxy requires. Replace inbound
`X-Correlation-ID` values that do not meet Shepherd RM's documented safe format;
the application also validates them before logging.

The unversioned `/health` and `/ready` routes share the API listener, and the
planned `/metrics` route will use that listener as well. Keep the listener on an
operator-controlled network. Liveness may be used by the local orchestrator;
readiness must not be exposed publicly. `/metrics` will also require an
authenticated administrator when the initial metrics slice is implemented.

## Application security limits

Request bodies default to 1 MiB. Login allows 10 attempts per normalized username
in five minutes. Explicit secret access allows 60 attempts per authenticated
principal per minute, shared between administrator and lease access paths. The
limits use atomic PostgreSQL counters and therefore apply consistently across API
processes. A blocked response is RFC 9457 problem JSON with status `429`, a safe
correlation ID, and `Retry-After` in seconds.

Configure the limits with:

- `SHEPHERD_MAX_REQUEST_BODY_BYTES`;
- `SHEPHERD_LOGIN_RATE_LIMIT_ATTEMPTS` and
  `SHEPHERD_LOGIN_RATE_LIMIT_WINDOW_SECONDS`;
- `SHEPHERD_SECRET_ACCESS_RATE_LIMIT_ATTEMPTS` and
  `SHEPHERD_SECRET_ACCESS_RATE_LIMIT_WINDOW_SECONDS`.

All limits must remain positive. A reverse proxy may add broader IP or network
controls, but those controls are deployment-specific and do not replace the
application limits above.
