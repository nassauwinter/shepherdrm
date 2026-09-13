"""Propagate request correlation data through asynchronous call contexts."""

from contextvars import ContextVar, Token

CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="unknown")


def set_correlation_id(correlation_id: str) -> Token[str]:
    return CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str]) -> None:
    CORRELATION_ID.reset(token)


def get_correlation_id() -> str:
    return CORRELATION_ID.get()
