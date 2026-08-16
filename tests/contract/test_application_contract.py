"""Ensure runtime route registration matches the authoritative OpenAPI contract."""

from collections.abc import Iterable
from typing import Any

from fastapi.routing import APIRoute

from shepherd_rm.application import create_app
from shepherd_rm.config import get_settings
from shepherd_rm.contract import load_openapi_contract

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}


def contract_operations(contract: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (path, method.upper(), operation["operationId"])
        for path, path_item in contract["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    }


def application_operations() -> set[tuple[str, str, str]]:
    app = create_app()

    def included_routes(
        routes: Iterable[object], prefix: str = ""
    ) -> Iterable[tuple[str, APIRoute]]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield prefix, route
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                include_context = getattr(route, "include_context", None)
                nested_prefix = prefix + getattr(include_context, "prefix", "")
                yield from included_routes(original_router.routes, nested_prefix)

    return {
        (prefix + route.path, method, route.operation_id)
        for prefix, route in included_routes(app.routes)
        for method in route.methods
    }


def test_application_operations_match_contract() -> None:
    """FastAPI registers exactly the paths, methods, and operation IDs in the contract."""
    contract = load_openapi_contract(get_settings().openapi_path)
    assert application_operations() == contract_operations(contract)
