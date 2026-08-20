"""Adversarial contract tests for the private-organ registry credential path."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
AUTH_HELPER = ROOT / "bootstrap" / "configure-ghcr-pull-auth.py"
SECRET_NAME = "szl-ghcr-pull"
PRIVATE_ORGAN = "killinchu"
ORGANS = ("a11oy", "sentra", "amaru", PRIVATE_ORGAN, "rosie")


def _install_fake_kubectl(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    kubectl = bin_directory / "kubectl"
    kubectl.write_text(
        """#!/usr/bin/env python3
import base64
import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
if os.environ.get("GHCR_TOKEN"):
    print(os.environ["GHCR_TOKEN"], file=sys.stderr)
    raise SystemExit(92)
with Path(os.environ["KUBECTL_CALLS"]).open("a") as stream:
    stream.write(json.dumps(arguments) + "\\n")

if arguments[:2] == ["get", "namespace"]:
    print("namespace/szl")
    raise SystemExit(0)

if arguments[:2] == ["create", "--filename=-"]:
    manifest_bytes = sys.stdin.buffer.read()
    Path(os.environ["KUBECTL_MANIFEST"]).write_bytes(manifest_bytes)
    if os.environ.get("KUBECTL_MODE") == "create-fail-echo-secret":
        manifest = json.loads(manifest_bytes)
        config = json.loads(base64.b64decode(manifest["data"][".dockerconfigjson"]))
        credential = base64.b64decode(config["auths"]["ghcr.io"]["auth"]).decode()
        print(credential, file=sys.stderr)
        raise SystemExit(1)
    print("secret/szl-ghcr-pull created")
    raise SystemExit(0)

if "get" in arguments and "secret" in arguments:
    if os.environ.get("KUBECTL_MODE") == "bad-verification":
        print("Opaque\\ncredential-present", end="")
    else:
        print("kubernetes.io/dockerconfigjson\\ncredential-present", end="")
    raise SystemExit(0)

raise SystemExit(91)
"""
    )
    kubectl.chmod(0o755)
    paths = {
        "KUBECTL_CALLS": str(tmp_path / "kubectl-calls.jsonl"),
        "KUBECTL_MANIFEST": str(tmp_path / "secret.json"),
    }
    return bin_directory, paths


def _run_helper(
    tmp_path: Path,
    *,
    token: bytes,
    mode: str | None = None,
    extra_arguments: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], dict[str, str]]:
    bin_directory, paths = _install_fake_kubectl(tmp_path)
    environment = os.environ.copy()
    environment.update(paths)
    environment["PATH"] = f"{bin_directory}{os.pathsep}{environment['PATH']}"
    if token:
        environment["GHCR_TOKEN"] = token.decode("utf-8", errors="ignore")
    if mode is not None:
        environment["KUBECTL_MODE"] = mode
    else:
        environment.pop("KUBECTL_MODE", None)

    command = [
        sys.executable,
        str(AUTH_HELPER),
        "--username",
        "ci-reader",
    ]
    command.extend(extra_arguments or [])
    completed = subprocess.run(
        command,
        input=token,
        capture_output=True,
        env=environment,
        check=False,
    )
    return completed, paths


def _read_calls(paths: dict[str, str]) -> list[list[str]]:
    call_path = Path(paths["KUBECTL_CALLS"])
    if not call_path.exists():
        return []
    return [json.loads(line) for line in call_path.read_text().splitlines()]


def _deployment(path: Path) -> dict:
    for document in yaml.safe_load_all(path.read_text()):
        if isinstance(document, dict) and document.get("kind") == "Deployment":
            return document
    raise AssertionError(f"Deployment is missing from {path}")


def test_helper_passes_token_only_in_secret_stdin_and_writes_mode_0600_config(tmp_path):
    token = b"adversarial-registry-token-do-not-print"
    docker_config_dir = tmp_path / "host-auth"
    completed, paths = _run_helper(
        tmp_path,
        token=token,
        extra_arguments=["--docker-config-dir", str(docker_config_dir)],
    )

    assert completed.returncode == 0, completed.stderr.decode()
    assert token not in completed.stdout
    assert token not in completed.stderr

    calls = _read_calls(paths)
    assert [call[:2] for call in calls] == [
        ["get", "namespace"],
        ["create", "--filename=-"],
        ["--namespace", "szl"],
    ]
    assert token.decode() not in json.dumps(calls)

    manifest_bytes = Path(paths["KUBECTL_MANIFEST"]).read_bytes()
    assert token not in manifest_bytes
    manifest = json.loads(manifest_bytes)
    assert manifest["metadata"]["name"] == SECRET_NAME
    assert manifest["metadata"]["namespace"] == "szl"
    assert manifest["immutable"] is True
    assert manifest["type"] == "kubernetes.io/dockerconfigjson"

    encoded_config = manifest["data"][".dockerconfigjson"]
    config = json.loads(base64.b64decode(encoded_config))
    encoded_auth = config["auths"]["ghcr.io"]["auth"]
    assert base64.b64decode(encoded_auth) == b"ci-reader:" + token

    local_config = docker_config_dir / "config.json"
    assert json.loads(local_config.read_bytes()) == config
    assert stat.S_IMODE(local_config.stat().st_mode) == 0o600
    assert stat.S_IMODE(docker_config_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("token", [b"", b"line-one\nline-two", b"has-a-tab\t"])
def test_helper_rejects_missing_or_structurally_unsafe_token_before_kubectl(
    tmp_path,
    token,
):
    completed, paths = _run_helper(tmp_path, token=token)

    assert completed.returncode != 0
    assert _read_calls(paths) == []
    if token:
        assert token not in completed.stdout
        assert token not in completed.stderr


def test_helper_suppresses_even_a_hostile_kubectl_error_that_echoes_the_token(tmp_path):
    token = b"credential-kubectl-must-not-echo"
    completed, paths = _run_helper(
        tmp_path,
        token=token,
        mode="create-fail-echo-secret",
        extra_arguments=["--docker-config-dir", str(tmp_path / "host-auth")],
    )

    assert completed.returncode != 0
    assert token not in completed.stdout
    assert token not in completed.stderr
    assert not (tmp_path / "host-auth" / "config.json").exists()
    assert len(_read_calls(paths)) == 2


def test_helper_fails_if_api_server_does_not_preserve_secret_shape(tmp_path):
    token = b"credential-for-shape-check"
    completed, paths = _run_helper(
        tmp_path,
        token=token,
        mode="bad-verification",
        extra_arguments=["--docker-config-dir", str(tmp_path / "host-auth")],
    )

    assert completed.returncode != 0
    assert token not in completed.stdout
    assert token not in completed.stderr
    assert not (tmp_path / "host-auth" / "config.json").exists()
    assert len(_read_calls(paths)) == 3


def test_helper_refuses_to_replace_existing_host_docker_config_before_kubectl(tmp_path):
    docker_config_dir = tmp_path / "host-auth"
    docker_config_dir.mkdir()
    existing = docker_config_dir / "config.json"
    existing.write_text("operator-owned")
    completed, paths = _run_helper(
        tmp_path,
        token=b"must-not-overwrite-host-config",
        extra_arguments=["--docker-config-dir", str(docker_config_dir)],
    )

    assert completed.returncode != 0
    assert existing.read_text() == "operator-owned"
    assert _read_calls(paths) == []


def test_private_runtime_manifest_scopes_auth_away_from_application_container():
    deployment = _deployment(ROOT / "manifests" / "organs" / "killinchu.yaml")
    pod = deployment["spec"]["template"]["spec"]

    assert pod["automountServiceAccountToken"] is False
    assert pod["imagePullSecrets"] == [{"name": SECRET_NAME}]
    registry_volume = next(item for item in pod["volumes"] if item["name"] == "registry-auth")
    assert registry_volume["secret"] == {
        "secretName": SECRET_NAME,
        "items": [{"key": ".dockerconfigjson", "path": "config.json"}],
    }

    gate = next(item for item in pod["initContainers"] if item["name"] == "cosign-gate")
    assert {item["name"]: item.get("value") for item in gate["env"]}["DOCKER_CONFIG"] == "/registry-auth"
    assert {item["name"] for item in gate["volumeMounts"]} >= {"registry-auth"}

    application = next(item for item in pod["containers"] if item["name"] == PRIVATE_ORGAN)
    assert "volumeMounts" not in application or all(
        item["name"] != "registry-auth" for item in application["volumeMounts"]
    )


def test_registry_auth_is_not_attached_to_public_organ_pods():
    for organ in ORGANS:
        primary = _deployment(ROOT / "manifests" / "organs" / f"{organ}.yaml")
        generated = _deployment(ROOT / "deploy" / "organs" / f"{organ}-deployment.yaml")
        for deployment in (primary, generated):
            pod = deployment["spec"]["template"]["spec"]
            if organ == PRIVATE_ORGAN:
                assert pod["imagePullSecrets"] == [{"name": SECRET_NAME}]
                assert pod["automountServiceAccountToken"] is False
            else:
                assert SECRET_NAME not in json.dumps(pod)


def test_generated_deployments_are_reproducible():
    paths = [ROOT / "deploy" / "organs" / f"{organ}-deployment.yaml" for organ in ORGANS]
    before = {path: path.read_bytes() for path in paths}
    completed = subprocess.run(
        [sys.executable, "scripts/gen_organ_deployments.py"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode()
    assert {path: path.read_bytes() for path in paths} == before


def test_ci_authenticates_without_putting_token_in_process_arguments():
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["permissions"]["packages"] == "read"
    build_steps = workflow["jobs"]["build-env"]["steps"]
    by_name = {step.get("name"): step for step in build_steps if "name" in step}

    rejection = by_name["Reject forked private-image acceptance"]
    assert "head.repo.full_name != github.repository" in rejection["if"]

    auth = by_name["Configure least-privilege killinchu registry auth"]
    assert "head.repo.full_name == github.repository" in auth["if"]
    assert auth["env"] == {
        "GHCR_TOKEN": "${{ github.token }}",
        "GHCR_USERNAME": "${{ github.actor }}",
    }
    assert "printf '%s' \"$GHCR_TOKEN\" |" in auth["run"]
    assert "--username \"$GHCR_USERNAME\"" in auth["run"]
    assert "--password" not in auth["run"]

    verifier = by_name["make verify (honest cosign + SLSA gate)"]
    assert verifier["env"] == {
        "DOCKER_CONFIG": "${{ runner.temp }}/szl-ghcr-auth",
        "REQUIRE_PRIVATE_ORGANS": "1",
    }
    cleanup = by_name["Remove host-side registry auth"]
    assert cleanup["if"] == "${{ always() }}"
    assert 'rm -- "$auth_config"' in cleanup["run"]

    checkout_steps = [step for step in build_steps if "uses" in step and "actions/checkout@" in step["uses"]]
    assert checkout_steps[0]["with"]["persist-credentials"] == "false"


def test_authenticated_host_verification_promotes_private_access_failure_to_hard_fail(tmp_path):
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    cosign = bin_directory / "cosign"
    cosign.write_text("#!/usr/bin/env sh\nexit 1\n")
    cosign.chmod(0o755)
    slsa_verifier = bin_directory / "slsa-verifier"
    slsa_verifier.write_text("#!/usr/bin/env sh\nexit 1\n")
    slsa_verifier.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{bin_directory}{os.pathsep}{environment['PATH']}"
    environment["COSIGN_PUB"] = str(ROOT / "keys" / "cosign.pub")

    local = subprocess.run(
        ["bash", "verify/cosign-init.sh", "--all"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert b"killinchu  KNOWN-GAP" in local.stdout

    environment["REQUIRE_PRIVATE_ORGANS"] = "1"
    authenticated = subprocess.run(
        ["bash", "verify/cosign-init.sh", "--all"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert authenticated.returncode != 0
    assert b"killinchu  FAIL" in authenticated.stdout
    assert b"killinchu  KNOWN-GAP" not in authenticated.stdout
