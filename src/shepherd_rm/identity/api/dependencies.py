"""Provide reusable authentication and authorization dependencies for identity routes."""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shepherd_rm.config import Settings
from shepherd_rm.identity.authentication import (
    AuthenticatedPrincipal,
    AuthenticationError,
    authenticate_token,
)

BEARER = HTTPBearer(auto_error=False)


class IdentityDependencies:
    """Bind request-security dependencies to one application configuration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def authenticated(
        self,
        credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
    ) -> AuthenticatedPrincipal:
        """Return the active bearer principal or reject the request."""
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            return await authenticate_token(self.settings, credentials.credentials)
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="Invalid bearer token") from None

    async def administrator(
        self,
        credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
    ) -> AuthenticatedPrincipal:
        """Return an authenticated administrator or reject the request."""
        current = await self.authenticated(credentials)
        if current.principal.role != "Admin":
            raise HTTPException(status_code=403, detail="Administrator permission required")
        return current
