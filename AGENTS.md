# Shepherd RM contributor rules

These rules apply to the entire repository.

## Source-file documentation

- Every Python file must begin with a module docstring that briefly explains
  the file's purpose and responsibility.
- The module docstring must be the first statement, except that a shebang or an
  encoding declaration may precede it.
- This rule applies to application modules, scripts, tests, migration files,
  and Python documentation configuration.
- Use a concise description of why the module exists. Do not merely repeat the
  filename or list implementation details.
- Every new or renamed Python file must include its module docstring in the same
  change. When materially changing a module's responsibility, update its
  docstring as well.
- Non-Python files do not use Python docstrings. Add a leading purpose comment
  only when the file format supports comments and the purpose is not already
  clear from nearby documentation. Do not add comments that would invalidate
  formats such as JSON.

## Architecture and API contract

- Keep the server a modular monolith. Domain boundaries are internal modules,
  not separate services.
- PostgreSQL is authoritative. Concurrency correctness must use transactions,
  row locks, and database constraints rather than process-local locks.
- `openapi/openapi.yaml` is the authoritative public HTTP contract. Update it
  before or together with endpoint changes.
- Keep registered paths, methods, and operation IDs synchronized with the
  OpenAPI contract and preserve the contract tests.
- Product endpoints are versioned under `/v1`; operational health endpoints
  remain unversioned.
- The Python SDK and CLI must consume the public API rather than importing
  server internals or accessing PostgreSQL directly.

## Database changes

- Represent every schema change with a reviewed Alembic migration.
- Keep SQLAlchemy models and migration heads consistent; run the migration
  drift check after model changes.
- State-changing domain writes and their audit events must be committed in the
  same transaction.
- Never put passwords, bearer tokens, secret values, or credentials in logs,
  audit metadata, exceptions, or ordinary API responses.
- Coordinate every operation that can change resource allocatability by locking
  the affected resource row.

## Testing

- Follow the additional test conventions in `tests/README.md`.
- Every `test_` function must have a behavioral docstring.
- Add contract tests for API changes and PostgreSQL integration tests for
  migrations, constraints, transactions, and concurrency behavior.
- Tests that create database records must clean up only their own uniquely
  identified data.
- Run `make check` before handing off a change. When PostgreSQL integration is
  relevant, set `SHEPHERD_TEST_DATABASE_URL` so integration tests execute rather
  than skip.

## Python tooling and style

- Use `uv` and the checked-in `uv.lock` for dependency management.
- Support the Python versions configured in CI and keep strict mypy checking
  clean.
- Format and lint with Ruff. Do not weaken checks merely to make a change pass.
- Prefer small domain-focused modules and explicit names over generic utility
  modules.
- Load deployment configuration through `Settings`; do not embed production
  credentials or environment-specific paths in application logic.

## Documentation and local files

- Update user and development documentation when commands, configuration,
  deployment behavior, or supported features change.
- Keep local planning and design notes under `.local-docs/`; that directory is
  intentionally excluded from Git.
- Do not commit generated documentation, caches, virtual environments, IDE
  state, local environment files, or credentials.
