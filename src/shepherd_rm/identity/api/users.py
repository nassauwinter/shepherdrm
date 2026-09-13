"""Expose administrator-managed human-user endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from shepherd_rm.audit import record_audit_event
from shepherd_rm.database_models import PasswordCredential, Principal
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticatedPrincipal, hash_password_async
from shepherd_rm.identity.constants import ADMINISTRATION_LOCK_ID
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import (
    PasswordRequest,
    PrincipalCreate,
    PrincipalResponse,
    PrincipalUpdate,
)
from shepherd_rm.identity.persistence import (
    archive_principal,
    create_principal,
    fetch_principal,
    list_principals,
    update_principal,
)


def build_users_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build administrator-only user-management endpoints."""
    router = APIRouter(prefix="/users", tags=["Identity"])

    @router.post("", response_model=PrincipalResponse, status_code=201, operation_id="createUser")
    async def create_user(
        request: PrincipalCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        try:
            async with identity_transaction(dependencies.settings) as connection:
                return await create_principal(connection, request, "User", admin.principal.id)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Principal name already exists") from None

    @router.get("", response_model=list[PrincipalResponse], operation_id="listUsers")
    async def list_users(
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> list[PrincipalResponse]:
        async with identity_transaction(dependencies.settings) as connection:
            return await list_principals(connection, "User")

    @router.get("/{user_id}", response_model=PrincipalResponse, operation_id="getUser")
    async def get_user(
        user_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        async with identity_transaction(dependencies.settings) as connection:
            return await fetch_principal(connection, user_id, "User")

    @router.patch("/{user_id}", response_model=PrincipalResponse, operation_id="updateUser")
    async def update_user(
        user_id: uuid.UUID,
        request: PrincipalUpdate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> PrincipalResponse:
        async with identity_transaction(dependencies.settings) as connection:
            if request.role == "User":
                await connection.execute(select(func.pg_advisory_xact_lock(ADMINISTRATION_LOCK_ID)))
                administrators = (
                    (
                        await connection.execute(
                            select(Principal.id)
                            .join(
                                PasswordCredential,
                                PasswordCredential.principal_id == Principal.id,
                            )
                            .where(
                                Principal.kind == "User",
                                Principal.role == "Admin",
                                Principal.archived_at.is_(None),
                            )
                            .with_for_update(of=Principal)
                        )
                    )
                    .scalars()
                    .all()
                )
                if administrators == [user_id]:
                    raise HTTPException(
                        status_code=409,
                        detail="The final active administrator cannot be demoted",
                    )
            return await update_principal(connection, user_id, request, "User", admin.principal.id)

    @router.delete("/{user_id}", status_code=204, operation_id="archiveUser")
    async def archive_user(
        user_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            await archive_principal(connection, user_id, "User", admin.principal.id)
        return Response(status_code=204)

    @router.put("/{user_id}/password", status_code=204, operation_id="resetUserPassword")
    async def reset_user_password(
        user_id: uuid.UUID,
        request: PasswordRequest,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            result = await connection.execute(
                update(PasswordCredential)
                .where(PasswordCredential.principal_id == user_id)
                .values(
                    password_hash=await hash_password_async(request.password),
                    updated_at=func.now(),
                )
                .returning(PasswordCredential.principal_id)
            )
            if result.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="User not found")
            await record_audit_event(
                connection, admin.principal.id, "password.reset", "Principal", user_id
            )
        return Response(status_code=204)

    return router
