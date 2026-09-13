"""Expose the resource catalog feature as an internal application module."""

from shepherd_rm.catalog.api import build_catalog_router

__all__ = ["build_catalog_router"]
