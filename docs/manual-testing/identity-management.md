# Identity management smoke test

This flow verifies the implemented identity features through the public API:
bootstrap, login, users and passwords, personal tokens, groups, service
identities, service tokens, authorization, and archival. It requires `curl`
and `jq`.

## Start and authenticate

```bash
docker compose up --build -d
curl -fsS http://localhost:8000/health | jq -e '.status == "ok"'
curl -fsS http://localhost:8000/ready | jq -e '.status == "ready"'
docker compose run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
```

The bootstrap command prompts for the password and refuses to create another
administrator when a usable one already exists. Authenticate that administrator:

```bash
read -rsp 'Administrator password: ' ADMIN_PASSWORD
echo
ADMIN_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username admin --arg password "$ADMIN_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
unset ADMIN_PASSWORD
curl -fsS http://localhost:8000/v1/me \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e '.kind == "User" and .role == "Admin" and .archived_at == null'
```

## Manage a user and passwords

```bash
RUN_ID="$(date +%s)-$RANDOM"
TEST_USERNAME="identity-user-$RUN_ID"
read -rsp 'Initial test password (at least 12 characters): ' TEST_PASSWORD
echo
TEST_USER=$(curl -fsS http://localhost:8000/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{name: $name, display_name: "Manual Identity User", role: "User", password: $password}')")
TEST_USER_ID=$(jq -er '.id' <<<"$TEST_USER")

curl -fsS http://localhost:8000/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg id "$TEST_USER_ID" 'any(.id == $id)'
curl -fsS "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg name "$TEST_USERNAME" '.name == $name and .kind == "User"'
curl -fsS -X PATCH "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"display_name":"Updated Manual Identity User"}' |
jq -e '.display_name == "Updated Manual Identity User"'

USER_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
curl -fsS http://localhost:8000/v1/me -H "Authorization: Bearer $USER_TOKEN" |
jq -e --arg id "$TEST_USER_ID" '.id == $id and .role == "User"'
```

A regular user cannot administer users; this must return `403`:

```bash
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/users \
  -H "Authorization: Bearer $USER_TOKEN")
test "$STATUS" = 403
```

Exercise both self-service password change and administrator password reset:

```bash
read -rsp 'Replacement password (at least 12 characters): ' NEW_PASSWORD
echo
curl -fsS -X PUT http://localhost:8000/v1/me/password \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg password "$NEW_PASSWORD" '{password: $password}')"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$TEST_PASSWORD" \
    '{username: $username, password: $password}')")
test "$STATUS" = 401
unset TEST_PASSWORD

USER_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$NEW_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
read -rsp 'Administrator-set password (at least 12 characters): ' RESET_PASSWORD
echo
curl -fsS -X PUT "http://localhost:8000/v1/users/$TEST_USER_ID/password" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg password "$RESET_PASSWORD" '{password: $password}')"
USER_TOKEN=$(curl -fsS http://localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg username "$TEST_USERNAME" --arg password "$RESET_PASSWORD" \
    '{username: $username, password: $password}')" | jq -er '.access_token')
unset NEW_PASSWORD RESET_PASSWORD
```

## Manage a personal API token

The plaintext token is returned only when created and is absent from listings:

```bash
PERSONAL_RESPONSE=$(curl -fsS http://localhost:8000/v1/me/api-tokens \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "manual-personal-$RUN_ID" '{name: $name}')")
PERSONAL_ID=$(jq -er '.id' <<<"$PERSONAL_RESPONSE")
PERSONAL_TOKEN=$(jq -er '.token' <<<"$PERSONAL_RESPONSE")
curl -fsS http://localhost:8000/v1/me/api-tokens \
  -H "Authorization: Bearer $USER_TOKEN" |
jq -e --arg id "$PERSONAL_ID" 'any(.id == $id and (has("token") | not))'
curl -fsS http://localhost:8000/v1/me -H "Authorization: Bearer $PERSONAL_TOKEN" |
jq -e --arg id "$TEST_USER_ID" '.id == $id'
curl -fsS -X DELETE "http://localhost:8000/v1/me/api-tokens/$PERSONAL_ID" \
  -H "Authorization: Bearer $USER_TOKEN"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/me \
  -H "Authorization: Bearer $PERSONAL_TOKEN")
test "$STATUS" = 401
unset PERSONAL_TOKEN
```

## Manage a group

```bash
GROUP=$(curl -fsS http://localhost:8000/v1/groups \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "identity-group-$RUN_ID" \
    '{name: $name, description: "Manual identity group"}')")
GROUP_ID=$(jq -er '.id' <<<"$GROUP")
curl -fsS http://localhost:8000/v1/groups -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg id "$GROUP_ID" 'any(.id == $id)'
curl -fsS "http://localhost:8000/v1/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e --arg id "$GROUP_ID" '.id == $id'
curl -fsS -X PATCH "http://localhost:8000/v1/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"description":"Updated manual identity group"}' |
jq -e '.description == "Updated manual identity group"'
curl -fsS -X PUT "http://localhost:8000/v1/groups/$GROUP_ID/members/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/groups/$GROUP_ID/members/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Membership writes return `204` and are idempotent. Group-based resource access
is tested in {doc}`resource-visibility`.

## Manage a service identity and token

```bash
SERVICE=$(curl -fsS http://localhost:8000/v1/service-identities \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "identity-service-$RUN_ID" \
    '{name: $name, display_name: "Manual Identity Service", role: "User"}')")
SERVICE_ID=$(jq -er '.id' <<<"$SERVICE")
curl -fsS http://localhost:8000/v1/service-identities \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg id "$SERVICE_ID" 'any(.id == $id)'
curl -fsS "http://localhost:8000/v1/service-identities/$SERVICE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.kind == "Service"'
curl -fsS -X PATCH "http://localhost:8000/v1/service-identities/$SERVICE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"display_name":"Updated Manual Identity Service"}' |
jq -e '.display_name == "Updated Manual Identity Service"'

SERVICE_RESPONSE=$(curl -fsS \
  "http://localhost:8000/v1/service-identities/$SERVICE_ID/api-tokens" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary "$(jq -n --arg name "manual-service-$RUN_ID" '{name: $name}')")
SERVICE_TOKEN_ID=$(jq -er '.id' <<<"$SERVICE_RESPONSE")
SERVICE_TOKEN=$(jq -er '.token' <<<"$SERVICE_RESPONSE")
curl -fsS "http://localhost:8000/v1/service-identities/$SERVICE_ID/api-tokens" \
  -H "Authorization: Bearer $ADMIN_TOKEN" |
jq -e --arg id "$SERVICE_TOKEN_ID" 'any(.id == $id and (has("token") | not))'
curl -fsS http://localhost:8000/v1/me -H "Authorization: Bearer $SERVICE_TOKEN" |
jq -e --arg id "$SERVICE_ID" '.id == $id and .kind == "Service"'
curl -fsS -X DELETE \
  "http://localhost:8000/v1/service-identities/$SERVICE_ID/api-tokens/$SERVICE_TOKEN_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/v1/me \
  -H "Authorization: Bearer $SERVICE_TOKEN")
test "$STATUS" = 401
unset SERVICE_TOKEN
```

## Clean up

```bash
curl -fsS -X DELETE "http://localhost:8000/v1/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/service-identities/$SERVICE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS -X DELETE "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -fsS "http://localhost:8000/v1/groups/$GROUP_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.archived_at != null'
curl -fsS "http://localhost:8000/v1/service-identities/$SERVICE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -e '.archived_at != null'

unset ADMIN_TOKEN USER_TOKEN RUN_ID TEST_USERNAME TEST_USER TEST_USER_ID STATUS
unset PERSONAL_RESPONSE PERSONAL_ID GROUP GROUP_ID SERVICE SERVICE_ID
unset SERVICE_RESPONSE SERVICE_TOKEN_ID
docker compose down
```

Archival invalidates the user's credentials, while archived records and audit
history remain in PostgreSQL.
