from pathlib import Path
from typing import Any

import yaml


def load_openapi_contract(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as contract_file:
        contract = yaml.safe_load(contract_file)
    if not isinstance(contract, dict):
        raise ValueError(f"OpenAPI contract at {path} must contain a mapping")
    return contract
