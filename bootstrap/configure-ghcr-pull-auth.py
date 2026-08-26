"""Install the narrow GHCR pull credential used by the private organ.

The password/token is accepted only on standard input.  It is never placed in a
command-line argument, a child-process environment variable, or command output.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

REGISTRY = "ghcr.io"
DEFAULT_NAMESPACE = "szl"
DEFAULT_SECRET_NAME = "szl-ghcr-pull"
MAX_CREDENTIAL_BYTES = 4096
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")


class ConfigurationError(Exception):
    """A safe-to-report configuration failure."""


def fail(message: str) -> NoReturn:
    raise ConfigurationError(message)


def validate_dns_label(value: str, label: str) -> str:
    if not DNS_LABEL.fullmatch(value):
        fail(f"{label} must be a valid DNS label")
    return value


def validate_username(value: str) -> str:
    if not value or len(value) > 256:
        fail("registry username must contain between 1 and 256 characters")
    if ":" in value or any(
        ord(character) < 33 or ord(character) == 127 for character in value
    ):
        fail("registry username contains an unsupported character")
    return value


def read_token() -> str:
    raw = sys.stdin.buffer.read(MAX_CREDENTIAL_BYTES + 1)
    if len(raw) > MAX_CREDENTIAL_BYTES:
        fail("registry credential exceeds the supported size")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("registry credential must be UTF-8")
    if not token:
        fail("registry credential is required on standard input")
    if any(ord(character) < 33 or ord(character) == 127 for character in token):
        fail("registry credential contains an unsupported character")
    return token


def docker_config(username: str, token: str) -> bytes:
    encoded_auth = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
    document = {"auths": {REGISTRY: {"auth": encoded_auth}}}
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def secret_manifest(namespace: str, secret_name: str, config: bytes) -> bytes:
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "labels": {
                "app.kubernetes.io/part-of": "szl-build-env",
                "szl.holdings/purpose": "killinchu-image-pull",
            },
            "name": secret_name,
            "namespace": namespace,
        },
        "immutable": True,
        "type": "kubernetes.io/dockerconfigjson",
        "data": {
            ".dockerconfigjson": base64.b64encode(config).decode("ascii"),
        },
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def sanitized_child_environment(token: str) -> dict[str, str]:
    environment = os.environ.copy()
    known_credential_names = {"CR_PAT", "GHCR_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}
    for name, value in list(environment.items()):
        if name in known_credential_names or value == token:
            environment.pop(name, None)
    return environment


def run_kubectl(
    arguments: list[str],
    *,
    environment: dict[str, str],
    input_bytes: bytes | None = None,
) -> bytes:
    completed = subprocess.run(
        ["kubectl", *arguments],
        input=input_bytes,
        capture_output=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        # kubectl output is intentionally not relayed: admission or client errors
        # must never have an opportunity to echo the submitted Secret document.
        fail("kubectl rejected the registry-auth operation")
    return completed.stdout


def prepare_local_config_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        fail("Docker config destination must be a real directory")
    directory.chmod(0o700)

    destination = directory / "config.json"
    if destination.exists() or destination.is_symlink():
        fail("refusing to replace an existing Docker config")


def write_local_config(directory: Path, config: bytes) -> None:
    destination = directory / "config.json"

    temporary = directory / f".config.json.{os.getpid()}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(config)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-linking is an atomic no-replace publish. A path introduced after
        # the preflight cannot be overwritten, including a symlink.
        os.link(temporary, destination, follow_symlinks=False)
        destination.chmod(0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the immutable killinchu GHCR pull Secret",
    )
    parser.add_argument("--username", required=True, help="GHCR login (not secret)")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--secret-name", default=DEFAULT_SECRET_NAME)
    parser.add_argument(
        "--docker-config-dir",
        type=Path,
        help="also write a mode-0600 config.json for the host-side verifier",
    )
    return parser.parse_args()


def main() -> int:
    try:
        arguments = parse_args()
        namespace = validate_dns_label(arguments.namespace, "namespace")
        secret_name = validate_dns_label(arguments.secret_name, "secret name")
        username = validate_username(arguments.username)
        if shutil.which("kubectl") is None:
            fail("kubectl is required")
        if arguments.docker_config_dir is not None:
            prepare_local_config_directory(arguments.docker_config_dir)

        token = read_token()
        config = docker_config(username, token)
        manifest = secret_manifest(namespace, secret_name, config)
        child_environment = sanitized_child_environment(token)

        run_kubectl(
            ["get", "namespace", namespace, "--output=name"],
            environment=child_environment,
        )
        # A fresh Secret is required. Refusing to update an existing credential
        # avoids silently taking ownership of operator-managed authentication.
        run_kubectl(
            ["create", "--filename=-"],
            environment=child_environment,
            input_bytes=manifest,
        )

        verification = run_kubectl(
            [
                "--namespace",
                namespace,
                "get",
                "secret",
                secret_name,
                "--output=go-template={{.type}}{{\"\\n\"}}{{if index .data \".dockerconfigjson\"}}credential-present{{end}}",
            ],
            environment=child_environment,
        ).decode("utf-8", errors="replace")
        if verification != "kubernetes.io/dockerconfigjson\ncredential-present":
            fail("created registry Secret failed structural verification")

        if arguments.docker_config_dir is not None:
            write_local_config(arguments.docker_config_dir, config)

        print(f"secret/{secret_name} configured for the private organ")
        return 0
    except ConfigurationError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    except OSError:
        print("[FAIL] registry authentication could not be configured", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
