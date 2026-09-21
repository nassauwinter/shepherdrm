# Deployment security boundary

Shepherd RM `0.1.0` is deployed as the published container image with PostgreSQL
17. The repository's `compose.yaml` exposes PostgreSQL and uses development
credentials for local testing; it is not a production deployment definition.

## Supported single-host deployment

Use `compose.production.yaml` with a published `linux/amd64` Shepherd RM image.
This versioned definition runs PostgreSQL 17, a one-shot migration container, the
API, and the expiration worker. It pulls the server image and never builds source.
PostgreSQL has persistent storage on an internal network and publishes no host
port. The API binds to `127.0.0.1:8000` by default for an operator-managed TLS
proxy.

Copy `production.env.example` to a protected deployment directory as
`production.env`. Set `SHEPHERD_IMAGE` to the published digest, not `latest`:

```text
SHEPHERD_IMAGE=docker.io/nassauw/shepherdrm@sha256:<published-digest>
SHEPHERD_SECRETS_DIR=/etc/shepherdrm
```

Create the secret directory with permissions limited to the deployment operator.
The published image runs Shepherd RM as UID/GID `10001`, so the mounted files
must be owned by that identity; Compose file-backed secrets retain their host
ownership and mode:

```bash
sudo install -d -m 0700 /etc/shepherdrm
openssl rand -base64 32 | sudo tee /etc/shepherdrm/postgres_password >/dev/null
sudo chown 10001:10001 /etc/shepherdrm/postgres_password
sudo chmod 0400 /etc/shepherdrm/postgres_password
```

For managed resource secrets, create a separate 32-byte key and store a JSON key
ring in `/etc/shepherdrm/secret_encryption_keys.json` as documented in
{doc}`resource-secrets`. Set `SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID` in
`production.env`. External-only deployments must still provide a protected file
containing `{}` and leave the active key ID empty. Give the key-ring file the
same `10001:10001` ownership and `0400` mode. Rootless or user-namespace-remapped
Docker installations must use the host UID/GID mapped to container UID/GID
`10001` instead.

Validate and start the deployment:

```bash
docker compose --env-file production.env -f compose.production.yaml config --quiet
docker compose --env-file production.env -f compose.production.yaml pull
docker compose --env-file production.env -f compose.production.yaml up -d
docker compose --env-file production.env -f compose.production.yaml ps
```

The migration container must complete successfully. The API health check uses
`/ready`, so healthy means the process can reach the expected PostgreSQL schema.
Create the first administrator through the same image and configuration:

```bash
docker compose --env-file production.env -f compose.production.yaml \
  run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
```

Inspect routine state with `docker compose ps` and bounded service logs:

```bash
docker compose --env-file production.env -f compose.production.yaml \
  logs --tail 200 shepherd-rm worker migrate postgres
```

`docker compose ... down` stops containers but preserves the database volume.
Never add `--volumes` during routine shutdown. Use an external PostgreSQL 17
deployment by supplying the same application settings through an equivalent
orchestrator; PostgreSQL remains a separate service and its lifecycle stays under
the operator's control.

## Supported configuration

| Setting | Default | Production guidance |
| --- | --- | --- |
| `SHEPHERD_DATABASE_URL` | Local development URL | Production Compose supplies a passwordless internal URL. External deployments may supply a PostgreSQL 17 URL. |
| `SHEPHERD_DATABASE_PASSWORD_FILE` | Unset | Preferred password source for a passwordless URL. A password already present in the URL takes precedence. |
| `SHEPHERD_DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | Keep finite and positive. |
| `SHEPHERD_HOST` | `127.0.0.1` | Production container uses `0.0.0.0`; restrict exposure at the host or proxy. |
| `SHEPHERD_PORT` | `8000` | Container port remains `8000`; change only the host mapping. |
| `SHEPHERD_LOG_LEVEL` | `INFO` | Use `INFO` unless diagnosing a bounded incident. |
| `SHEPHERD_MAX_REQUEST_BODY_BYTES` | `1048576` | Positive; increase only for a documented workload. |
| `SHEPHERD_LOGIN_TOKEN_TTL_SECONDS` | `43200` | Login token lifetime in seconds. |
| `SHEPHERD_LOGIN_RATE_LIMIT_ATTEMPTS` | `10` | Shared login attempts per window. |
| `SHEPHERD_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `300` | Login window in seconds. |
| `SHEPHERD_SECRET_ACCESS_RATE_LIMIT_ATTEMPTS` | `60` | Shared explicit secret accesses per window. |
| `SHEPHERD_SECRET_ACCESS_RATE_LIMIT_WINDOW_SECONDS` | `60` | Secret-access window in seconds. |
| `SHEPHERD_LEASE_EXPIRATION_POLL_SECONDS` | `1` | Worker polling interval; must be positive. |
| `SHEPHERD_LEASE_EXPIRATION_BATCH_SIZE` | `100` | Worker batch size from `1` through `1000`. |
| `SHEPHERD_WORKER_METRICS_PATH` | Unset | API and worker must share this atomic state file. Production Compose configures a private runtime volume. |
| `SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID` | Unset | Required for managed-secret writes. This identifier is not secret. |
| `SHEPHERD_SECRET_ENCRYPTION_KEYS_FILE` | Unset | Preferred JSON key-ring source. The direct setting takes precedence when both forms are configured. |
| `SHEPHERD_SECRET_ENCRYPTION_KEYS` | `{}` | Direct JSON fallback for other orchestrators; avoid environment storage when file mounts are available. |
| `SHEPHERD_OPENAPI_PATH` | Packaged source path | Production image uses `/app/openapi/openapi.yaml`; do not override. |
| `SHEPHERD_MIGRATIONS_PATH` | Packaged source path | Production image uses `/app/migrations`; do not override. |

`POSTGRES_DB`, `POSTGRES_USER`, `SHEPHERD_BIND_ADDRESS`,
`SHEPHERD_SECRETS_DIR`, and `SHEPHERD_IMAGE` configure the Compose deployment,
not the Shepherd RM process. The example defaults are safe only when the API
remains behind the documented network boundary. The development database URL and
development Compose password are unsafe for production.

The `shepherd_runtime_state` volume contains only bounded worker counters,
timestamps, and the image revision. It contains no resource, principal, or secret
data and is not part of backup or restore. Removing it resets worker counters;
Prometheus must tolerate counter resets.

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

The unversioned `/health`, `/ready`, and `/metrics` routes share the API listener.
Keep the listener on an operator-controlled network. Liveness may be used by the
local orchestrator; readiness must not be exposed publicly. Metrics also require
an authenticated administrator. See the [observability guide](observability.md)
for scrape credentials, metric definitions, and alert guidance.

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

## Manual resource care

The initial release does not clean, reset, or health-check a resource after a
lease. Operators must inspect and prepare resources between leases. Disable a
resource before planned maintenance, quarantine it when suitability is uncertain,
then use the explicit recover and enable operations after inspection. Automating
these actions remains outside the initial release.
