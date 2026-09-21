"""Small, dependency-free task runner for local development."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: Sequence[str]) -> None:
    """Run a command from the repository root and fail with the same status."""
    subprocess.run(command, cwd=ROOT, check=True)


def uv_run(*command: str) -> None:
    run(("uv", "run", "--locked", *command))


def install() -> None:
    run(("uv", "sync", "--locked"))


def test() -> None:
    uv_run("pytest")


def lint() -> None:
    uv_run("ruff", "check", ".")


def format_code() -> None:
    uv_run("ruff", "format", ".")
    uv_run("ruff", "check", "--fix", ".")


def format_check() -> None:
    uv_run("ruff", "format", "--check", ".")


def typecheck() -> None:
    uv_run("mypy")


def docs() -> None:
    uv_run("sphinx-build", "-W", "--keep-going", "-b", "html", "docs", "docs/_build/html")


def serve() -> None:
    """Run the dev server without Uvicorn's raw-URL access logger."""
    uv_run(
        "uvicorn",
        "shepherd_rm.main:app",
        "--reload",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--no-access-log",
    )


def check() -> None:
    for task in (lint, format_check, typecheck, test, docs):
        task()


def db_up() -> None:
    run(("docker", "compose", "up", "-d", "postgres"))


def db_down() -> None:
    run(("docker", "compose", "down"))


def migrate() -> None:
    uv_run("alembic", "upgrade", "head")


def migration_check() -> None:
    uv_run("alembic", "check")


def image_check() -> None:
    """Build and verify the supported runtime-only container image."""
    image = "shepherd-rm:check"
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run(
        (
            "docker",
            "build",
            "--build-arg",
            "SHEPHERD_BUILD_VERSION=0.1.0",
            "--build-arg",
            f"SHEPHERD_BUILD_REVISION={revision}",
            "--tag",
            image,
            ".",
        )
    )
    run(("python3", "scripts/verify_image.py", image))


def release_check() -> None:
    """Build one image and exercise its production deployment and recovery boundary."""
    image = "shepherd-rm:acceptance"
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run(
        (
            "docker",
            "build",
            "--build-arg",
            "SHEPHERD_BUILD_VERSION=0.1.0",
            "--build-arg",
            f"SHEPHERD_BUILD_REVISION={revision}",
            "--tag",
            image,
            ".",
        )
    )
    run(("python3", "scripts/verify_image.py", image))
    run(("python3", "scripts/verify_release_deployment.py", image))


def clean() -> None:
    for relative_path in (
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "docs/_build",
        "htmlcov",
    ):
        path = ROOT / relative_path
        if path.exists():
            shutil.rmtree(path)


TASKS: dict[str, Callable[[], None]] = {
    "install": install,
    "test": test,
    "lint": lint,
    "format": format_code,
    "format-check": format_check,
    "typecheck": typecheck,
    "docs": docs,
    "serve": serve,
    "check": check,
    "db-up": db_up,
    "db-down": db_down,
    "migrate": migrate,
    "migration-check": migration_check,
    "image-check": image_check,
    "release-check": release_check,
    "clean": clean,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASKS)
    arguments = parser.parse_args()
    try:
        TASKS[arguments.task]()
    except FileNotFoundError as error:
        raise SystemExit(f"Required command is not installed: {error.filename}") from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None


if __name__ == "__main__":
    main()
