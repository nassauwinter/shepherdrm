# Resource visibility smoke test

This flow verifies restricted-resource visibility through direct principal and
group grants. It proves that access given to one user does not reveal the
resource to another user. It requires `curl` and `jq`.

See {doc}`resource-management` for resource metadata, lifecycle transitions,
filtering, and archival listing.

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

Create two uniquely named regular users, then authenticate as both:

```bash
RUN_ID="$(date +%s)-$RANDOM"
USER1_NAME="visibility-user1-$RUN_ID"
USER2_NAME="visibility-user2-$RUN_ID"
read -rsp 'Test user password (at least 12 characters): ' TEST_PASSWORD
echo
USER1=$(
  curl -fsS http://localhost:8000/v1/users \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg name "$USER1_NAME" \
      --arg password "$TEST_PASSWORD" \
      '{name: $name, display_name: "Visibility User 1", role: "User", password: $password}')"
)
USER2=$(
  curl -fsS http://localhost:8000/v1/users \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg name "$USER2_NAME" \
      --arg password "$TEST_PASSWORD" \
      '{name: $name, display_name: "Visibility User 2", role: "User", password: $password}')"
)
USER1_ID=$(jq -er '.id' <<<"$USER1")
USER2_ID=$(jq -er '.id' <<<"$USER2")
USER1_TOKEN=$(
  curl -fsS http://localhost:8000/v1/auth/login \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg username "$USER1_NAME" \
      --arg password "$TEST_PASSWORD" \
      '{username: $username, password: $password}')" |
  jq -er '.access_token'
)
USER2_TOKEN=$(
  curl -fsS http://localhost:8000/v1/auth/login \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg username "$USER2_NAME" \
      --arg password "$TEST_PASSWORD" \
      '{username: $username, password: $password}')" |
  jq -er '.access_token'
)
unset TEST_PASSWORD
```

## Create a restricted resource

Omit `visibility_mode` to verify that new resources default to `Restricted`:

```bash
RESOURCE_NAME="restricted-environment-$RUN_ID"
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
        labels: {purpose: "visibility-smoke-test", run_id: $run_id}
      }')"
)
RESOURCE_ID=$(jq -er '.id' <<<"$RESOURCE")
jq -e '
  .visibility_mode == "Restricted" and
  .expiration_mode == "Optional" and
  .default_ttl_seconds == null and
  .max_ttl_seconds == null
' <<<"$RESOURCE"
```

Before any grant, both users receive HTTP `404` and cannot discover the
resource:

```bash
for TOKEN in "$USER1_TOKEN" "$USER2_TOKEN"; do
  STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://localhost:8000/v1/resources/$RESOURCE_ID" \
    -H "Authorization: Bearer $TOKEN")
  test "$STATUS" = 404
done

for TOKEN in "$USER1_TOKEN" "$USER2_TOKEN"; do
  curl -fsS --get http://localhost:8000/v1/resources \
    -H "Authorization: Bearer $TOKEN" \
    --data-urlencode "label=run_id=$RUN_ID" |
  jq -e --arg id "$RESOURCE_ID" '.items | all(.id != $id)'
done
```

## Verify direct principal access

Grant direct access to user 1, then verify that user 1 can retrieve and discover
the resource:

```bash
curl -fsS -X PUT \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/access/principals/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN" |
jq -e --arg id "$RESOURCE_ID" '.id == $id'

curl -fsS --get http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $USER1_TOKEN" \
  --data-urlencode "label=run_id=$RUN_ID" |
jq -e --arg id "$RESOURCE_ID" '.items | any(.id == $id)'
```

User 2 remains unable to retrieve or discover it:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER2_TOKEN")
test "$STATUS" = 404

curl -fsS --get http://localhost:8000/v1/resources \
  -H "Authorization: Bearer $USER2_TOKEN" \
  --data-urlencode "label=run_id=$RUN_ID" |
jq -e --arg id "$RESOURCE_ID" '.items | all(.id != $id)'
```

Inspect the grants as the administrator and verify that only user 1 is listed:

```bash
RESOURCE_ACCESS=$(
  curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID/access" \
    -H "Authorization: Bearer $ADMIN_TOKEN"
)
jq -e \
  --arg user1_id "$USER1_ID" \
  --arg user2_id "$USER2_ID" \
  '(.principal_ids | index($user1_id)) != null and
   (.principal_ids | index($user2_id)) == null' \
  <<<"$RESOURCE_ACCESS"
```

Grant inspection is administrator-only. User 1 should receive HTTP `403`:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/access" \
  -H "Authorization: Bearer $USER1_TOKEN")
test "$STATUS" = 403
```

Revoke the direct grant and verify that user 1 immediately loses access:

```bash
curl -fsS -X DELETE \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/access/principals/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN")
test "$STATUS" = 404
```

## Verify group-based access

Create a group, add user 1, and grant the group access to the resource:

```bash
GROUP=$(
  curl -fsS http://localhost:8000/v1/groups \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n --arg name "visibility-group-$RUN_ID" \
      '{name: $name, description: "Manual visibility test group"}')"
)
GROUP_ID=$(jq -er '.id' <<<"$GROUP")

curl -fsS -X PUT \
  "http://localhost:8000/v1/groups/$GROUP_ID/members/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

curl -fsS -X PUT \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/access/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Verify that the administrator sees the group grant, user 1 gains access through
membership, and user 2 remains unable to see the resource:

```bash
curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID/access" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg group_id "$GROUP_ID" '.group_ids | index($group_id) != null'

curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN" |
jq -e --arg id "$RESOURCE_ID" '.id == $id'

STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER2_TOKEN")
test "$STATUS" = 404
```

Remove user 1 from the group and verify that access disappears immediately:

```bash
curl -fsS -X DELETE \
  "http://localhost:8000/v1/groups/$GROUP_ID/members/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN")
test "$STATUS" = 404
```

Add user 1 again and verify that membership restores access:

```bash
curl -fsS -X PUT \
  "http://localhost:8000/v1/groups/$GROUP_ID/members/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN" |
jq -e --arg id "$RESOURCE_ID" '.id == $id'
```

Archive the group while its resource grant remains. Archived groups confer no
visibility, so user 1 should lose access again:

```bash
curl -fsS -X DELETE "http://localhost:8000/v1/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN")
test "$STATUS" = 404
```

## Verify archival visibility

Grant user 1 direct access again and confirm that the restricted resource is
visible immediately before archival:

```bash
curl -fsS -X PUT \
  "http://localhost:8000/v1/resources/$RESOURCE_ID/access/principals/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

curl -fsS "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN" |
jq -e --arg id "$RESOURCE_ID" '.id == $id'
```

Archive the resource, then verify that user 1 receives HTTP `404` despite the
remaining direct grant:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN")
test "$STATUS" = 204

STATUS=$(curl -sS \
  -o "/tmp/shepherd-rm-visibility-not-found-$RUN_ID.json" \
  -w '%{http_code}' \
  "http://localhost:8000/v1/resources/$RESOURCE_ID" \
  -H "Authorization: Bearer $USER1_TOKEN")
test "$STATUS" = 404
jq < "/tmp/shepherd-rm-visibility-not-found-$RUN_ID.json"
```

## Clean up

The resource and group are already archived. Archive only the two temporary
users created by this walkthrough:

```bash
curl -fsS -X DELETE \
  "http://localhost:8000/v1/users/$USER1_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE \
  "http://localhost:8000/v1/users/$USER2_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Clear tokens, identifiers, and temporary responses from the current shell:

```bash
rm -f "/tmp/shepherd-rm-visibility-not-found-$RUN_ID.json"
unset ADMIN_TOKEN USER1_TOKEN USER2_TOKEN RUN_ID USER1_NAME USER2_NAME
unset USER1 USER2 USER1_ID USER2_ID STATUS TOKEN
unset RESOURCE RESOURCE_ACCESS RESOURCE_ID RESOURCE_NAME GROUP GROUP_ID
```

Stop the containers while preserving the local PostgreSQL data:

```bash
docker compose down
```
