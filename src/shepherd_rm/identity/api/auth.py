"""Expose local username and password authentication endpoints."""

from fastapi import APIRouter, HTTPException

from shepherd_rm.identity.api.dependencies import IdentityDependencies
from shepherd_rm.identity.authentication import AuthenticationError, login
from shepherd_rm.identity.models import LoginRequest, LoginResponse
from shepherd_rm.rate_limits import RateLimitExceeded, enforce_rate_limit, login_subject


def build_auth_router(dependencies: IdentityDependencies) -> APIRouter:
    """Build the public login router."""
    router = APIRouter(tags=["Identity"])

    @router.post("/auth/login", response_model=LoginResponse, operation_id="login")
    async def login_route(request: LoginRequest) -> LoginResponse:
        try:
            await enforce_rate_limit(
                dependencies.settings,
                "login",
                login_subject(request.username),
                dependencies.settings.login_rate_limit_attempts,
                dependencies.settings.login_rate_limit_window_seconds,
            )
            return await login(dependencies.settings, request.username, request.password)
        except RateLimitExceeded as error:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts",
                headers={"Retry-After": str(error.retry_after_seconds)},
            ) from None
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="Invalid username or password") from None

    return router
