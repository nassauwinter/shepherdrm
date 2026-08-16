"""Expose administrator-managed groups and user memberships."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from shepherd_rm.audit import record_audit_event
from shepherd_rm.database_models import GroupMembership
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticatedPrincipal
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import GroupCreate, GroupResponse, GroupUpdate
from shepherd_rm.identity.persistence import (
    archive_group,
    create_group,
    fetch_group,
    fetch_principal,
    list_groups,
    update_group,
)


def build_groups_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build administrator-only group and membership endpoints."""
    router = APIRouter(prefix="/groups", tags=["Identity"])

    @router.post("", response_model=GroupResponse, status_code=201, operation_id="createGroup")
    async def create_group_route(
        request: GroupCreate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> GroupResponse:
        try:
            async with identity_transaction(dependencies.settings) as connection:
                return await create_group(connection, request, admin.principal.id)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Group name already exists") from None

    @router.get("", response_model=list[GroupResponse], operation_id="listGroups")
    async def list_groups_route(
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> list[GroupResponse]:
        async with identity_transaction(dependencies.settings) as connection:
            return await list_groups(connection)

    @router.get("/{group_id}", response_model=GroupResponse, operation_id="getGroup")
    async def get_group(
        group_id: uuid.UUID,
        _admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> GroupResponse:
        async with identity_transaction(dependencies.settings) as connection:
            return await fetch_group(connection, group_id)

    @router.patch("/{group_id}", response_model=GroupResponse, operation_id="updateGroup")
    async def update_group_route(
        group_id: uuid.UUID,
        request: GroupUpdate,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> GroupResponse:
        async with identity_transaction(dependencies.settings) as connection:
            return await update_group(connection, group_id, request, admin.principal.id)

    @router.delete("/{group_id}", status_code=204, operation_id="archiveGroup")
    async def archive_group_route(
        group_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            await archive_group(connection, group_id, admin.principal.id)
        return Response(status_code=204)

    @router.put("/{group_id}/members/{user_id}", status_code=204, operation_id="addGroupMember")
    async def add_group_member(
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        try:
            async with identity_transaction(dependencies.settings) as connection:
                principal = await fetch_principal(connection, user_id, "User")
                if principal.archived_at is not None:
                    raise HTTPException(status_code=409, detail="Archived users cannot join groups")
                await connection.execute(
                    insert(GroupMembership)
                    .values(group_id=group_id, principal_id=user_id)
                    .on_conflict_do_nothing()
                )
                await record_audit_event(
                    connection, admin.principal.id, "group.member_added", "Group", group_id
                )
        except IntegrityError:
            raise HTTPException(status_code=404, detail="Group not found") from None
        return Response(status_code=204)

    @router.delete(
        "/{group_id}/members/{user_id}",
        status_code=204,
        operation_id="removeGroupMember",
    )
    async def remove_group_member(
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        admin: AuthenticatedPrincipal = Depends(dependencies.administrator),
    ) -> Response:
        async with identity_transaction(dependencies.settings) as connection:
            await connection.execute(
                delete(GroupMembership).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.principal_id == user_id,
                )
            )
            await record_audit_event(
                connection, admin.principal.id, "group.member_removed", "Group", group_id
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
