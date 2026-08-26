"""Provide focused setup helpers shared by leasing integration tests."""

from __future__ import annotations

from typing import Any, cast

import httpx

from tests.integration.support import IdentityEnvironment


async def create_owned_lease(
    client: httpx.AsyncClient,
    environment: IdentityEnvironment,
    prefix: str,
    *,
    default_ttl_seconds: int | None = None,
    max_ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Create a public exclusive resource and acquire it as the regular test user."""
    resource_type = environment.name(prefix)
    body: dict[str, object] = {
        "name": environment.name(f"{prefix}-resource"),
        "type": resource_type,
        "sharing_mode": "Exclusive",
        "visibility_mode": "Public",
    }
    if default_ttl_seconds is not None:
        body.update(
            expiration_mode="Required",
            default_ttl_seconds=default_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
        )
    created = await client.post(
        "/v1/resources", headers=environment.authorization("admin"), json=body
    )
    assert created.status_code == 201
    acquired = await client.post(
        "/v1/leases",
        headers={
            **environment.authorization("user"),
            "Idempotency-Key": environment.name(f"{prefix}-key"),
        },
        json={"resource_type": resource_type, "sharing_mode": "Exclusive"},
    )
    assert acquired.status_code == 201
    return cast(dict[str, Any], acquired.json())
