"""Expose authenticated resource discovery and administrator catalog operations."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from shepherd_rm.catalog.database import catalog_transaction
from shepherd_rm.catalog.models import (
    ResourceAccess,
    ResourceCreate,
    ResourcePage,
    ResourceResponse,
    ResourceUpdate,
)
from shepherd_rm.catalog.persistence import (
    archive_resource,
    create_resource,
    fetch_resource,
    fetch_resource_access,
    fetch_visible_resource,
    list_resources,
    set_resource_grant,
    transition_resource,
    update_resource,
)
from shepherd_rm.config import Settings
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticatedPrincipal


def parse_labels(values: list[str]) -> dict[str, str]:
    """Parse repeated key=value query parameters into an exact label selector."""
    labels: dict[str, str] = {}
    for value in values:
        key, separator, label_value = value.partition("=")
        if not separator or not key:
            raise HTTPException(status_code=400, detail="Labels must use key=value syntax")
        if key in labels and labels[key] != label_value:
            raise HTTPException(status_code=400, detail=f"Label {key!r} has conflicting values")
        labels[key] = label_value
    return labels


def build_catalog_router(settings: Settings) -> APIRouter:
    """Build the resource catalog router under the versioned API prefix."""
    dependencies = IdentityDependencies(settings)
    router = APIRouter(prefix="/v1/resources", tags=["Resources"])

    @router.post(
        "", response_model=ResourceResponse, status_code=201, operation_id="createResource"
    )
    async def create(
        request: ResourceCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceResponse:
        try:
            async with catalog_transaction(settings) as connection:
                return await create_resource(connection, request, admin.principal.id)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Resource name already exists") from None

    @router.get("", response_model=ResourcePage, operation_id="listResources")
    async def list_catalog(
        resource_type: Annotated[
            str | None, Query(alias="type", min_length=1, max_length=255)
        ] = None,
        sharing_mode: Literal["Exclusive", "Shared"] | None = None,
        operational_status: Literal["Active", "Cleaning", "Quarantined", "Disabled"] | None = None,
        available: bool | None = None,
        label: Annotated[list[str], Query()] = [],
        include_archived: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> ResourcePage:
        if include_archived and principal.principal.role != "Admin":
            raise HTTPException(
                status_code=403, detail="Only administrators can list archived resources"
            )
        async with catalog_transaction(settings) as connection:
            return await list_resources(
                connection,
                resource_type=resource_type,
                sharing_mode=sharing_mode,
                operational_status=operational_status,
                available=available,
                labels=parse_labels(label),
                include_archived=include_archived,
                principal_id=principal.principal.id,
                administrator=principal.principal.role == "Admin",
                offset=offset,
                limit=limit,
            )

    @router.get("/{resource_id}", response_model=ResourceResponse, operation_id="getResource")
    async def get(
        resource_id: uuid.UUID,
        principal: AuthenticatedPrincipal = Depends(dependencies.authenticated),
    ) -> ResourceResponse:
        async with catalog_transaction(settings) as connection:
            if principal.principal.role == "Admin":
                return await fetch_resource(connection, resource_id)
            return await fetch_visible_resource(connection, resource_id, principal.principal.id)

    @router.patch("/{resource_id}", response_model=ResourceResponse, operation_id="updateResource")
    async def update_catalog(
        resource_id: uuid.UUID,
        request: ResourceUpdate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceResponse:
        try:
            async with catalog_transaction(settings) as connection:
                return await update_resource(connection, resource_id, request, admin.principal.id)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Resource name already exists") from None

    @router.delete("/{resource_id}", status_code=204, operation_id="archiveResource")
    async def archive(
        resource_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with catalog_transaction(settings) as connection:
            await archive_resource(connection, resource_id, admin.principal.id)
        return Response(status_code=204)

    def add_transition(
        path: str,
        transition: Literal["disable", "enable", "quarantine", "recover"],
        operation_id: str,
    ) -> None:
        async def endpoint(
            resource_id: uuid.UUID,
            admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
        ) -> ResourceResponse:
            async with catalog_transaction(settings) as connection:
                return await transition_resource(
                    connection, resource_id, transition, admin.principal.id
                )

        router.add_api_route(
            f"/{{resource_id}}/{path}",
            endpoint,
            methods=["POST"],
            response_model=ResourceResponse,
            operation_id=operation_id,
        )

    add_transition("disable", "disable", "disableResource")
    add_transition("enable", "enable", "enableResource")
    add_transition("quarantine", "quarantine", "quarantineResource")
    add_transition("recover", "recover", "recoverResource")

    @router.get(
        "/{resource_id}/access",
        response_model=ResourceAccess,
        operation_id="getResourceAccess",
    )
    async def get_access(
        resource_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> ResourceAccess:
        async with catalog_transaction(settings) as connection:
            return await fetch_resource_access(connection, resource_id)

    def add_grant_route(
        target_path: Literal["principals", "groups"],
        target_type: Literal["Principal", "Group"],
        granted: bool,
        operation_id: str,
    ) -> None:
        async def endpoint(
            resource_id: uuid.UUID,
            target_id: uuid.UUID,
            admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
        ) -> Response:
            async with catalog_transaction(settings) as connection:
                await set_resource_grant(
                    connection,
                    resource_id,
                    target_id,
                    target_type,
                    granted,
                    admin.principal.id,
                )
            return Response(status_code=204)

        router.add_api_route(
            f"/{{resource_id}}/access/{target_path}/{{target_id}}",
            endpoint,
            methods=["PUT" if granted else "DELETE"],
            status_code=204,
            operation_id=operation_id,
        )

    add_grant_route("principals", "Principal", True, "grantResourcePrincipalAccess")
    add_grant_route("principals", "Principal", False, "revokeResourcePrincipalAccess")
    add_grant_route("groups", "Group", True, "grantResourceGroupAccess")
    add_grant_route("groups", "Group", False, "revokeResourceGroupAccess")
    return router
