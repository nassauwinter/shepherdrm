"""Expose local username and password authentication endpoints."""

from fastapi import APIRouter, HTTPException

from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticationError, login
from shepherd_rm.identity.models import LoginRequest, LoginResponse


def build_auth_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build the public login router."""
    router = APIRouter(tags=["Identity"])

    @router.post("/auth/login", response_model=LoginResponse, operation_id="login")
    async def login_route(request: LoginRequest) -> LoginResponse:
        try:
            return await login(dependencies.settings, request.username, request.password)
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="Invalid username or password") from None

    return router
