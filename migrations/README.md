# Database migrations

Alembic manages the versioned PostgreSQL schema. Apply all migrations with
`python3 scripts/dev.py migrate` and verify that SQLAlchemy models match the
latest revision with `python3 scripts/dev.py migration-check`.

Every generated migration must be reviewed before it is committed. The Compose
stack applies pending migrations through its one-shot `migrate` service before
starting the API.
