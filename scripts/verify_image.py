"""Verify that a built Shepherd RM image contains only its supported runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> str:
    """Run one inspection command and return its trimmed standard output."""
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def contract_manifest(root: Path) -> dict[str, str]:
    """Hash the public contract and migration sources shipped in a release image."""
    paths = [root / "openapi" / "openapi.yaml", *sorted((root / "migrations").rglob("*.py"))]
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def verify_image(image: str) -> None:
    """Assert runtime identity, package exclusions, and local-file exclusions."""
    configured_user = run("docker", "image", "inspect", "--format", "{{.Config.User}}", image)
    if configured_user != "shepherd":
        raise RuntimeError(f"Image user is {configured_user!r}, expected 'shepherd'")
    version = run(
        "docker",
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "org.opencontainers.image.version"}}',
        image,
    )
    revision = run(
        "docker",
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        image,
    )
    if version != "0.1.0":
        raise RuntimeError(f"Image version label is {version!r}, expected '0.1.0'")
    if revision == "unknown" or len(revision) < 7:
        raise RuntimeError("Image revision label must identify the source commit")

    runtime_check = """
import importlib.util
import os
import pathlib
import shutil

assert os.getuid() == 10001
state_directory = pathlib.Path('/var/lib/shepherd-rm')
assert state_directory.is_dir()
assert os.access(state_directory, os.W_OK)
for package in ('mypy', 'pytest', 'ruff', 'sphinx'):
    assert importlib.util.find_spec(package) is None, package
for command in ('uv', 'uvx'):
    assert shutil.which(command) is None, command
for path in ('/app/.env', '/app/pyproject.toml', '/app/uv.lock', '/app/src'):
    assert not pathlib.Path(path).exists(), path
import shepherd_rm
"""
    subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", image, "-c", runtime_check],
        check=True,
    )

    manifest_script = """
import hashlib
import json
import pathlib

root = pathlib.Path('/app')
paths = [root / 'openapi' / 'openapi.yaml', *sorted((root / 'migrations').rglob('*.py'))]
print(json.dumps({
    path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in paths
}, sort_keys=True))
"""
    embedded_manifest = json.loads(
        run("docker", "run", "--rm", "--entrypoint", "python", image, "-c", manifest_script)
    )
    if embedded_manifest != contract_manifest(ROOT):
        raise RuntimeError("Image contract or migration sources differ from the release tree")


def main() -> None:
    """Parse the image reference and run the release-runtime assertions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", default="shepherd-rm:check")
    arguments = parser.parse_args()
    verify_image(arguments.image)


if __name__ == "__main__":
    main()
