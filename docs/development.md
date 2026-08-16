# Development

## Set up the environment

Create the local virtual environment and install all development and documentation dependencies:

```bash
uv sync --locked
```

`uv` manages the `.venv` directory automatically. Activating it is optional; project commands should normally use `uv run`.

Run the complete local verification suite:

```bash
python3 scripts/dev.py check
```

Run the API locally with automatic reload:

```bash
python3 scripts/dev.py serve
```

The local API expects PostgreSQL at the URL configured by `SHEPHERD_DATABASE_URL`.

## Run PostgreSQL

Start the development database:

```bash
python3 scripts/dev.py db-up
```

Apply the versioned database schema after PostgreSQL is ready:

```bash
python3 scripts/dev.py migrate
```

When database models change, create a reviewed migration with
`uv run alembic revision --autogenerate -m "describe the change"`. Verify that
the models and latest migration agree with `python3 scripts/dev.py migration-check`.

Stop it when it is no longer needed:

```bash
python3 scripts/dev.py db-down
```

## Build the documentation

```bash
python3 scripts/dev.py docs
```

Open `docs/_build/html/index.html` in a browser to inspect the generated site.

Systems with Make installed may use the equivalent `make` targets as shortcuts.
