"""Verify CI retains immutable, release-blocking security checks."""

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yaml"
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def load_workflow() -> dict[str, Any]:
    """Load the committed CI workflow for structural assertions."""
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        workflow = yaml.safe_load(workflow_file)
    assert isinstance(workflow, dict)
    return workflow


def test_all_ci_actions_are_pinned_to_commit_shas() -> None:
    """Every external action reference uses an immutable 40-character commit SHA."""
    workflow = load_workflow()
    action_references = [
        step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step
    ]

    assert action_references
    assert all(PINNED_ACTION.fullmatch(reference) for reference in action_references)


def test_ci_blocks_on_dependency_secret_and_image_findings() -> None:
    """CI contains strict dependency, repository-secret, and final-image gates."""
    jobs = load_workflow()["jobs"]
    repository_steps = jobs["repository-security"]["steps"]
    image_steps = jobs["image-security"]["steps"]

    commands = "\n".join(str(step.get("run", "")) for step in repository_steps)
    assert "pip-audit" in commands
    assert "--strict" in commands
    assert any("gitleaks/gitleaks-action@" in step.get("uses", "") for step in repository_steps)

    trivy_steps = [
        step for step in image_steps if "aquasecurity/trivy-action@" in step.get("uses", "")
    ]
    vulnerability_step = next(
        step for step in trivy_steps if step["with"]["scanners"] == "vuln,secret"
    )
    license_step = next(step for step in trivy_steps if step["with"]["scanners"] == "license")

    assert vulnerability_step["with"]["exit-code"] == "1"
    assert vulnerability_step["with"]["ignore-unfixed"] == "true"
    assert vulnerability_step["with"]["severity"] == "HIGH,CRITICAL"
    assert license_step["with"]["exit-code"] == "1"
    assert license_step["with"]["severity"] == "CRITICAL"
    assert any("scripts/verify_image.py" in step.get("run", "") for step in image_steps)
