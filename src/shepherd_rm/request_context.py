"""Propagate request correlation data through asynchronous call contexts."""

from contextvars import ContextVar, Token
from uuid import uuid4

CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="unknown")


def select_correlation_id(supplied_correlation_id: str | None) -> str:
    """Preserve a safe caller identifier or generate a new correlation ID."""
    if (
        supplied_correlation_id
        and len(supplied_correlation_id) <= 128
        and all(character.isalnum() or character in "-_." for character in supplied_correlation_id)
    ):
        return supplied_correlation_id
    return str(uuid4())


def set_correlation_id(correlation_id: str) -> Token[str]:
    return CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str]) -> None:
    CORRELATION_ID.reset(token)


def get_correlation_id() -> str:
    return CORRELATION_ID.get()
