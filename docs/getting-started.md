# Getting started

Shepherd RM is in its initial development stage. The current executable foundation can be started with Docker Compose:

```bash
docker compose up --build
```

This starts PostgreSQL, applies the versioned database migrations, and then
starts Shepherd RM. A migration failure prevents the API from starting against
an incompatible schema. The available endpoints are:

- `http://localhost:8000/health` for service liveness;
- `http://localhost:8000/ready` for PostgreSQL readiness;
- `http://localhost:8000/docs` for interactive API documentation;
- `http://localhost:8000/openapi.json` for the authoritative API contract.

Create the first administrator interactively after the stack is healthy:

```bash
docker compose run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
```

The command refuses to run while an active administrator with a password exists.
It can be used as a recovery command after upgrading an older database whose
administrator records have no credentials, or when every administrator has been
archived. Once a usable administrator exists, users, groups, service identities,
and API tokens are managed through the `/v1` API.
API token values are returned only when created; Shepherd RM stores only their
SHA-256 digests.

Resource management and leasing endpoints are not implemented yet.
