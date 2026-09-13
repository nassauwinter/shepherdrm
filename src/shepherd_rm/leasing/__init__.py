"""Expose the public lease API and background expiration entry points."""

from shepherd_rm.leasing.api import build_leasing_router

__all__ = ["build_leasing_router"]
