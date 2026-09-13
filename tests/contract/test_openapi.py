"""Validate the syntax and identifier integrity of the OpenAPI document."""

from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
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


def test_issued_token_response_conforms_to_its_schema() -> None:
    """The response schema accepts the complete one-time token representation."""
    contract = load_contract()
    schema = contract["components"]["schemas"]["TokenIssued"]
    response = {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "name": "automation",
        "token": "srm_secret",
        "created_at": "2030-01-01T00:00:00Z",
        "expires_at": None,
        "last_used_at": None,
        "revoked_at": None,
    }

    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        response
    )


def test_every_documented_error_response_has_a_problem_body() -> None:
    """Shared error responses consistently describe the runtime Problem representation."""
    responses = load_contract()["components"]["responses"]
    for name in [
        "BadRequest",
        "Unauthorized",
        "Forbidden",
        "NotFound",
        "Conflict",
        "ValidationError",
        "PayloadTooLarge",
        "TooManyRequests",
        "ServiceUnavailable",
    ]:
        assert responses[name]["content"]["application/problem+json"]["schema"] == {
            "$ref": "#/components/schemas/Problem"
        }


def test_every_request_body_operation_documents_payload_too_large() -> None:
    """Every body-bearing operation documents the transport-level size rejection."""
    contract = load_contract()
    for path_item in contract["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "requestBody" not in operation:
                continue
            assert operation["responses"]["413"] == {
                "$ref": "#/components/responses/PayloadTooLarge"
            }


def test_resource_secret_metadata_schema_cannot_expose_material() -> None:
    """The reusable metadata schema contains no plaintext or external-reference property."""
    schema = load_contract()["components"]["schemas"]["ResourceSecretMetadata"]

    assert not ({"value", "reference", "material"} & schema["properties"].keys())
