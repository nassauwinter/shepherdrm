"""Expose administrator-managed service identities and their API tokens."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from shepherd_rm.audit import record_audit_event
from shepherd_rm.database_models import ApiToken
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import (
    AuthenticatedPrincipal,
    issue_token,
    list_tokens,
)
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import (
    PrincipalCreate,
    PrincipalResponse,
    PrincipalUpdate,
    TokenCreate,
    TokenIssued,
    TokenResponse,
)
from shepherd_rm.identity.persistence import (
    archive_principal,
    create_principal,
    fetch_principal,
    list_principals,
    update_principal,
)


def build_service_identities_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build administrator-only service-identity and token endpoints."""
    router = APIRouter(prefix="/service-identities", tags=["Identity"])

    @router.post(
        "", response_model=PrincipalResponse, status_code=201, operation_id="createServiceIdentity"
    )
    async def create_service(
        request: PrincipalCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        try:
            async with identity_transaction(dependencies.settings) as connection:
                return await create_principal(connection, request, "Service", admin.principal.id)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Principal name already exists") from None

    @router.get("", response_model=list[PrincipalResponse], operation_id="listServiceIdentities")
    async def list_services(
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> list[PrincipalResponse]:
        async with identity_transaction(dependencies.settings) as connection:
            return await list_principals(connection, "Service")

    @router.get(
        "/{identity_id}", response_model=PrincipalResponse, operation_id="getServiceIdentity"
    )
    async def get_service(
        identity_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        async with identity_transaction(dependencies.settings) as connection:
            return await fetch_principal(connection, identity_id, "Service")

    @router.patch(
        "/{identity_id}",
        response_model=PrincipalResponse,
        operation_id="updateServiceIdentity",
    )
    async def update_service(
        identity_id: uuid.UUID,
        request: PrincipalUpdate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        async with identity_transaction(dependencies.settings) as connection:
            return await update_principal(
                connection, identity_id, request, "Service", admin.principal.id
            )

    @router.delete("/{identity_id}", status_code=204, operation_id="archiveServiceIdentity")
    async def archive_service(
        identity_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            await archive_principal(connection, identity_id, "Service", admin.principal.id)
        return Response(status_code=204)

    @router.post(
        "/{identity_id}/api-tokens",
        response_model=TokenIssued,
        status_code=201,
        operation_id="createServiceToken",
    )
    async def create_service_token(
        identity_id: uuid.UUID,
        request: TokenCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> TokenIssued:
        if request.expires_at is not None and request.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=400, detail="Token expiration must be in the future")
        async with identity_transaction(dependencies.settings) as connection:
            await fetch_principal(connection, identity_id, "Service")
            issued = await issue_token(connection, identity_id, request.name, request.expires_at)
            await record_audit_event(
                connection, admin.principal.id, "api_token.created", "ApiToken", issued.id
            )
            return issued

    @router.get(
        "/{identity_id}/api-tokens",
        response_model=list[TokenResponse],
        operation_id="listServiceTokens",
    )
    async def list_service_tokens(
        identity_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> list[TokenResponse]:
        async with identity_transaction(dependencies.settings) as connection:
            await fetch_principal(connection, identity_id, "Service")
            return await list_tokens(connection, identity_id)

    @router.delete(
        "/{identity_id}/api-tokens/{token_id}",
        status_code=204,
        operation_id="revokeServiceToken",
    )
    async def revoke_service_token(
        identity_id: uuid.UUID,
        token_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            result = await connection.execute(
                update(ApiToken)
                .where(ApiToken.id == token_id, ApiToken.principal_id == identity_id)
                .values(revoked_at=func.coalesce(ApiToken.revoked_at, func.now()))
                .returning(ApiToken.id)
            )
            if result.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="Token not found")
            await record_audit_event(
                connection, admin.principal.id, "api_token.revoked", "ApiToken", token_id
            )
        return Response(status_code=204)

    return router
