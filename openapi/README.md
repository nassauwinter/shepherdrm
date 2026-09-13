# OpenAPI contract

`openapi.yaml` is the authoritative public contract for the Shepherd RM HTTP API. Server endpoints, the Python SDK, the CLI, documentation, and integrations must agree with it.

The contract is updated in the same change as any API behavior. The server exposes this document at `/openapi.json`. CI validates its OpenAPI structure and verifies that FastAPI's registered paths, methods, and operation IDs match it. Endpoint tests verify runtime response behavior.

Operational endpoints such as `/health` and `/ready` are unversioned. Product API endpoints are versioned under `/v1`.

Validate the contract locally:

```bash
uv run pytest tests/contract
```
