# Database migrations

Alembic manages the versioned PostgreSQL schema. Apply all migrations with
`python3 scripts/dev.py migrate` and verify that SQLAlchemy models match the
latest revision with `python3 scripts/dev.py migration-check`.

Every generated migration must be reviewed before it is committed. The Compose
stack applies pending migrations through its one-shot `migrate` service before
starting the API.

Before the first release, development databases are disposable and migration
revisions may be consolidated into the baseline. Published releases establish
the compatibility boundary: migrations required to upgrade between supported
release versions are retained and released migration history is never
rewritten. The schema published with `0.1.0` is the first immutable migration
baseline. Consolidating pre-release revisions does not remove schema tests;
the baseline must still be tested for creation, downgrade, model drift,
constraints, indexes, defaults, and database-managed behavior.
