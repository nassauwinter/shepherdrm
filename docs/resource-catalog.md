# Resource catalog

The resource catalog stores the allocatable accounts, devices, environments,
and other items managed by Shepherd RM. Every resource explicitly uses either
`Exclusive` or `Shared` sharing mode. The catalog reports availability, but
lease acquisition will be introduced by the core-leasing milestone.

Every resource also has a visibility mode. New resources default to
`Restricted`; `Public` resources are visible to every authenticated principal.
A restricted resource is visible only through a direct principal grant or
membership in a granted, non-archived group. Administrators can always see and
manage every resource.

All catalog requests require a bearer token. Administrators can create, update,
archive, and change the operational state of resources. Regular users can read
and list only visible, non-archived resources. A direct request for an
inaccessible resource returns `404`, so the API does not disclose that it
exists. Only administrators may retrieve an archived resource or use
`include_archived=true` when listing.

## Access grants

Administrators inspect grants with `GET /v1/resources/{resource_id}/access`.
They add or remove direct access idempotently through these endpoints:

```text
PUT    /v1/resources/{resource_id}/access/principals/{target_id}
DELETE /v1/resources/{resource_id}/access/principals/{target_id}
PUT    /v1/resources/{resource_id}/access/groups/{target_id}
DELETE /v1/resources/{resource_id}/access/groups/{target_id}
```

Direct grants support human users and service identities. Group grants apply to
active members only; archiving a group immediately stops it from providing
access. Grants do not make a resource belong to one user—they allow several
principals or teams to share access without changing ownership.

## Discovery

`GET /v1/resources` returns an offset-based page with `items`, `offset`,
`limit`, and `total`. Results have a stable name-and-ID ordering. The following
filters may be combined:

- `type` for an exact resource type;
- `sharing_mode` with `Exclusive` or `Shared`;
- `operational_status` with `Active`, `Cleaning`, `Quarantined`, or `Disabled`;
- `available` for derived allocatability;
- repeated `label=key=value` parameters for exact label pairs;
- `include_archived=true` for administrators.

For example:

```text
GET /v1/resources?type=account&sharing_mode=Shared&label=region=eu&available=true
```

`available` and `active_lease_count` are read-only values. An active shared
resource remains available with active leases. An exclusive resource is
available only while it has none. Archived or non-active resources are never
available.

## Updates and operational state

Metadata updates use `PATCH /v1/resources/{resource_id}` and must include the
current `version` from the resource representation. A successful change
increments the version; a stale version receives `409 Conflict` instead of
overwriting a concurrent update.

Operational changes use explicit action endpoints:

- `disable`: `Active` or `Quarantined` to `Disabled`;
- `enable`: `Disabled` to `Active`;
- `quarantine`: `Active` to `Quarantined`;
- `recover`: `Quarantined` to `Active`.

Archival is a `DELETE` operation that preserves history. It is idempotent and
is rejected while a resource has an active lease. Normal discovery excludes
archived records.
