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

Resource management and leasing endpoints are not implemented yet.
