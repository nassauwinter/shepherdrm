# Manual identity testing

This flow verifies administrator bootstrap, local login, user creation,
authorization, and user login against a local Docker Compose deployment. It
requires `curl` and `jq`.

## Start the service

Build and start the complete local stack:

```bash
docker compose up --build -d
```

Confirm that the API and database schema are ready:

```bash
curl -fsS http://localhost:8000/ready | jq
```

The expected response is `{"status":"ready"}`.

## Bootstrap an administrator

Create the first administrator if the database does not already contain an
active administrator with password credentials:

```bash
docker compose run --rm shepherd-rm shepherd-rm-bootstrap-admin admin
```

The command prompts for the password without displaying it. Refusing to create
another administrator when a usable one already exists is expected behavior.

## Authenticate as the administrator

Read the administrator password without storing it in the command itself, then
request a login token:

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

Verify the authenticated principal:

```bash
curl -fsS http://localhost:8000/v1/me \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

## Create and inspect a regular user

Use a unique name so the flow can be repeated even though normal API deletion
archives records rather than permanently deleting them:

```bash
TEST_USERNAME="manual-tester-$(date +%s)"
read -rsp 'Test user password (at least 12 characters): ' TEST_PASSWORD
echo
TEST_USER=$(
  curl -fsS http://localhost:8000/v1/users \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "$(jq -n \
      --arg name "$TEST_USERNAME" \
      --arg password "$TEST_PASSWORD" \
      '{name: $name, display_name: "Manual Test User", role: "User", password: $password}')"
)
TEST_USER_ID=$(jq -er '.id' <<<"$TEST_USER")
jq <<<"$TEST_USER"
```

List all users as the administrator:

```bash
curl -fsS http://localhost:8000/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

## Authenticate as the regular user

```bash
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

Verify that the token identifies the newly created user:

```bash
curl -fsS http://localhost:8000/v1/me \
  -H "Authorization: Bearer $USER_TOKEN" | jq
```

Verify that a regular user cannot administer users. This request should return
HTTP `403`:

```bash
curl -sS -o /tmp/shepherd-rm-forbidden.json -w '%{http_code}\n' \
  http://localhost:8000/v1/users \
  -H "Authorization: Bearer $USER_TOKEN"
jq < /tmp/shepherd-rm-forbidden.json
```

## Clean up the test user

Archive only the user created by this flow:

```bash
curl -fsS -X DELETE \
  "http://localhost:8000/v1/users/$TEST_USER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

The endpoint returns HTTP `204`. The user's existing tokens stop authenticating,
but the archived record and its audit history remain in PostgreSQL by design.
Clear secrets and temporary response data from the current shell:

```bash
unset ADMIN_TOKEN USER_TOKEN TEST_USER TEST_USER_ID TEST_USERNAME
rm -f /tmp/shepherd-rm-forbidden.json
```

Stop the containers while preserving the local PostgreSQL data:

```bash
docker compose down
```

For a complete reset of a disposable local installation, including the
administrator, archived records, and audit history, remove the Compose volume:

```bash
docker compose down -v
```

This final command permanently deletes the local Shepherd RM database. Do not
use it for an installation containing data that must be retained.
