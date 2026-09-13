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

The `/v1/resources` API now supports authenticated resource discovery and
administrator-managed creation, updates, lifecycle transitions, and archival.
See {doc}`resource-catalog` for its permissions, filters, and update model.
The `/v1/leases` API supports visibility-aware acquisition, listing, renewal,
release, and administrative revocation. A separate worker expires finite leases;
see {doc}`manual-testing/leasing` for a complete manual flow.

Resources can also carry encrypted managed secrets or external secret references.
Managed storage requires a deployment encryption key configured separately from
PostgreSQL. See {doc}`resource-secrets` for key handling and access behavior.
