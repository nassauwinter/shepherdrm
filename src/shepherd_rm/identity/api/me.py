"""Expose current-principal, password, and personal-token endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, update

from shepherd_rm.audit import record_audit_event
from shepherd_rm.database_models import ApiToken, PasswordCredential
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import (
    AuthenticatedPrincipal,
    hash_password_async,
    issue_token,
    list_tokens,
)
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import (
    PasswordRequest,
    PrincipalResponse,
    TokenCreate,
    TokenIssued,
    TokenResponse,
)


def build_me_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build endpoints operated by the authenticated principal on itself."""
    router = APIRouter(tags=["Identity"])

    @router.get("/me", response_model=PrincipalResponse, operation_id="getMe")
    async def get_me(
        current: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> PrincipalResponse:
        return current.principal

    @router.put("/me/password", status_code=204, operation_id="changeMyPassword")
    async def change_my_password(
        request: PasswordRequest,
        current: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> Response:
        if current.principal.kind != "User":
            raise HTTPException(status_code=400, detail="Service identities do not have passwords")
        async with identity_transaction(dependencies.settings) as connection:
            await connection.execute(
                update(PasswordCredential)
                .where(PasswordCredential.principal_id == current.principal.id)
                .values(
                    password_hash=await hash_password_async(request.password),
                    updated_at=func.now(),
                )
            )
            await record_audit_event(
                connection,
                current.principal.id,
                "password.changed",
                "Principal",
                current.principal.id,
            )
        return Response(status_code=204)

    @router.post(
        "/me/api-tokens", response_model=TokenIssued, status_code=201, operation_id="createMyToken"
    )
    async def create_my_token(
        request: TokenCreate,
        current: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> TokenIssued:
        if request.expires_at is not None and request.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=400, detail="Token expiration must be in the future")
        async with identity_transaction(dependencies.settings) as connection:
            issued = await issue_token(
                connection, current.principal.id, request.name, request.expires_at
            )
            await record_audit_event(
                connection, current.principal.id, "api_token.created", "ApiToken", issued.id
            )
            return issued

    @router.get("/me/api-tokens", response_model=list[TokenResponse], operation_id="listMyTokens")
    async def list_my_tokens(
        current: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> list[TokenResponse]:
        async with identity_transaction(dependencies.settings) as connection:
            return await list_tokens(connection, current.principal.id)

    @router.delete("/me/api-tokens/{token_id}", status_code=204, operation_id="revokeMyToken")
    async def revoke_my_token(
        token_id: uuid.UUID,
        current: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            result = await connection.execute(
                update(ApiToken)
                .where(ApiToken.id == token_id, ApiToken.principal_id == current.principal.id)
                .values(revoked_at=func.coalesce(ApiToken.revoked_at, func.now()))
                .returning(ApiToken.id)
            )
            if result.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="Token not found")
            await record_audit_event(
                connection, current.principal.id, "api_token.revoked", "ApiToken", token_id
            )
        return Response(status_code=204)

    return router
