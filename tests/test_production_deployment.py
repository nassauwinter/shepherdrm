"""Verify the supported production Compose boundary."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE_PATH = Path(__file__).parents[1] / "compose.production.yaml"


def load_compose() -> dict[str, Any]:
    """Load the versioned production Compose definition."""
    with COMPOSE_PATH.open(encoding="utf-8") as compose_file:
        document = yaml.safe_load(compose_file)
    assert isinstance(document, dict)
    return document


def test_production_services_use_published_image_and_private_database() -> None:
    """Production pulls the server image and never publishes PostgreSQL."""
    services = load_compose()["services"]

    for service_name in ("migrate", "shepherd-rm", "worker"):
        service = services[service_name]
        assert "build" not in service
        assert service["image"].startswith("${SHEPHERD_IMAGE:?")
        assert "database" in service["networks"]
        assert "postgres_password" in service["secrets"]

    assert "ports" not in services["postgres"]
    assert services["postgres"]["networks"] == ["database"]
    assert load_compose()["networks"]["database"]["internal"] is True


def test_production_api_uses_readiness_and_file_backed_keys() -> None:
    """Traffic readiness and protected key files are explicit deployment controls."""
    api = load_compose()["services"]["shepherd-rm"]
    healthcheck = " ".join(api["healthcheck"]["test"])

    assert "/ready" in healthcheck
    assert "/health" not in healthcheck
    assert api["environment"]["SHEPHERD_SECRET_ENCRYPTION_KEYS_FILE"] == (
        "/run/secrets/secret_encryption_keys"
    )
    assert "secret_encryption_keys" in api["secrets"]
    assert api["cap_drop"] == ["ALL"]
    assert api["security_opt"] == ["no-new-privileges:true"]
