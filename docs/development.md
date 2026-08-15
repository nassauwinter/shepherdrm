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

## Run PostgreSQL

Start the development database:

```bash
python3 scripts/dev.py db-up
```

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
