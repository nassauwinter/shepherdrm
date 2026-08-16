from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate

CONTRACT_PATH = Path(__file__).parents[2] / "openapi" / "openapi.yaml"


def load_contract() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contract = yaml.safe_load(contract_file)
    assert isinstance(contract, dict)
    return contract


def test_openapi_contract_is_valid() -> None:
    """The committed OpenAPI document conforms to the OpenAPI specification."""
    validate(load_contract())


def test_operation_ids_are_unique() -> None:
    """Every described API operation has a distinct operation identifier."""
    contract = load_contract()
    operation_ids = [
        operation["operationId"]
        for path_item in contract["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert len(operation_ids) == len(set(operation_ids))
