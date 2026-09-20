# Upgrade and rollback

Treat the Shepherd RM image, PostgreSQL state, migration history, and managed
secret key ring as one compatibility boundary.

## Upgrade

1. Read the target release notes and verify its supported PostgreSQL version.
2. Back up PostgreSQL and the full key ring using {doc}`backup-restore`.
3. Record the running image digest and migration revision.
4. Stop the API and worker; leave PostgreSQL running.
5. Change `SHEPHERD_IMAGE` to the target immutable digest.
6. Pull the image and run the one-shot migration service.
7. Start the API and worker only after migration succeeds.
8. Verify `/ready`, login, lease operations, worker logs, and managed-secret access.

```bash
docker compose --env-file production.env -f compose.production.yaml stop shepherd-rm worker
# Update SHEPHERD_IMAGE in production.env to the target immutable digest here.
docker compose --env-file production.env -f compose.production.yaml pull
docker compose --env-file production.env -f compose.production.yaml run --rm migrate
docker compose --env-file production.env -f compose.production.yaml up -d shepherd-rm worker
```

Never run two application versions concurrently unless the target release notes
explicitly declare rolling-upgrade compatibility. A failed migration leaves the
old application stopped until compatibility is understood.

## Rollback

An application-only rollback is safe only when release notes state that the old
image supports the migrated schema. Do not run Alembic downgrade in production
unless a release-specific procedure explicitly requires and verifies it.

For an incompatible or destructive migration, stop the API and worker, restore the
database snapshot and matching key ring, restore the previous image digest, then
start the old version. Database and key backups from different points in time are
not a valid rollback pair.

`0.1.0` establishes the first immutable migration baseline. Later releases add
migrations and retain published migration history.
