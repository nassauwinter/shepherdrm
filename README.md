# Shepherd RM

[![Documentation Status](https://readthedocs.org/projects/shepherdrm/badge/?version=latest)](https://shepherdrm.readthedocs.io/en/latest/?badge=latest)

Shepherd RM is an open-source resource manager primarily focused on quality assurance. It lets human users and automated pipelines register, find, lease, renew, and release resources through a common API.

The initial implementation is a Python modular monolith backed by PostgreSQL. A Python SDK and thin CLI will consume the same versioned REST/OpenAPI contract.

## Documentation

Project documentation is published on Read the Docs:

- https://shepherdrm.readthedocs.io/en/latest/

## Repository layout

```text
src/shepherd_rm/  Server package
tests/            Automated tests
migrations/       PostgreSQL schema migrations
openapi/          Published API contract
sdk/              Python SDK package
cli/              Command-line client package
```

The authoritative HTTP contract is [openapi/openapi.yaml](openapi/openapi.yaml). API changes update the contract, server implementation, and relevant tests together.

## Local development

Requirements:

- [uv](https://docs.astral.sh/uv/)
- PostgreSQL, or Docker Compose for the provided local database

Create the project environment and install all development tools:

```bash
uv sync --locked
```

The project task script provides convenient aliases:

```bash
python3 scripts/dev.py install
```

Start Shepherd RM and PostgreSQL:

```bash
docker compose up --build
```

The API is then available at `http://localhost:8000`, interactive documentation at `http://localhost:8000/docs`, and the authoritative contract at `http://localhost:8000/openapi.json`.

For a faster development loop, start only PostgreSQL and run the API locally with reload enabled:

```bash
docker compose up -d postgres
python3 scripts/dev.py serve
```

Run the initial checks:

```bash
python3 scripts/dev.py check
```

Useful individual tasks include `serve`, `test`, `lint`, `format`, `typecheck`, and `docs`. Run `python3 scripts/dev.py --help` to list all tasks. Equivalent Make targets are provided as an optional convenience on systems with Make installed.

The Compose credentials are intended only for local development. Copy `.env.example` to `.env` to override them locally; `.env` is ignored by Git.

## Documentation

Documentation is built with Sphinx and MyST Markdown:

```bash
python3 scripts/dev.py docs
```

The generated site is written to `docs/_build/html`. Read the Docs uses the checked-in `.readthedocs.yaml` configuration.

## Continuous integration

GitHub Actions uses the checked-in `uv.lock` to run tests on every supported Python version and to run Ruff, mypy, and the documentation build. The same checks are available locally through the task script or Makefile.

## Status

The service provides migration-aware PostgreSQL readiness, local user login,
administrator and regular-user authorization, user/group/service-identity
administration, revocable API tokens, and an authenticated resource catalog.
Administrators can manage exclusive and shared resources while users can
discover public or explicitly granted resources by type, labels, state, sharing
mode, and availability. Restricted resources support direct-principal and group
access grants. Users and pipelines can acquire matching resources with
principal-scoped idempotency, then list, renew, and release their leases;
administrators can inspect and revoke any lease. The Compose worker expires
bounded batches of leases with finite TTLs while indefinite leases remain active.
Administrators can associate encrypted managed values or opaque external references
with resources and explicitly access them; active lease owners can discover and
access their leased resource's secrets without exposing material in ordinary
resource, lease, or secret-metadata responses.
