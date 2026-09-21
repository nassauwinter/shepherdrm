"""Verify release-image deployment, recovery, backup, restore, and abuse boundaries."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from scripts.release_acceptance import (
        AcceptanceState,
        ApiClient,
        create_expiring_lease,
        expect,
        login,
        run_api_smoke,
        run_concurrency_probe,
        run_security_probes,
        wait_lease_state,
        wait_ready,
    )
except ModuleNotFoundError:
    from release_acceptance import (  # type: ignore[no-redef]
        AcceptanceState,
        ApiClient,
        create_expiring_lease,
        expect,
        login,
        run_api_smoke,
        run_concurrency_probe,
        run_security_probes,
        wait_lease_state,
        wait_ready,
    )

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "compose.production.yaml"


def available_port() -> int:
    """Reserve an available loopback port for one short-lived Compose stack."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class ComposeStack:
    """Run production Compose commands under an isolated explicit project name."""

    def __init__(self, project: str, image: str, secrets_dir: Path, port: int) -> None:
        self.project = project
        self.port = port
        self.environment = {
            **os.environ,
            "SHEPHERD_IMAGE": image,
            "SHEPHERD_SECRETS_DIR": str(secrets_dir),
            "SHEPHERD_PORT": str(port),
            "SHEPHERD_BIND_ADDRESS": "127.0.0.1",
            "SHEPHERD_SECRET_ENCRYPTION_ACTIVE_KEY_ID": "acceptance",
        }

    @property
    def api(self) -> ApiClient:
        """Return a client for this stack's loopback-only API port."""
        return ApiClient(f"http://127.0.0.1:{self.port}")

    def command(self, *arguments: str) -> list[str]:
        """Build one project-scoped production Compose command."""
        return [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--file",
            str(COMPOSE_FILE),
            *arguments,
        ]

    def run(
        self,
        *arguments: str,
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a text-mode Compose command without printing supplied secret input."""
        return subprocess.run(
            self.command(*arguments),
            cwd=ROOT,
            env=self.environment,
            input=input_text,
            capture_output=capture_output,
            text=True,
            check=check,
        )

    def run_bytes(
        self, *arguments: str, input_bytes: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a binary Compose command for PostgreSQL dump and restore streams."""
        return subprocess.run(
            self.command(*arguments),
            cwd=ROOT,
            env=self.environment,
            input=input_bytes,
            capture_output=True,
            check=True,
        )

    def start_database(self) -> None:
        """Start PostgreSQL and wait for its production health check."""
        self.run("up", "-d", "--wait", "postgres")

    def migrate_and_start(self) -> None:
        """Apply packaged migrations and start API and worker without source mounts."""
        self.run("run", "--rm", "migrate")
        self.run("up", "-d", "--no-deps", "shepherd-rm", "worker")
        wait_ready(self.api)

    def bootstrap(self, username: str, password: str) -> None:
        """Create the first administrator through the packaged bootstrap command."""
        self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "shepherd-rm",
            "shepherd-rm-bootstrap-admin",
            username,
            input_text=f"{password}\n{password}\n",
            capture_output=True,
        )

    def cleanup(self) -> None:
        """Remove only this explicitly named disposable Compose project."""
        self.run("down", "--volumes", "--remove-orphans", check=False)


def write_secret(path: Path, value: str) -> None:
    """Write an ephemeral fixture readable by the non-root acceptance containers."""
    if path.exists():
        path.chmod(0o600)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o444)


def verify_restart_and_expiration(
    stack: ComposeStack, state: AcceptanceState, admin_password: str
) -> None:
    """Prove API, database, and worker interruption recover without lease corruption."""
    stack.run("restart", "shepherd-rm")
    wait_ready(stack.api)
    login(stack.api, "release-admin", admin_password)

    stack.run("stop", "postgres")
    wait_ready(stack.api, ready=False)
    stack.run("start", "postgres")
    wait_ready(stack.api)

    stack.run("stop", "worker")
    lease_id = create_expiring_lease(stack.api, state)
    time.sleep(2)
    lease = expect(
        stack.api.request("GET", f"/v1/leases/{lease_id}", token=state.user_token),
        200,
        "read overdue lease while worker is stopped",
    )
    if lease["state"] != "Active":
        raise RuntimeError("Stopped worker did not preserve the overdue lease for catch-up")
    stack.run("up", "-d", "--no-deps", "worker")
    wait_lease_state(stack.api, state.user_token, lease_id, "Expired")
    expired = expect(
        stack.api.request("GET", f"/v1/leases/{lease_id}", token=state.user_token),
        200,
        "read caught-up lease",
    )
    if expired["end_reason"] != "Expired":
        raise RuntimeError("Expiration catch-up did not record the Expired end reason")


def database_dump(stack: ComposeStack) -> bytes:
    """Create and structurally inspect one custom-format PostgreSQL backup."""
    dump = stack.run_bytes(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --create',
    ).stdout
    if not dump:
        raise RuntimeError("PostgreSQL backup was empty")
    listing = stack.run_bytes(
        "exec", "-T", "postgres", "pg_restore", "--list", input_bytes=dump
    ).stdout
    if b"TABLE DATA public" not in listing:
        raise RuntimeError("PostgreSQL backup did not contain application table data")
    return dump


def restore_database(stack: ComposeStack, dump: bytes) -> None:
    """Restore a backup into a fresh project volume before running packaged migrations."""
    stack.start_database()
    stack.run_bytes(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'pg_restore --username="$POSTGRES_USER" --clean --if-exists --create --dbname=postgres',
        input_bytes=dump,
    )
    stack.migrate_and_start()


def verify_restored_state(
    stack: ComposeStack,
    state: AcceptanceState,
    admin_username: str,
    admin_password: str,
) -> None:
    """Verify identities, leases, audit history, and managed secrets after restore."""
    token = login(stack.api, admin_username, admin_password)
    resource = expect(
        stack.api.request("GET", f"/v1/resources/{state.resource_id}", token=token),
        200,
        "read restored resource",
    )
    if str(resource["id"]) != state.resource_id:
        raise RuntimeError("Restored resource identity changed")
    lease = expect(
        stack.api.request("GET", f"/v1/leases/{state.released_lease_id}", token=token),
        200,
        "read restored lease history",
    )
    if lease["state"] != "Released":
        raise RuntimeError("Restored lease history changed")
    accessed = expect(
        stack.api.request(
            "POST",
            f"/v1/resources/{state.resource_id}/secrets/{state.secret_id}/access",
            token=token,
        ),
        200,
        "access restored managed secret",
    )
    if accessed["value"] != state.secret_value:
        raise RuntimeError("Restored key ring could not decrypt managed-secret material")
    audit_count = stack.run(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
        '--tuples-only --no-align --command="SELECT count(*) FROM audit_events"',
        capture_output=True,
    ).stdout.strip()
    if int(audit_count) <= 0:
        raise RuntimeError("Restored database lost audit history")


def verify_key_ring_boundary(
    stack: ComposeStack,
    key_ring_path: Path,
    key_ring: str,
    state: AcceptanceState,
    admin_username: str,
    admin_password: str,
) -> None:
    """Prove database-only recovery cannot reveal managed-secret material."""
    write_secret(key_ring_path, "{}")
    stack.run("restart", "shepherd-rm")
    wait_ready(stack.api)
    token = login(stack.api, admin_username, admin_password)
    expect(
        stack.api.request(
            "POST",
            f"/v1/resources/{state.resource_id}/secrets/{state.secret_id}/access",
            token=token,
        ),
        503,
        "deny managed-secret access without the key ring",
    )
    write_secret(key_ring_path, key_ring)
    stack.run("restart", "shepherd-rm")
    wait_ready(stack.api)
    verify_restored_state(stack, state, admin_username, admin_password)


def verify_corrupt_ciphertext(
    stack: ComposeStack,
    state: AcceptanceState,
    admin_username: str,
    admin_password: str,
) -> None:
    """Prove malformed stored envelopes fail closed through the deployed API."""
    stack.run(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
        "--command=\"UPDATE resource_secrets SET encrypted_value = decode('00', 'hex') "
        f"WHERE id = '{state.secret_id}'\"",
    )
    token = login(stack.api, admin_username, admin_password)
    result = stack.api.request(
        "POST",
        f"/v1/resources/{state.resource_id}/secrets/{state.secret_id}/access",
        token=token,
    )
    body = expect(result, 500, "deny invalid managed ciphertext")
    rendered = json.dumps(body)
    forbidden = (state.secret_value, "SecretDecryptionError", "ciphertext", "nonce", "key")
    if any(value in rendered for value in forbidden):
        raise RuntimeError("Invalid ciphertext response exposed crypto or secret material")


def verify_logs_do_not_contain(stack: ComposeStack, canaries: list[str]) -> None:
    """Reject release logs that contain deliberately submitted secret material."""
    logs = stack.run("logs", "--no-color", "shepherd-rm", "worker", capture_output=True).stdout
    leaked = [canary for canary in canaries if canary in logs]
    if leaked:
        raise RuntimeError("Release logs contained a submitted secret canary")


def verify_release(image: str) -> dict[str, Any]:
    """Run the deterministic R4 acceptance suite against one local image reference."""
    run_id = uuid.uuid4().hex[:12]
    admin_username = "release-admin"
    admin_password = secrets.token_urlsafe(24)
    user_password = secrets.token_urlsafe(24)
    key_ring = json.dumps(
        {"acceptance": base64.b64encode(secrets.token_bytes(32)).decode()}, separators=(",", ":")
    )
    source: ComposeStack | None = None
    restored: ComposeStack | None = None
    with tempfile.TemporaryDirectory(prefix="shepherdrm-acceptance-") as temporary:
        secrets_dir = Path(temporary)
        password_path = secrets_dir / "postgres_password"
        key_ring_path = secrets_dir / "secret_encryption_keys.json"
        write_secret(password_path, secrets.token_urlsafe(24))
        write_secret(key_ring_path, key_ring)
        source = ComposeStack(f"shepherdrm-r4-{run_id}", image, secrets_dir, available_port())
        restored = ComposeStack(
            f"shepherdrm-r4-restore-{run_id}", image, secrets_dir, available_port()
        )
        try:
            source.start_database()
            source.migrate_and_start()
            source.bootstrap(admin_username, admin_password)
            state = run_api_smoke(source.api, admin_username, admin_password, user_password)
            security_canary = run_security_probes(source.api, state)
            concurrency = run_concurrency_probe(source.api, state)
            verify_restart_and_expiration(source, state, admin_password)
            verify_logs_do_not_contain(source, [security_canary, state.secret_value])

            dump = database_dump(source)
            restore_database(restored, dump)
            verify_restored_state(restored, state, admin_username, admin_password)
            verify_key_ring_boundary(
                restored,
                key_ring_path,
                key_ring,
                state,
                admin_username,
                admin_password,
            )
            verify_corrupt_ciphertext(restored, state, admin_username, admin_password)
            verify_logs_do_not_contain(restored, [state.secret_value])
            return {
                "image": image,
                "database_backup_sha256": hashlib.sha256(dump).hexdigest(),
                "concurrency": concurrency,
                "checks": [
                    "artifact-smoke",
                    "security-abuse",
                    "api-database-worker-recovery",
                    "expiration-catch-up",
                    "backup-restore",
                    "managed-secret-key-boundary",
                    "invalid-ciphertext",
                ],
            }
        finally:
            restored.cleanup()
            source.cleanup()


def main() -> None:
    """Parse the image reference, run acceptance, and emit non-sensitive evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", default="shepherd-rm:acceptance")
    arguments = parser.parse_args()
    started = time.monotonic()
    evidence = verify_release(arguments.image)
    evidence["duration_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
