"""Expose authenticated lease acquisition, reads, and lifecycle operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from shepherd_rm.config import Settings
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticatedPrincipal
from shepherd_rm.leasing.database import leasing_transaction
from shepherd_rm.leasing.models import LeaseCreate, LeasePage, LeaseRenew, LeaseResponse
from shepherd_rm.leasing.persistence import (
    acquire_lease,
    end_lease,
    fetch_lease,
    list_leases,
    renew_lease,
)


def build_leasing_router(settings: Settings) -> APIRouter:
    """Build the lease router under the versioned public API prefix."""
    dependencies = IdentityDependencies(settings)
    router = APIRouter(prefix="/v1/leases", tags=["Leases"])

    @router.post("", response_model=LeaseResponse, status_code=201, operation_id="acquireLease")
    async def acquire(
        request: LeaseCreate,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> LeaseResponse:
        async with leasing_transaction(settings) as connection:
            return await acquire_lease(
                connection,
                request,
                idempotency_key,
                principal.principal.id,
                principal.principal.role == "Admin",
            )

    @router.get("", response_model=LeasePage, operation_id="listLeases")
    async def list_authorized(
        resource_id: uuid.UUID | None = None,
        state: Literal["Active", "Released", "Expired", "Revoked"] | None = None,
        acquired_by: uuid.UUID | None = None,
        consumer: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        acquired_after: datetime | None = None,
        acquired_before: datetime | None = None,
        expires_after: datetime | None = None,
        expires_before: datetime | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> LeasePage:
        async with leasing_transaction(settings) as connection:
            return await list_leases(
                connection,
                principal_id=principal.principal.id,
                administrator=principal.principal.role == "Admin",
                resource_id=resource_id,
                state=state,
                acquired_by=acquired_by,
                consumer=consumer,
                acquired_after=acquired_after,
                acquired_before=acquired_before,
                expires_after=expires_after,
                expires_before=expires_before,
                offset=offset,
                limit=limit,
            )

    @router.get("/{lease_id}", response_model=LeaseResponse, operation_id="getLease")
    async def get(
        lease_id: uuid.UUID,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> LeaseResponse:
        async with leasing_transaction(settings) as connection:
            return await fetch_lease(
                connection,
                lease_id,
                principal.principal.id,
                principal.principal.role == "Admin",
            )

    @router.post("/{lease_id}/renew", response_model=LeaseResponse, operation_id="renewLease")
    async def renew(
        lease_id: uuid.UUID,
        request: LeaseRenew,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> LeaseResponse:
        async with leasing_transaction(settings) as connection:
            return await renew_lease(
                connection,
                lease_id,
                request,
                principal.principal.id,
            )

    @router.post("/{lease_id}/release", response_model=LeaseResponse, operation_id="releaseLease")
    async def release(
        lease_id: uuid.UUID,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> LeaseResponse:
        async with leasing_transaction(settings) as connection:
            return await end_lease(
                connection,
                lease_id,
                principal.principal.id,
                "Released",
            )

    @router.post("/{lease_id}/revoke", response_model=LeaseResponse, operation_id="revokeLease")
    async def revoke(
        lease_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> LeaseResponse:
        if admin.principal.role != "Admin":
            raise HTTPException(status_code=403, detail="Administrator permission is required")
        async with leasing_transaction(settings) as connection:
            return await end_lease(connection, lease_id, admin.principal.id, "Revoked")

    return router
