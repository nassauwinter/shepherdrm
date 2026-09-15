"""Expose administrator management and explicitly authorized secret access."""

from __future__ import annotations

import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError

from shepherd_rm.config import Settings
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticatedPrincipal
from shepherd_rm.rate_limits import (
    RateLimitExceeded,
    enforce_rate_limit,
    principal_subject,
)
from shepherd_rm.resource_secrets.crypto import (
    SecretCipher,
    SecretDecryptionError,
    SecretEncryptionUnavailable,
)
from shepherd_rm.resource_secrets.database import resource_secret_transaction
from shepherd_rm.resource_secrets.models import (
    ResourceSecretCreate,
    ResourceSecretMetadata,
    ResourceSecretUpdate,
    SecretAccess,
)
from shepherd_rm.resource_secrets.persistence import (
    access_lease_secret,
    access_resource_secret_as_admin,
    create_resource_secret,
    delete_resource_secret,
    get_resource_secret,
    list_lease_secrets,
    list_resource_secrets,
    update_resource_secret,
)


def _raise_safe_crypto_problem(error: Exception) -> NoReturn:
    """Translate configuration and stored-data failures without exposing material."""
    if isinstance(error, SecretEncryptionUnavailable):
        raise HTTPException(status_code=503, detail=str(error)) from None
    raise HTTPException(status_code=500, detail="Managed secret could not be accessed") from None


def _prevent_caching(response: Response) -> None:
    """Mark secret-bearing responses as unsuitable for client or intermediary caches."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _enforce_secret_access_limit(
    settings: Settings, principal: AuthenticatedPrincipal
) -> None:
    """Apply one shared limit to explicit secret access by a principal."""
    try:
        await enforce_rate_limit(
            settings,
            "secret_access",
            principal_subject(principal.principal.id),
            settings.secret_access_rate_limit_attempts,
            settings.secret_access_rate_limit_window_seconds,
        )
    except RateLimitExceeded as error:
        raise HTTPException(
            status_code=429,
            detail="Too many secret access attempts",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from None


def build_resource_secrets_router(settings: Settings) -> APIRouter:
    """Build resource-secret routes under their resource and lease parents."""
    dependencies = IdentityDependencies(settings)
    cipher = SecretCipher(settings)
    router = APIRouter(tags=["Resource secrets"])

    @router.post(
        "/v1/resources/{resource_id}/secrets",
        response_model=ResourceSecretMetadata,
        status_code=201,
        operation_id="createResourceSecret",
    )
    async def create(
        resource_id: uuid.UUID,
        request: ResourceSecretCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceSecretMetadata:
        try:
            async with resource_secret_transaction(settings) as connection:
                return await create_resource_secret(
                    connection, cipher, resource_id, request, admin.principal.id
                )
        except (SecretEncryptionUnavailable, SecretDecryptionError) as error:
            _raise_safe_crypto_problem(error)
        except IntegrityError:
            raise HTTPException(
                status_code=409, detail="Resource secret name already exists"
            ) from None

    @router.get(
        "/v1/resources/{resource_id}/secrets",
        response_model=list[ResourceSecretMetadata],
        operation_id="listResourceSecrets",
    )
    async def list_for_resource(
        resource_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> list[ResourceSecretMetadata]:
        async with resource_secret_transaction(settings) as connection:
            return await list_resource_secrets(connection, resource_id)

    @router.get(
        "/v1/resources/{resource_id}/secrets/{secret_id}",
        response_model=ResourceSecretMetadata,
        operation_id="getResourceSecret",
    )
    async def get(
        resource_id: uuid.UUID,
        secret_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceSecretMetadata:
        async with resource_secret_transaction(settings) as connection:
            return await get_resource_secret(connection, resource_id, secret_id)

    @router.patch(
        "/v1/resources/{resource_id}/secrets/{secret_id}",
        response_model=ResourceSecretMetadata,
        operation_id="updateResourceSecret",
    )
    async def update(
        resource_id: uuid.UUID,
        secret_id: uuid.UUID,
        request: ResourceSecretUpdate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceSecretMetadata:
        try:
            async with resource_secret_transaction(settings) as connection:
                return await update_resource_secret(
                    connection, cipher, resource_id, secret_id, request, admin.principal.id
                )
        except (SecretEncryptionUnavailable, SecretDecryptionError) as error:
            _raise_safe_crypto_problem(error)
        except IntegrityError:
            raise HTTPException(
                status_code=409, detail="Resource secret name already exists"
            ) from None

    @router.delete(
        "/v1/resources/{resource_id}/secrets/{secret_id}",
        status_code=204,
        operation_id="deleteResourceSecret",
    )
    async def remove(
        resource_id: uuid.UUID,
        secret_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with resource_secret_transaction(settings) as connection:
            await delete_resource_secret(connection, resource_id, secret_id, admin.principal.id)
        return Response(status_code=204)

    @router.post(
        "/v1/resources/{resource_id}/secrets/{secret_id}/access",
        response_model=SecretAccess,
        operation_id="accessResourceSecret",
    )
    async def access_as_admin(
        resource_id: uuid.UUID,
        secret_id: uuid.UUID,
        response: Response,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> SecretAccess:
        await _enforce_secret_access_limit(settings, admin)
        try:
            async with resource_secret_transaction(settings) as connection:
                result = await access_resource_secret_as_admin(
                    connection, cipher, resource_id, secret_id, admin.principal.id
                )
        except (SecretEncryptionUnavailable, SecretDecryptionError) as error:
            _raise_safe_crypto_problem(error)
        _prevent_caching(response)
        return result

    @router.get(
        "/v1/leases/{lease_id}/secrets",
        response_model=list[ResourceSecretMetadata],
        operation_id="listLeaseSecrets",
    )
    async def list_for_lease(
        lease_id: uuid.UUID,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> list[ResourceSecretMetadata]:
        async with resource_secret_transaction(settings) as connection:
            return await list_lease_secrets(
                connection,
                lease_id,
                principal.principal.id,
                principal.principal.role == "Admin",
            )

    @router.post(
        "/v1/leases/{lease_id}/secrets/{secret_id}/access",
        response_model=SecretAccess,
        operation_id="accessLeaseSecret",
    )
    async def access_through_lease(
        lease_id: uuid.UUID,
        secret_id: uuid.UUID,
        response: Response,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> SecretAccess:
        await _enforce_secret_access_limit(settings, principal)
        try:
            async with resource_secret_transaction(settings) as connection:
                result = await access_lease_secret(
                    connection,
                    cipher,
                    lease_id,
                    secret_id,
                    principal.principal.id,
                    principal.principal.role == "Admin",
                )
        except (SecretEncryptionUnavailable, SecretDecryptionError) as error:
            _raise_safe_crypto_problem(error)
        _prevent_caching(response)
        return result

    return router
