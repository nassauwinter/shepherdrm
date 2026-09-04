# Resource secrets

Resource secrets associate credentials with a resource without placing them in
ordinary resource or lease responses. Administrators can manage two modes:

- `Managed` values are encrypted by Shepherd RM using a deployment key;
- `External` entries store a provider and opaque reference, leaving resolution
  to the authorized client.

Secret list and detail operations return only an ID, resource ID, name,
description, mode, version, and timestamps. Managed values and external
references are returned only by explicit `/access` operations. Administrators
may use the resource-scoped access operation without acquiring a lease. Regular
principals use the lease-scoped operations, which require an owned lease that
has not ended or expired and continued visibility of the resource. Changing a
public resource to restricted or revoking the owner's direct or active-group
grant immediately removes lease-scoped secret discovery and access.
Administrators continue to bypass resource visibility. Successful access is
audited without recording the material.

## Configure managed-secret encryption

Generate a distinct 32-byte deployment key and keep it outside PostgreSQL:

```bash
openssl rand -base64 32
```

Configure its identifier and a JSON key ring. For example, after placing the
generated value in a protected environment variable:

```bash
export SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID=primary-2026
export SHEPHERD_SECRET_ENCRYPTION_KEYS="$(jq -nc \
  --arg key "$DEPLOYMENT_SECRET_KEY" '{"primary-2026": $key}')"
```

`SHEPHERD_SECRET_ENCRYPTION_KEYS` maps non-secret key identifiers to
base64-encoded 32-byte AES keys. Shepherd RM uses the active key for new writes
and retains every configured key for decryption. This allows a new active key
to be introduced without making existing values unreadable.

Do not commit real keys to `.env`, Compose files, deployment manifests, or this
repository. Production deployments should inject the configuration from a
protected secret store or mounted secret mechanism. Back up the key ring
separately from PostgreSQL: losing an encryption key makes values encrypted by
it unrecoverable, while exposing a key together with the database exposes those
values.

External-only deployments may leave the key ring empty. External entries remain
usable, but managed-secret creation, replacement, and access return `503` until
a valid active key is configured.

## Rotation

To begin using a new key, add it to the key ring and change the active key ID.
Keep the previous key configured while any stored value still uses it. Replacing
a managed secret through `PATCH` encrypts the new value with the current active
key. A future bulk re-encryption operation may automate migration of unchanged
values; until then, remove an old key only after all values encrypted with it
have been replaced or deleted.

All secret-bearing responses include `Cache-Control: no-store` and
`Pragma: no-cache`. Clients remain responsible for avoiding command histories,
terminal capture, artifact storage, and application logs when consuming them.
