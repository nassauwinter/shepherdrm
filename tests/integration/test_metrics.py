"""Verify authenticated metrics against real PostgreSQL state."""

import httpx
import pytest

from tests.integration.support import IdentityEnvironment, create_test_resource

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_metrics_require_an_administrator(
    identity_client: httpx.AsyncClient,
    identity_environment: IdentityEnvironment,
) -> None:
    """Anonymous and regular-user scrapes cannot inspect operational metrics."""
    anonymous = await identity_client.get("/metrics")
    user = await identity_client.get("/metrics", headers=identity_environment.authorization("user"))

    assert anonymous.status_code == 401
    assert user.status_code == 403


async def test_metrics_report_bounded_request_allocation_and_database_state(
    identity_client: httpx.AsyncClient,
    identity_environment: IdentityEnvironment,
) -> None:
    """An administrator sees core signals without record or credential identifiers."""
    resource = await create_test_resource(
        identity_client,
        identity_environment,
        "metrics-resource",
        visibility_mode="Public",
    )
    acquired = await identity_client.post(
        "/v1/leases",
        headers={
            **identity_environment.authorization("user"),
            "Idempotency-Key": "metrics-acquisition",
        },
        json={"resource_type": "environment", "sharing_mode": "Exclusive"},
    )
    assert acquired.status_code == 201

    response = await identity_client.get(
        "/metrics", headers=identity_environment.authorization("admin")
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=1.0.0; charset=utf-8"
    assert "shepherd_rm_http_requests_total" in response.text
    assert 'route="/v1/leases"' in response.text
    assert "shepherd_rm_allocation_attempts_total" in response.text
    assert "shepherd_rm_active_leases" in response.text
    assert "shepherd_rm_resource_utilization_ratio" in response.text
    assert "shepherd_rm_expired_leases" in response.text
    assert "shepherd_rm_expiration_backlog_leases" in response.text
    assert "shepherd_rm_expiration_lag_seconds" in response.text
    assert "shepherd_rm_worker_metrics_available" in response.text
    assert "shepherd_rm_worker_loops_total" in response.text
    assert resource["id"] not in response.text
    assert str(identity_environment.user_id) not in response.text
    assert identity_environment.admin_token not in response.text
