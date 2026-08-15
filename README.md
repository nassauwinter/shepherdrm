# Shepherd RM

Shepherd RM is an open-source resource manager primarily focused on quality assurance. It lets human users and automated pipelines register, find, lease, renew, and release resources through a common API.

The initial implementation is a Python modular monolith backed by PostgreSQL. A Python SDK and thin CLI will consume the same versioned REST/OpenAPI contract.

## Repository layout

```text
src/shepherd_rm/  Server package
tests/            Automated tests
migrations/       PostgreSQL schema migrations
openapi/          Published API contract
sdk/              Python SDK package
cli/              Command-line client package
```

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

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Run the initial checks:

```bash
python3 scripts/dev.py check
```

Useful individual tasks include `test`, `lint`, `format`, `typecheck`, and `docs`. Run `python3 scripts/dev.py --help` to list all tasks. Equivalent Make targets are provided as an optional convenience on systems with Make installed.

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

The project is at the initial scaffolding stage. The next implementation slice will add service configuration, database migrations, health endpoints, and authentication.
