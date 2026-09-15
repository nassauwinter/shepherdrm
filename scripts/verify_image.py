"""Verify that a built Shepherd RM image contains only its supported runtime."""

from __future__ import annotations

import argparse
import subprocess


def run(*command: str) -> str:
    """Run one inspection command and return its trimmed standard output."""
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def verify_image(image: str) -> None:
    """Assert runtime identity, package exclusions, and local-file exclusions."""
    configured_user = run("docker", "image", "inspect", "--format", "{{.Config.User}}", image)
    if configured_user != "shepherd":
        raise RuntimeError(f"Image user is {configured_user!r}, expected 'shepherd'")

    runtime_check = """
import importlib.util
import os
import pathlib
import shutil

assert os.getuid() == 10001
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


def main() -> None:
    """Parse the image reference and run the release-runtime assertions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", default="shepherd-rm:check")
    arguments = parser.parse_args()
    verify_image(arguments.image)


if __name__ == "__main__":
    main()
