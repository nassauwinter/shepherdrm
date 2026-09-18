# Backup and restore

A recoverable Shepherd RM deployment requires both PostgreSQL and the managed
secret encryption key ring. Store their backups separately. Database-only restore
recovers ordinary data but cannot decrypt managed resource secrets without every
historical key referenced by stored ciphertext.

## Back up

Choose a protected destination outside the Compose volume. Record the Shepherd RM
image digest, PostgreSQL image digest, UTC timestamp, and migration revision with
the backup.

Create a consistent custom-format PostgreSQL backup:

```bash
docker compose --env-file production.env -f compose.production.yaml \
  exec -T postgres sh -c \
  'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --create' \
  > shepherdrm.dump
```

Copy `/etc/shepherdrm/secret_encryption_keys.json` to a different protected backup
location. Preserve all historical keys, not only the active key. Record the active
key ID without copying secret values into logs or release artifacts. Back up the
PostgreSQL password only when the restored deployment must retain that credential;
otherwise generate a new password during recovery.

Test the dump with `pg_restore --list shepherdrm.dump`. This checks structure, not
recoverability. A restore drill remains required.

## Restore into a fresh deployment

Perform the drill under a separate Compose project name or on a disposable host.
Never test by deleting the only production volume.

1. Stop the API and worker.
2. Install the same Compose file and recorded image digest.
3. Restore the PostgreSQL password file with owner `10001:10001` and mode `0400`.
4. Restore the complete key-ring file with owner `10001:10001` and mode `0400`.
5. Start only PostgreSQL and wait for its health check.
6. Restore the custom-format dump.
7. Run the packaged migration job, then start the API and worker.

```bash
docker compose --env-file production.env -f compose.production.yaml up -d postgres
docker compose --env-file production.env -f compose.production.yaml \
  exec -T postgres sh -c \
  'pg_restore --username="$POSTGRES_USER" --clean --if-exists --create --dbname=postgres' \
  < shepherdrm.dump
docker compose --env-file production.env -f compose.production.yaml run --rm migrate
docker compose --env-file production.env -f compose.production.yaml up -d shepherd-rm worker
```

Verify `/ready`, administrator login, resource and lease records, audit history,
and explicit access to one managed secret created before the backup. A temporary
start with an empty key-ring file must leave ordinary data readable while managed
secret access returns a safe service error. Restore the real key ring immediately
and confirm access succeeds. This proves the database backup is intentionally
insufficient by itself.

Record the drill date, source image digest, restored image digest, database and key
backup identifiers, checks performed, and result. Delete disposable copies after
the retention policy permits.
