"""Verify the release workflow publishes only a checked, immutable image."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[1]


def load_workflow() -> dict[str, Any]:
    """Load the committed release workflow for structural assertions."""
    with (ROOT / ".github/workflows/release.yaml").open(encoding="utf-8") as workflow_file:
        workflow = yaml.safe_load(workflow_file)
    assert isinstance(workflow, dict)
    return workflow


def test_release_is_tag_driven_and_environment_protected() -> None:
    """Publishing runs only for version tags and requires the release environment."""
    workflow = load_workflow()
    job = workflow["jobs"]["publish"]

    assert workflow[True]["push"]["tags"] == ["v*"]
    assert job["environment"] == "release"
    assert workflow["permissions"] == {
        "contents": "write",
        "attestations": "write",
        "id-token": "write",
    }
    identity_step = next(step for step in job["steps"] if step.get("id") == "identity")
    assert "version_pattern=" in identity_step["run"]
    assert '[[ ! "$version" =~ ^$version_pattern$ ]]' in identity_step["run"]
    assert "git fetch origin main --depth=1" in identity_step["run"]
    assert "git rev-parse origin/main" in identity_step["run"]


def test_release_verifies_and_records_the_published_digest_before_release() -> None:
    """Runtime checks, scan, provenance, SBOM, and notes all use the immutable digest."""
    steps = load_workflow()["jobs"]["publish"]["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    uses = "\n".join(str(step.get("uses", "")) for step in steps)
    build_step = next(step for step in steps if step.get("id") == "build")
    scan_step = next(step for step in steps if "aquasecurity/trivy-action@" in step.get("uses", ""))
    provenance_step = next(
        step for step in steps if "actions/attest-build-provenance@" in step.get("uses", "")
    )

    assert "docker pull '${{ steps.image.outputs.reference }}'" in commands
    assert "scripts/verify_image.py" in commands
    assert "scripts/verify_release_deployment.py" in commands
    assert "'${{ steps.image.outputs.reference }}'" in commands
    assert build_step["with"]["push"] is True
    assert build_step["with"]["platforms"] == "linux/amd64"
    assert (
        "SHEPHERD_BUILD_VERSION=${{ steps.identity.outputs.version }}"
        in build_step["with"]["build-args"]
    )
    assert "SHEPHERD_BUILD_REVISION=${{ github.sha }}" in build_step["with"]["build-args"]
    assert scan_step["with"]["image-ref"] == "${{ steps.image.outputs.reference }}"
    assert scan_step["with"]["severity"] == "HIGH,CRITICAL"
    assert provenance_step["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}"
    assert provenance_step["with"]["push-to-registry"] is True
    assert "actions/attest-build-provenance@" in uses
    assert "anchore/sbom-action@" in uses
    assert "softprops/action-gh-release@" in uses
