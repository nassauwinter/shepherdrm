"""Exercise a deployed release image through its public HTTP contract."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HttpResult:
    """Capture one bounded HTTP response without raising for expected errors."""

    status: int
    body: Any
    headers: dict[str, str]


@dataclass(frozen=True)
class AcceptanceState:
    """Identify durable records used to prove restart and restore behavior."""

    run_id: str
    admin_token: str
    user_token: str
    resource_id: str
    secret_id: str
    released_lease_id: str
    secret_value: str


class ApiClient:
    """Send dependency-free JSON requests to a running Shepherd RM API."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Any | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        """Return status, decoded body, and normalized headers for one request."""
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        body = raw_body
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=request_headers, method=method
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            return self._result(error.code, error.read(), dict(error.headers.items()))
        with response:
            return self._result(response.status, response.read(), dict(response.headers.items()))

    @staticmethod
    def _result(status: int, body: bytes, headers: dict[str, str]) -> HttpResult:
        """Decode JSON when possible while retaining safe text for diagnostics."""
        text = body.decode(errors="replace")
        try:
            decoded: Any = json.loads(text) if text else None
        except json.JSONDecodeError:
            decoded = text
        return HttpResult(status, decoded, {key.lower(): value for key, value in headers.items()})


def expect(result: HttpResult, status: int, context: str) -> Any:
    """Return a response body or raise a bounded diagnostic on mismatch."""
    if result.status != status:
        raise RuntimeError(
            f"{context}: expected HTTP {status}, got {result.status}: {result.body!r}"
        )
    return result.body


def wait_ready(api: ApiClient, *, ready: bool = True, timeout: float = 60.0) -> None:
    """Wait until readiness reaches the requested available or unavailable state."""
    deadline = time.monotonic() + timeout
    last_status: int | None = None
    while time.monotonic() < deadline:
        try:
            result = api.request("GET", "/ready")
            last_status = result.status
            if (result.status == 200) is ready:
                return
        except (urllib.error.URLError, OSError):
            if not ready:
                return
        time.sleep(1)
    expected = "ready" if ready else "unavailable"
    raise RuntimeError(f"API did not become {expected}; last readiness status was {last_status}")


def login(api: ApiClient, username: str, password: str) -> str:
    """Authenticate one local user and return its short-lived bearer token."""
    body = expect(
        api.request(
            "POST", "/v1/auth/login", json_body={"username": username, "password": password}
        ),
        200,
        f"login for {username}",
    )
    return str(body["access_token"])


def create_resource(
    api: ApiClient,
    token: str,
    *,
    name: str,
    resource_type: str,
    run_id: str,
    visibility: str = "Public",
) -> dict[str, Any]:
    """Create one exclusive release-test resource with a finite default lease."""
    body = expect(
        api.request(
            "POST",
            "/v1/resources",
            token=token,
            json_body={
                "name": name,
                "type": resource_type,
                "sharing_mode": "Exclusive",
                "visibility_mode": visibility,
                "expiration_mode": "Required",
                "default_ttl_seconds": 60,
                "max_ttl_seconds": 300,
                "labels": {"release_acceptance": run_id},
            },
        ),
        201,
        f"create resource {name}",
    )
    return dict(body)


def run_api_smoke(
    api: ApiClient, admin_username: str, admin_password: str, user_password: str
) -> AcceptanceState:
    """Exercise identity, resource, lease, and managed-secret behavior end to end."""
    run_id = uuid.uuid4().hex
    admin_token = login(api, admin_username, admin_password)
    principal = expect(api.request("GET", "/v1/me", token=admin_token), 200, "read admin")
    if principal["role"] != "Admin":
        raise RuntimeError("Bootstrapped principal is not an administrator")

    username = f"acceptance-{run_id}"
    expect(
        api.request(
            "POST",
            "/v1/users",
            token=admin_token,
            json_body={
                "name": username,
                "display_name": "Release acceptance user",
                "password": user_password,
            },
        ),
        201,
        "create acceptance user",
    )
    user_token = login(api, username, user_password)

    resource = create_resource(
        api,
        admin_token,
        name=f"acceptance-resource-{run_id}",
        resource_type=f"acceptance-{run_id}",
        run_id=run_id,
    )
    resource_id = str(resource["id"])
    secret_value = f"release-secret-{uuid.uuid4().hex}"
    secret = expect(
        api.request(
            "POST",
            f"/v1/resources/{resource_id}/secrets",
            token=admin_token,
            json_body={
                "name": "acceptance-secret",
                "material": {"mode": "Managed", "value": secret_value},
            },
        ),
        201,
        "create managed secret",
    )
    secret_id = str(secret["id"])
    metadata = expect(
        api.request("GET", f"/v1/resources/{resource_id}/secrets", token=admin_token),
        200,
        "list secret metadata",
    )
    if any("value" in item or "reference" in item for item in metadata):
        raise RuntimeError("Secret metadata exposed material")

    lease = expect(
        api.request(
            "POST",
            "/v1/leases",
            token=user_token,
            headers={"Idempotency-Key": f"acceptance-{run_id}"},
            json_body={
                "resource_type": f"acceptance-{run_id}",
                "sharing_mode": "Exclusive",
                "labels": {"release_acceptance": run_id},
            },
        ),
        201,
        "acquire acceptance lease",
    )
    lease_id = str(lease["id"])
    accessed = expect(
        api.request(
            "POST",
            f"/v1/leases/{lease_id}/secrets/{secret_id}/access",
            token=user_token,
        ),
        200,
        "access secret through lease",
    )
    if accessed.get("value") != secret_value:
        raise RuntimeError("Managed-secret round trip changed the value")
    released = expect(
        api.request("POST", f"/v1/leases/{lease_id}/release", token=user_token),
        200,
        "release acceptance lease",
    )
    if released["state"] != "Released":
        raise RuntimeError("Released lease did not reach Released state")
    expect(
        api.request(
            "POST",
            f"/v1/leases/{lease_id}/secrets/{secret_id}/access",
            token=user_token,
        ),
        404,
        "deny secret access after release",
    )
    return AcceptanceState(
        run_id,
        admin_token,
        user_token,
        resource_id,
        secret_id,
        lease_id,
        secret_value,
    )


def run_security_probes(api: ApiClient, state: AcceptanceState) -> str:
    """Probe abuse boundaries and return the canary that must stay out of logs."""
    malformed = api.request("GET", "/v1/me", headers={"Authorization": "Basic invalid"})
    expect(malformed, 401, "reject malformed authorization")

    correlation_canary = f"hostile/correlation/{uuid.uuid4().hex}"
    health = expect(
        api.request("GET", "/health", headers={"X-Correlation-ID": correlation_canary}),
        200,
        "health with hostile correlation ID",
    )
    if correlation_canary in json.dumps(health):
        raise RuntimeError("Hostile correlation ID was reflected")

    restricted = create_resource(
        api,
        state.admin_token,
        name=f"restricted-{state.run_id}",
        resource_type=f"restricted-{state.run_id}",
        run_id=state.run_id,
        visibility="Restricted",
    )
    expect(
        api.request("GET", f"/v1/resources/{restricted['id']}", token=state.user_token),
        404,
        "hide inaccessible resource",
    )

    body_canary = f"oversized-secret-{uuid.uuid4().hex}"
    oversized = api.request(
        "POST",
        "/v1/auth/login",
        raw_body=(body_canary.encode() + b"x" * 1_048_576),
        headers={"Content-Type": "application/json"},
    )
    expect(oversized, 413, "reject oversized request")
    if body_canary in json.dumps(oversized.body):
        raise RuntimeError("Oversized request reflected secret material")

    ghost_username = f"missing-{state.run_id}"
    statuses = [
        api.request(
            "POST",
            "/v1/auth/login",
            json_body={"username": ghost_username, "password": "wrong-password"},
        ).status
        for _ in range(11)
    ]
    if statuses[-1] != 429 or any(status not in {401, 429} for status in statuses):
        raise RuntimeError(f"Login abuse was not rate limited: {statuses}")
    return body_canary


def run_concurrency_probe(api: ApiClient, state: AcceptanceState) -> dict[str, float | int]:
    """Prove parallel acquisition preserves exclusive allocation and terminates promptly."""
    resource_type = f"concurrency-{state.run_id}"
    resource_count = 4
    request_count = 8
    for index in range(resource_count):
        create_resource(
            api,
            state.admin_token,
            name=f"concurrency-{index}-{state.run_id}",
            resource_type=resource_type,
            run_id=state.run_id,
        )

    def acquire(index: int) -> tuple[HttpResult, float]:
        started = time.monotonic()
        result = api.request(
            "POST",
            "/v1/leases",
            token=state.user_token,
            headers={"Idempotency-Key": f"concurrency-{state.run_id}-{index}"},
            json_body={
                "resource_type": resource_type,
                "sharing_mode": "Exclusive",
                "labels": {"release_acceptance": state.run_id},
            },
        )
        return result, time.monotonic() - started

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        outcomes = list(executor.map(acquire, range(request_count)))
    successes = [result for result, _duration in outcomes if result.status == 201]
    conflicts = [result for result, _duration in outcomes if result.status == 409]
    if len(successes) != resource_count or len(conflicts) != request_count - resource_count:
        raise RuntimeError(
            f"Unexpected parallel allocation outcomes: {len(successes)} acquired, "
            f"{len(conflicts)} unavailable"
        )
    resource_ids = {str(result.body["resource"]["id"]) for result in successes}
    if len(resource_ids) != resource_count:
        raise RuntimeError("Parallel exclusive acquisitions reused a resource")
    for result in successes:
        expect(
            api.request("POST", f"/v1/leases/{result.body['id']}/release", token=state.user_token),
            200,
            "release concurrent lease",
        )
    durations = [duration for _result, duration in outcomes]
    return {
        "requests": request_count,
        "acquired": len(successes),
        "unavailable": len(conflicts),
        "maximum_seconds": round(max(durations), 3),
        "mean_seconds": round(sum(durations) / len(durations), 3),
    }


def create_expiring_lease(api: ApiClient, state: AcceptanceState) -> str:
    """Create a one-second lease used to prove worker catch-up after downtime."""
    resource_type = f"expiration-{state.run_id}"
    create_resource(
        api,
        state.admin_token,
        name=f"expiration-{state.run_id}",
        resource_type=resource_type,
        run_id=state.run_id,
    )
    lease = expect(
        api.request(
            "POST",
            "/v1/leases",
            token=state.user_token,
            headers={"Idempotency-Key": f"expiration-{state.run_id}"},
            json_body={
                "resource_type": resource_type,
                "sharing_mode": "Exclusive",
                "labels": {"release_acceptance": state.run_id},
                "ttl_seconds": 1,
            },
        ),
        201,
        "create expiring lease",
    )
    return str(lease["id"])


def wait_lease_state(
    api: ApiClient, token: str, lease_id: str, state: str, timeout: float = 30.0
) -> None:
    """Wait for one lease to reach an externally observable lifecycle state."""
    deadline = time.monotonic() + timeout
    last_state: str | None = None
    while time.monotonic() < deadline:
        lease = expect(
            api.request("GET", f"/v1/leases/{lease_id}", token=token),
            200,
            "read lease state",
        )
        last_state = str(lease["state"])
        if last_state == state:
            return
        time.sleep(1)
    raise RuntimeError(f"Lease {lease_id} did not reach {state}; last state was {last_state}")
