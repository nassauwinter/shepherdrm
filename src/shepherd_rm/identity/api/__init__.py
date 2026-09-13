"""Assemble the identity feature's subfeature routers."""

from fastapi import APIRouter

from shepherd_rm.config import Settings
from shepherd_rm.identity.api.auth import build_auth_router
from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.api.groups import build_groups_router
from shepherd_rm.identity.api.me import build_me_router
from shepherd_rm.identity.api.service_identities import build_service_identities_router
from shepherd_rm.identity.api.users import build_users_router


def build_identity_router(settings: Settings) -> APIRouter:
    """Combine all identity subfeature routers under the versioned API prefix."""
    dependencies = IdentityDependencies(settings)
    router = APIRouter(prefix="/v1")
    router.include_router(build_auth_router(dependencies))
    router.include_router(build_me_router(dependencies))
    router.include_router(build_users_router(dependencies))
    router.include_router(build_service_identities_router(dependencies))
    router.include_router(build_groups_router(dependencies))
    return router
