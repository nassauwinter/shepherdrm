"""Implement password verification and revocable bearer-token authentication."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from shepherd_rm.audit import record_audit_event
from shepherd_rm.config import Settings
from shepherd_rm.database_models import ApiToken, PasswordCredential, Principal
from shepherd_rm.identity.database import identity_transaction
from shepherd_rm.identity.models import LoginResponse, PrincipalResponse, TokenIssued, TokenResponse

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-password")


class AuthenticationError(Exception):
    """Indicate that supplied authentication material is invalid."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Associate an authenticated principal with the bearer token used."""

    principal: PrincipalResponse
    token_id: uuid.UUID


def hash_password(password: str) -> str:
    """Create an Argon2id password hash for synchronous setup code."""
    return PASSWORD_HASHER.hash(password)


async def hash_password_async(password: str) -> str:
    """Create an Argon2id password hash without blocking the event loop."""
    return await asyncio.to_thread(hash_password, password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password without exposing mismatch details."""
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except VerificationError:
        return False


def token_hash(token: str) -> str:
    """Create the deterministic lookup digest for a high-entropy token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(
    connection: AsyncConnection,
    principal_id: uuid.UUID,
    name: str,
    expires_at: datetime | None,
) -> TokenIssued:
    """Persist a token digest and return its plaintext value exactly once."""
    token_id = uuid.uuid4()
    token = f"srm_{secrets.token_urlsafe(32)}"
    result = await connection.execute(
        insert(ApiToken)
        .values(
            id=token_id,
            principal_id=principal_id,
            name=name,
            token_hash=token_hash(token),
            expires_at=expires_at,
        )
        .returning(ApiToken.created_at)
    )
    return TokenIssued(
        id=token_id,
        name=name,
        token=token,
        created_at=result.scalar_one(),
        expires_at=expires_at,
        last_used_at=None,
        revoked_at=None,
    )


async def authenticate_token(settings: Settings, token: str) -> AuthenticatedPrincipal:
    """Resolve an active bearer token and record its latest use."""
    async with identity_transaction(settings) as connection:
        result = await connection.execute(
            select(
                Principal.__table__,
                ApiToken.id.label("token_id"),
            )
            .join(ApiToken, ApiToken.principal_id == Principal.id)
            .where(
                ApiToken.token_hash == token_hash(token),
                ApiToken.revoked_at.is_(None),
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > func.now()),
                Principal.archived_at.is_(None),
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise AuthenticationError
        await connection.execute(
            update(ApiToken).where(ApiToken.id == row["token_id"]).values(last_used_at=func.now())
        )
    principal_data = {column.name: row[column.name] for column in Principal.__table__.columns}
    return AuthenticatedPrincipal(
        principal=PrincipalResponse.model_validate(principal_data),
        token_id=row["token_id"],
    )


async def login(settings: Settings, username: str, password: str) -> LoginResponse:
    """Exchange valid local credentials for a short-lived bearer token."""
    async with identity_transaction(settings) as connection:
        result = await connection.execute(
            select(Principal.id, PasswordCredential.password_hash)
            .join(PasswordCredential, PasswordCredential.principal_id == Principal.id)
            .where(
                Principal.name == username,
                Principal.kind == "User",
                Principal.archived_at.is_(None),
            )
            .with_for_update(of=Principal)
        )
        row = result.one_or_none()
        password_hash = row.password_hash if row is not None else DUMMY_PASSWORD_HASH
        password_valid = await asyncio.to_thread(verify_password, password_hash, password)
        if row is None or not password_valid:
            raise AuthenticationError
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.login_token_ttl_seconds)
        issued = await issue_token(connection, row.id, "login", expires_at)
        await record_audit_event(connection, row.id, "authentication.login", "ApiToken", issued.id)
    return LoginResponse(access_token=issued.token, expires_at=expires_at)


async def list_tokens(connection: AsyncConnection, principal_id: uuid.UUID) -> list[TokenResponse]:
    """List token metadata without returning secret token values."""
    result = await connection.execute(
        select(
            ApiToken.id,
            ApiToken.name,
            ApiToken.created_at,
            ApiToken.expires_at,
            ApiToken.last_used_at,
            ApiToken.revoked_at,
        )
        .where(ApiToken.principal_id == principal_id)
        .order_by(ApiToken.created_at, ApiToken.id)
    )
    return [TokenResponse.model_validate(dict(row)) for row in result.mappings()]
