# Manual API testing

These black-box smoke tests exercise Shepherd RM through its public HTTP API
against a local Docker Compose deployment. They require `curl` and `jq` and do
not inspect PostgreSQL or import server implementation code.

Each walkthrough is self-contained, creates uniquely named test records, and
cleans up only the records it creates. Run any walkthrough independently:

```{toctree}
:maxdepth: 1

identity-management
resource-management
resource-visibility
leasing
resource-secrets
```

Together, the walkthroughs cover the currently implemented public feature
areas:

- local login, current-principal lookup, password management, and personal API
  tokens;
- administrator-managed users, groups, memberships, service identities, and
  service API tokens;
- resource creation, discovery, filtering, optimistic updates, lifecycle
  transitions, visibility grants, and archival;
- exclusive and shared acquisition, TTL policies, idempotency, lease reads and
  filters, renewal, release, administrative revocation, and authorization.
- managed and external resource-secret administration, explicit administrator
  access, lease-authorized discovery and access, and post-release denial.

The walkthroughs preserve the local PostgreSQL volume when they stop the
containers. For a complete reset of a disposable installation, run:

```bash
docker compose down -v
```

This permanently deletes the local Shepherd RM database, including identities,
resources, and audit history. Do not use it for an installation containing data
that must be retained.
