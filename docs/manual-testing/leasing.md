# Manual lease testing

This walkthrough verifies resource acquisition and the initial lease lifecycle.
It assumes the Compose stack is running and that `ADMIN_TOKEN` and `USER_TOKEN`
contain bearer tokens created through the identity testing flow.

## Create a public exclusive resource

```bash
RESOURCE=$(curl -fsS -X POST http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "manual-exclusive-environment",
    "type": "environment",
    "sharing_mode": "Exclusive",
    "visibility_mode": "Public",
    "expiration_mode": "Optional",
    "default_ttl_seconds": 1800,
    "max_ttl_seconds": 7200,
    "labels": {"purpose": "manual-test"}
  }')
```

## Acquire and inspect it

Omitting `ttl_seconds` uses the resource default. Sending it as `null` requests
an indefinite lease when the resource expiration policy permits that.

```bash
LEASE=$(curl -fsS -X POST http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H 'Idempotency-Key: manual-lease-1' \
  -H 'Content-Type: application/json' \
  -d '{
    "resource_type": "environment",
    "sharing_mode": "Exclusive",
    "labels": {"purpose": "manual-test"},
    "consumer": "manual smoke test"
  }')

LEASE_ID=$(printf '%s' "$LEASE" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -fsS "http://localhost:8000/v1/leases/$LEASE_ID" \
  -H "Authorization: Bearer $USER_TOKEN"
```

Repeating the acquisition with the same user, key, and body returns the same
lease. Reusing that key with a different body returns `409`.

## Renew and release it

```bash
curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/renew" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ttl_seconds": 3600}'

curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/release" \
  -H "Authorization: Bearer $USER_TOKEN"
```

Release is idempotent. Administrators can instead end a lease with
`POST /v1/leases/{lease_id}/revoke`.

## Clean up

The resource can be archived after its lease ends:

```bash
RESOURCE_ID=$(printf '%s' "$RESOURCE" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -fsS -X DELETE "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Lease and audit records intentionally remain as history after archival.
