# Release acceptance

Shepherd RM validates the deployable image separately from its unit and
PostgreSQL integration tests. The release-acceptance gate uses the production
Compose definition, packaged migrations, and public HTTP API; it does not mount
the source tree into the application containers.

Run the complete deterministic gate locally with Docker and Compose available:

```bash
python3 scripts/dev.py release-check
```

The command builds one `shepherd-rm:acceptance` image, verifies its non-root
runtime contents and embedded OpenAPI and migration sources, then creates two
isolated disposable Compose projects. It checks:

- administrator bootstrap and a resource, lease, and managed-secret workflow;
- malformed authorization, inaccessible-resource probing, request-size limits,
  login throttling, hostile correlation IDs, and response/log redaction;
- bounded parallel exclusive acquisition without double allocation;
- API restart, PostgreSQL interruption and recovery, worker restart, and overdue
  lease expiration catch-up;
- a custom-format PostgreSQL backup restored into a fresh volume together with
  the encryption key ring;
- restored authentication, resources, lease and audit history, and managed-secret
  access; and
- safe managed-secret failure when the database is restored without its key ring.
- safe generic failure, without secret or cryptographic detail exposure, when a
  disposable restored ciphertext envelope is deliberately corrupted.

The runner generates temporary credentials and keys, binds both APIs to random
loopback ports, emits only non-sensitive summary evidence, and removes its named
projects and volumes on completion. A failed run preserves no release claim; use
the Compose logs shown during the run to diagnose it and rerun only after the
underlying problem changes.

## External clean-host record

CI proves deterministic behavior on a GitHub runner, but it does not replace the
release-candidate exercise on an independent host. For `0.1.0-rc.1`, record:

- UTC date, host or VM description, and Docker and Compose versions;
- GitHub release URL, immutable image reference and digest, and Compose-file
  checksum;
- whether installation used only published files and documentation;
- acceptance result plus the administrator, lease, secret, restart,
  database-interruption, backup/restore, and rollback checks performed;
- workload request count, concurrency, maximum and mean latency, and correctness
  result; and
- each finding classified as release-blocking, documented limitation, or
  deferred work.

Do not record passwords, tokens, encryption keys, secret values, raw scanner
output, or database dumps in the acceptance record.
