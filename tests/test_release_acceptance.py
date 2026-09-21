"""Verify the release-acceptance gate remains exact, isolated, and runnable."""

from pathlib import Path
from typing import Any

import yaml

from scripts.verify_image import contract_manifest
from scripts.verify_release_deployment import ComposeStack

ROOT = Path(__file__).parents[1]


def load_workflow() -> dict[str, Any]:
    """Load CI for structural assertions about the release-image gate."""
    with (ROOT / ".github/workflows/ci.yaml").open(encoding="utf-8") as workflow_file:
        workflow = yaml.safe_load(workflow_file)
    assert isinstance(workflow, dict)
    return workflow


def test_release_acceptance_runs_against_the_built_image() -> None:
    """CI verifies and exercises the exact image built from the pull-request revision."""
    job = load_workflow()["jobs"]["release-acceptance"]
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])

    assert job["timeout-minutes"] == 15
    assert "SHEPHERD_BUILD_REVISION=${{ github.sha }}" in commands
    assert "scripts/verify_image.py shepherd-rm:acceptance" in commands
    assert "scripts/verify_release_deployment.py shepherd-rm:acceptance" in commands


def test_contract_manifest_covers_openapi_and_every_migration_source() -> None:
    """Compatibility verification hashes the contract and all maintained migration code."""
    manifest = contract_manifest(ROOT)
    migration_paths = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "migrations").rglob("*.py")
    }

    assert manifest["openapi/openapi.yaml"]
    assert set(manifest) == {"openapi/openapi.yaml", *migration_paths}


def test_compose_stack_commands_are_project_scoped(tmp_path: Path) -> None:
    """Cleanup and orchestration commands always target one explicit disposable project."""
    stack = ComposeStack("acceptance-test", "shepherd-rm:test", tmp_path, 18000)

    command = stack.command("down", "--volumes")

    assert command[:4] == ["docker", "compose", "--project-name", "acceptance-test"]
    assert str(ROOT / "compose.production.yaml") in command


def test_release_acceptance_probes_invalid_ciphertext_through_the_api() -> None:
    """The artifact gate corrupts only disposable storage and requires a safe API failure."""
    source = (ROOT / "scripts" / "verify_release_deployment.py").read_text(encoding="utf-8")

    assert "def verify_corrupt_ciphertext(" in source
    assert "deny invalid managed ciphertext" in source
    assert '"invalid-ciphertext"' in source
