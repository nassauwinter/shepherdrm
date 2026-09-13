# Resource-secret smoke test

This self-contained flow verifies managed encryption, external references,
metadata-only reads, explicit administrator access, lease-authorized discovery
and access, and immediate denial after release. It requires `curl`, `jq`, and
`openssl`.

## Start with a temporary encryption key

The key remains in this shell and the API container environment; it is not
stored in PostgreSQL.

```bash
export SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID="manual-test"
MANUAL_SECRET_KEY="$(openssl rand -base64 32)"
export SHEPHERD_SECRET_ENCRYPTION_KEYS="$(jq -nc --arg key "$MANUAL_SECRET_KEY" \
  '{"manual-test": $key}')"
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
TEST_USERNAME="secret-user-$RUN_ID"
read -rsp 'Test user password (at least 12 characters): ' TEST_PASSWORD
echo
TEST_USER=$(curl -fsS http://localhost:8000/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{name: $name, display_name: "Manual Secret User", password: $password}')")
TEST_USER_ID=$(jq -er '.id' <<<"$TEST_USER")
USER_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
unset TEST_PASSWORD
```

## Create and inspect secrets

```bash
RESOURCE=$(curl -fsS http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "secret-resource-$RUN_ID" --arg run_id "$RUN_ID" \
    '{name: $name, type: "manual-secret", sharing_mode: "Exclusive",
      visibility_mode: "Public", labels: {run_id: $run_id}}')")
RESOURCE_ID=$(jq -er '.id' <<<"$RESOURCE")
read -rsp 'Managed test value: ' MANAGED_VALUE
echo
MANAGED=$(curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg value "$MANAGED_VALUE" \
    '{name: "device-login", description: "Manual managed value",
      material: {mode: "Managed", value: $value}}')")
MANAGED_ID=$(jq -er '.id' <<<"$MANAGED")
EXTERNAL=$(curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary '{"name":"vault-login","material":{"mode":"External",
    "provider":"vault","reference":"qa/manual/device-login"}}')
EXTERNAL_ID=$(jq -er '.id' <<<"$EXTERNAL")

METADATA=$(curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
jq -e 'length == 2 and all(.[]; has("value") | not) and
  all(.[]; has("reference") | not)' <<<"$METADATA"
ADMIN_ACCESS=$(curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets/$MANAGED_ID/access" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
jq -e --arg value "$MANAGED_VALUE" '.mode == "Managed" and .value == $value' \
  <<<"$ADMIN_ACCESS"
EXTERNAL_ACCESS=$(curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets/$EXTERNAL_ID/access" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
jq -e '.mode == "External" and .provider == "vault" and
  .reference == "qa/manual/device-login"' <<<"$EXTERNAL_ACCESS"
```

## Access through an active lease

```bash
LEASE=$(curl -fsS http://localhost:8000/v1/leases \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Idempotency-Key: secret-$RUN_ID" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg run_id "$RUN_ID" \
    '{resource_type: "manual-secret", sharing_mode: "Exclusive",
      labels: {run_id: $run_id}, ttl_seconds: 300}')")
LEASE_ID=$(jq -er '.id' <<<"$LEASE")
LEASE_SECRETS=$(curl -fsS "http://localhost:8000/v1/leases/$LEASE_ID/secrets" \
  -H "Authorization: Bearer $USER_TOKEN")
jq -e --arg managed "$MANAGED_ID" --arg external "$EXTERNAL_ID" '
  any(.[]; .id == $managed) and any(.[]; .id == $external) and
  all(.[]; has("value") | not) and all(.[]; has("reference") | not)' \
  <<<"$LEASE_SECRETS"
LEASE_ACCESS=$(curl -fsS -X POST \
  "http://localhost:8000/v1/leases/$LEASE_ID/secrets/$MANAGED_ID/access" \
  -H "Authorization: Bearer $USER_TOKEN")
jq -e --arg value "$MANAGED_VALUE" '.value == $value' <<<"$LEASE_ACCESS"
curl -fsS -X POST "http://localhost:8000/v1/leases/$LEASE_ID/release" \
  -H "Authorization: Bearer $USER_TOKEN" | jq -e '.state == "Released"'
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "http://localhost:8000/v1/leases/$LEASE_ID/secrets/$MANAGED_ID/access" \
  -H "Authorization: Bearer $USER_TOKEN")
test "$STATUS" = 404
```

## Clean up

```bash
curl -fsS -X DELETE "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets/$MANAGED_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/resources/$RESOURCE_ID/secrets/$EXTERNAL_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
unset ADMIN_TOKEN USER_TOKEN MANAGED_VALUE MANUAL_SECRET_KEY
unset SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID SHEPHERD_SECRET_ENCRYPTION_KEYS
docker compose down
```
