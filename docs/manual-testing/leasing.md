# Lease management smoke test

This self-contained flow verifies matching, exclusive and shared allocation,
TTL policy, principal-scoped idempotency, reads and filters, renewal, release,
administrative revocation, automatic expiration, and authorization. It requires
`curl` and `jq`.

## Start and prepare identities

```bash
docker compose up --build -d
curl -fsS http://localhost:8000/ready | jq -e '.status == "ready"'
docker compose run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
read -rsp 'Administrator password: ' ADMIN_PASSWORD
echo
ADMIN_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username admin --arg password "$ADMIN_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
unset ADMIN_PASSWORD

RUN_ID="$(date +%s)-$RANDOM"
TEST_USERNAME="lease-user-$RUN_ID"
read -rsp 'Test user password (at least 12 characters): ' TEST_PASSWORD
echo
TEST_USER=$(curl -fsS http://localhost:8000/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{name: $name, display_name: "Manual Lease User", password: $password}')")
TEST_USER_ID=$(jq -er '.id' <<<"$TEST_USER")
USER_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
unset TEST_PASSWORD
```

## Acquire an exclusive lease

Create a public resource whose required expiration policy defaults to 60
seconds and allows at most 300 seconds:

```bash
EXCLUSIVE_RESOURCE=$(curl -fsS http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "lease-exclusive-$RUN_ID" --arg run_id "$RUN_ID" \
    '{name: $name, type: "manual-exclusive", sharing_mode: "Exclusive",
      visibility_mode: "Public", expiration_mode: "Required",
      default_ttl_seconds: 60, max_ttl_seconds: 300,
      labels: {purpose: "lease-smoke", run_id: $run_id}}')")
EXCLUSIVE_RESOURCE_ID=$(jq -er '.id' <<<"$EXCLUSIVE_RESOURCE")
```

Omitting `ttl_seconds` uses the resource default. The response includes the
selected resource, consumer, and arbitrary metadata:

```bash
ACQUIRE_BODY=$(jq -nc --arg run_id "$RUN_ID" \
  '{resource_type: "manual-exclusive", sharing_mode: "Exclusive",
    labels: {run_id: $run_id}, consumer: "manual smoke test",
    metadata: {source: "manual-testing"}}')
LEASE=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Idempotency-Key: exclusive-$RUN_ID" -H 'Content-Type: application/json' \
  --data-binary "$ACQUIRE_BODY")
LEASE_ID=$(jq -er '.id' <<<"$LEASE")
jq -e --arg resource_id "$EXCLUSIVE_RESOURCE_ID" --arg user_id "$TEST_USER_ID" '
  .state == "Active" and .resource.id == $resource_id and
  .resource.available == false and .acquired_by == $user_id and
  .consumer == "manual smoke test" and .metadata.source == "manual-testing" and
  .expires_at != null' <<<"$LEASE"
```

Replay with the same principal, key, and body to get the original lease. Reuse
the key with a changed body to get `409`, and prove the occupied exclusive
resource cannot be acquired by the administrator:

```bash
REPLAY=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Idempotency-Key: exclusive-$RUN_ID" -H 'Content-Type: application/json' \
  --data-binary "$ACQUIRE_BODY")
jq -e --arg id "$LEASE_ID" '.id == $id' <<<"$REPLAY"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Idempotency-Key: exclusive-$RUN_ID" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg run_id "$RUN_ID" \
    '{resource_type: "manual-exclusive", sharing_mode: "Exclusive",
      labels: {run_id: $run_id}, consumer: "changed"}')")
test "$STATUS" = 409
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Idempotency-Key: occupied-$RUN_ID" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg run_id "$RUN_ID" \
    '{resource_type: "manual-exclusive", sharing_mode: "Exclusive", labels: {run_id: $run_id}}')")
test "$STATUS" = 409
```

## Read, list, renew, and revoke

The owner and an administrator can retrieve the lease. User listings contain
only that user's leases; administrators can list all leases and filter by
owner, resource, state, exact consumer, timestamps, offset, and limit.

```bash
curl -fsS "http://localhost:8000/v1/leases/$LEASE_ID" \
  -H "Authorization: Bearer $USER_TOKEN" | jq -e --arg id "$LEASE_ID" '.id == $id'
curl -fsS "http://localhost:8000/v1/leases/$LEASE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e --arg id "$LEASE_ID" '.id == $id'
curl -fsS --get http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  --data-urlencode 'state=Active' --data-urlencode 'consumer=manual smoke test' |
jq -e --arg id "$LEASE_ID" '.total >= 1 and any(.items[]; .id == $id)'
curl -fsS --get http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-urlencode "acquired_by=$TEST_USER_ID" \
  --data-urlencode "resource_id=$EXCLUSIVE_RESOURCE_ID" |
jq -e --arg id "$LEASE_ID" 'any(.items[]; .id == $id)'
```

Renewals are owner-only and must obey the resource maximum. Revocation is
administrator-only and idempotent:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "http://localhost:8000/v1/leases/$LEASE_ID/renew" \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"ttl_seconds":301}')
test "$STATUS" = 409
curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/renew" \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"ttl_seconds":120}' |
jq -e '.state == "Active" and .last_renewed_at != null'
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "http://localhost:8000/v1/leases/$LEASE_ID/revoke" \
  -H "Authorization: Bearer $USER_TOKEN")
test "$STATUS" = 403
curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.state == "Revoked"'
curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.state == "Revoked"'
```

## Verify shared leases and release

```bash
SHARED_RESOURCE=$(curl -fsS http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "lease-shared-$RUN_ID" --arg run_id "$RUN_ID" \
    '{name: $name, type: "manual-shared", sharing_mode: "Shared",
      visibility_mode: "Public", labels: {run_id: $run_id}}')")
SHARED_RESOURCE_ID=$(jq -er '.id' <<<"$SHARED_RESOURCE")
SHARED_BODY=$(jq -nc --arg run_id "$RUN_ID" \
  '{resource_type: "manual-shared", sharing_mode: "Shared", labels: {run_id: $run_id}}')
USER_SHARED=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" -H "Idempotency-Key: shared-user-$RUN_ID" \
  -H 'Content-Type: application/json' --data-binary "$SHARED_BODY")
ADMIN_SHARED=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Idempotency-Key: shared-admin-$RUN_ID" \
  -H 'Content-Type: application/json' --data-binary "$SHARED_BODY")
USER_SHARED_ID=$(jq -er '.id' <<<"$USER_SHARED")
ADMIN_SHARED_ID=$(jq -er '.id' <<<"$ADMIN_SHARED")
test "$USER_SHARED_ID" != "$ADMIN_SHARED_ID"
jq -e --arg id "$SHARED_RESOURCE_ID" '.resource.id == $id and .resource.available' \
  <<<"$USER_SHARED"
curl -fsS -X POST "http://localhost:8000/v1/leases/$USER_SHARED_ID/release" \
  -H "Authorization: Bearer $USER_TOKEN" | jq -e '.state == "Released"'
curl -fsS -X POST "http://localhost:8000/v1/leases/$USER_SHARED_ID/release" \
  -H "Authorization: Bearer $USER_TOKEN" | jq -e '.state == "Released"'
curl -fsS -X POST "http://localhost:8000/v1/leases/$ADMIN_SHARED_ID/release" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.state == "Released"'
```

## Verify automatic expiration

An explicit finite TTL overrides an optional resource's indefinite default.
The Compose worker polls every second by default:

```bash
EXPIRING_RESOURCE=$(curl -fsS http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "lease-expiring-$RUN_ID" --arg run_id "$RUN_ID" \
    '{name: $name, type: "manual-expiring", sharing_mode: "Exclusive",
      visibility_mode: "Public", labels: {run_id: $run_id}}')")
EXPIRING_RESOURCE_ID=$(jq -er '.id' <<<"$EXPIRING_RESOURCE")
EXPIRING=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" -H "Idempotency-Key: expiring-$RUN_ID" \
  -H 'Content-Type: application/json' \
  --data-binary '{"resource_type":"manual-expiring","sharing_mode":"Exclusive","ttl_seconds":1}')
EXPIRING_ID=$(jq -er '.id' <<<"$EXPIRING")
for ATTEMPT in $(seq 1 10); do
  STATE=$(curl -fsS "http://localhost:8000/v1/leases/$EXPIRING_ID" \
    -H "Authorization: Bearer $USER_TOKEN" | jq -er '.state')
  test "$STATE" = Expired && break
  sleep 1
done
test "$STATE" = Expired
```

Omitting `ttl_seconds` on this optional resource would create an indefinite
lease; explicit `null` does the same. Required resources reject explicit
`null`. Indefinite leases remain active until release or revocation.

## Clean up

```bash
for RESOURCE_ID in "$EXCLUSIVE_RESOURCE_ID" "$SHARED_RESOURCE_ID" "$EXPIRING_RESOURCE_ID"; do
  curl -fsS -X DELETE "http://localhost:8000/v1/resources/$RESOURCE_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN"
done
curl -fsS -X DELETE "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
unset ADMIN_TOKEN USER_TOKEN RUN_ID TEST_USERNAME TEST_USER TEST_USER_ID STATUS
unset EXCLUSIVE_RESOURCE EXCLUSIVE_RESOURCE_ID ACQUIRE_BODY LEASE LEASE_ID REPLAY
unset SHARED_RESOURCE SHARED_RESOURCE_ID SHARED_BODY USER_SHARED ADMIN_SHARED
unset USER_SHARED_ID ADMIN_SHARED_ID EXPIRING_RESOURCE EXPIRING_RESOURCE_ID
unset EXPIRING EXPIRING_ID ATTEMPT STATE RESOURCE_ID
docker compose down
```

Lease and audit records intentionally remain as history after cleanup.
