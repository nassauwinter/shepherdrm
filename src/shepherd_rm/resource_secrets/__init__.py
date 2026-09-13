"""Expose resource-secret administration and authorized access routes."""

from shepherd_rm.resource_secrets.api import build_resource_secrets_router

__all__ = ["build_resource_secrets_router"]
