# Resource management smoke test

This flow verifies resource creation, public discovery, authorization,
optimistic updates, operational transitions, and archival through the public
API. It requires `curl` and `jq`.

Resource leasing is not implemented yet, so this walkthrough verifies catalog
availability only for a resource with no active leases. See
{doc}`resource-visibility` for restricted resources and access grants.

## Start the service

Build and start the complete local stack, then wait for the API and database
schema to become ready:

```bash
docker compose up --build -d
curl -fsS http://localhost:8000/ready | jq
```

The expected readiness response is `{"status":"ready"}`.

## Prepare administrator and user access

Create the first administrator if the database does not already contain one:

```bash
docker compose run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
```

Refusing to create another administrator when a usable one already exists is
expected. Authenticate as that administrator:

```bash
read -rsp 'Administrator password: ' ADMIN_PASSWORD
echo
ADMIN_TOKEN=$(
  curl -fsS http://localhost:8000/v1/auth/login \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg username admin \
      --arg password "$ADMIN_PASSWORD" \
      '{username: $username, password: $password}')" |
  jq -er '.access_token'
)
unset ADMIN_PASSWORD
```

Create a uniquely named regular user for discovery and permission checks, then
authenticate as that user:

```bash
RUN_ID="$(date +%s)-$RANDOM"
TEST_USERNAME="catalog-manager-$RUN_ID"
read -rsp 'Test user password (at least 12 characters): ' TEST_PASSWORD
echo
TEST_USER=$(
  curl -fsS http://localhost:8000/v1/users \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg name "$TEST_USERNAME" \
      --arg password "$TEST_PASSWORD" \
      '{name: $name, display_name: "Catalog Management User", role: "User", password: $password}')"
)
TEST_USER_ID=$(jq -er '.id' <<<"$TEST_USER")
USER_TOKEN=$(
  curl -fsS http://localhost:8000/v1/auth/login \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg username "$TEST_USERNAME" \
      --arg password "$TEST_PASSWORD" \
      '{username: $username, password: $password}')" |
  jq -er '.access_token'
)
unset TEST_PASSWORD
```

## Create a public resource

Create an exclusive test environment that every authenticated principal can
discover:

```bash
RESOURCE_NAME="manual-environment-$RUN_ID"
RESOURCE=$(
  curl -fsS http://localhost:8000/v1/resources \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg name "$RESOURCE_NAME" \
      --arg run_id "$RUN_ID" \
      '{
        name: $name,
        type: "environment",
        sharing_mode: "Exclusive",
        visibility_mode: "Public",
        labels: {purpose: "manual-smoke-test", region: "local", run_id: $run_id}
      }')"
)
RESOURCE_ID=$(jq -er '.id' <<<"$RESOURCE")
RESOURCE_VERSION=$(jq -er '.version' <<<"$RESOURCE")
jq -e '
  .operational_status == "Active" and
  .visibility_mode == "Public" and
  .available == true and
  .active_lease_count == 0
' <<<"$RESOURCE"
```

The creation request returns HTTP `201`. The assertion succeeds only when the
new resource is active, public, and available with no active leases.

## Discover the resource

Retrieve the resource directly using the regular user's token:

```bash
curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER_TOKEN" |
jq -e --arg id "$RESOURCE_ID" '.id == $id'
```

Find it using exact type, sharing-mode, availability, and label filters:

```bash
curl -fsS --get http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $USER_TOKEN" \
  --data-urlencode 'type=environment' \
  --data-urlencode 'sharing_mode=Exclusive' \
  --data-urlencode 'available=true' \
  --data-urlencode 'label=purpose=manual-smoke-test' \
  --data-urlencode "label=run_id=$RUN_ID" |
jq -e --arg id "$RESOURCE_ID" '.items | any(.id == $id)'
```

Verify that a regular user cannot update resource metadata. Assert HTTP `403`
and inspect the problem-details response:

```bash
STATUS=$(curl -sS \
  -o "/tmp/shepherd-rm-management-forbidden-$RUN_ID.json" \
  -w '%{http_code}' \
  -X PATCH "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --argjson version "$RESOURCE_VERSION" \
    '{version: $version, labels: {purpose: "unauthorized-update"}}')")
test "$STATUS" = 403
jq < "/tmp/shepherd-rm-management-forbidden-$RUN_ID.json"
```

## Update and transition the resource

Replace the labels using the current resource version:

```bash
UPDATED_RESOURCE=$(
  curl -fsS -X PATCH "http://localhost:8000/v1/resources/$RESOURCE_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --argjson version "$RESOURCE_VERSION" \
      --arg run_id "$RUN_ID" \
      '{
        version: $version,
        labels: {purpose: "manual-smoke-test", region: "updated", run_id: $run_id}
      }')"
)
jq -e --argjson previous "$RESOURCE_VERSION" \
  '.version == ($previous + 1) and .labels.region == "updated"' \
  <<<"$UPDATED_RESOURCE"
RESOURCE_VERSION=$(jq -er '.version' <<<"$UPDATED_RESOURCE")
```

Disable and enable the resource, verifying derived availability after each
transition:

```bash
curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/disable" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e '.operational_status == "Disabled" and .available == false'

curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/enable" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e '.operational_status == "Active" and .available == true'
```

Quarantine and recover it to exercise the remaining administrative state
transitions:

```bash
curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/quarantine" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e '.operational_status == "Quarantined" and .available == false'

curl -fsS -X POST \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/recover" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e '.operational_status == "Active" and .available == true'
```

## Archive the resource

Archive the public resource and assert HTTP `204`:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
test "$STATUS" = 204
```

Although it remains public, archival hides it from the regular user. Assert
HTTP `404` and inspect the problem-details response:

```bash
STATUS=$(curl -sS \
  -o "/tmp/shepherd-rm-management-not-found-$RUN_ID.json" \
  -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER_TOKEN")
test "$STATUS" = 404
jq < "/tmp/shepherd-rm-management-not-found-$RUN_ID.json"
```

Normal discovery excludes the resource, while an administrator can explicitly
include archived records:

```bash
curl -fsS --get http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-urlencode "label=run_id=$RUN_ID" |
jq -e --arg id "$RESOURCE_ID" '.items | all(.id != $id)'

curl -fsS --get http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-urlencode "label=run_id=$RUN_ID" \
  --data-urlencode 'include_archived=true' |
jq -e --arg id "$RESOURCE_ID" '.items | any(.id == $id and .archived_at != null)'
```

## Clean up

Archive only the temporary user created by this walkthrough:

```bash
curl -fsS -X DELETE \
  "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Clear tokens, identifiers, and temporary responses from the current shell:

```bash
rm -f "/tmp/shepherd-rm-management-forbidden-$RUN_ID.json"
rm -f "/tmp/shepherd-rm-management-not-found-$RUN_ID.json"
unset ADMIN_TOKEN USER_TOKEN RUN_ID TEST_USERNAME TEST_USER TEST_USER_ID STATUS
unset RESOURCE RESOURCE_ID RESOURCE_NAME RESOURCE_VERSION UPDATED_RESOURCE
```

Stop the containers while preserving the local PostgreSQL data:

```bash
docker compose down
```
