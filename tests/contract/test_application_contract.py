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
    return {
        (route.path, method, route.operation_id)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_application_operations_match_contract() -> None:
    """FastAPI registers exactly the paths, methods, and operation IDs in the contract."""
    contract = load_openapi_contract(get_settings().openapi_path)
    assert application_operations() == contract_operations(contract)
