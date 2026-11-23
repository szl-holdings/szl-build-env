"""Adversarial contract tests for the private-organ registry credential path."""

from __future__ import annotations

import base64
import json
import os
import re
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
ORGAN_RUNTIME_SPECS = {
    "a11oy": {"digest": "sha256:c08839a06002b88c7c5c11dc8140099f8dd56ba0a618c1b5457bdedf17404cef", "source": "bab6cbec341f4206edf22d13e93af690f656650f", "ref": "refs/heads/main", "run": "44097341241", "health": "/healthz"},
    "sentra": {"digest": "sha256:62da54f92b39dd30537df87af08d4dda2c2bc14508fe53bd31ca7080dd62d08d", "source": "b9e609f7b897d0f2d689453b7bb870ebcd4e79d1", "ref": "refs/heads/main", "run": "45086503720", "health": "/healthz"},
    "amaru": {"digest": "sha256:c9f39694f9e8120bc5ba19c20db88d3a271eafa87eeb3bd03bca352a5ae82512", "source": "6e9c2fee26f5e8f5bd629dfda9e13cdb63a9688d", "ref": "refs/heads/main", "run": "45086464581", "health": "/healthz"},
    "killinchu": {"digest": "sha256:d7702fa2e9d1f844ccb6c4e62c27e516b2651724dfe8997e2c83c3a3609c9045", "source": "e5ca7b75adbfe54891fcff26c8abbdc61dc1fc48", "ref": "refs/heads/main", "run": "44182142894", "health": "/api/killinchu/healthz"},
    "rosie": {"digest": "sha256:dfa93e2d7f2e2de9437b07d9ec56ee1794ffcff752cf6dc634abd2707cb3d1ab", "source": "6b64ef3bdc4f89831654837515bf760b8c8a8ad5", "ref": "refs/heads/main", "run": "45086473772", "health": "/healthz"},
}
ORGANS = tuple(ORGAN_RUNTIME_SPECS)
KILLINCHU_SOURCE_SHA = ORGAN_RUNTIME_SPECS[PRIVATE_ORGAN]["source"]
KILLINCHU_DIGEST = ORGAN_RUNTIME_SPECS[PRIVATE_ORGAN]["digest"]
KILLINCHU_IMAGE = f"ghcr.io/szl-holdings/killinchu@{KILLINCHU_DIGEST}"


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


def _document(path: Path, kind: str) -> dict:
    for document in yaml.safe_load_all(path.read_text()):
        if isinstance(document, dict) and document.get("kind") == kind:
            return document
    raise AssertionError(f"{kind} is missing from {path}")


def _deployment(path: Path) -> dict:
    return _document(path, "Deployment")


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


def test_a11oy_v3_signing_requires_matching_immutable_verifiers():
    # The current A11oy publisher uses Cosign v3 OCI 1.1 referrers.
    # Neither a v2-only verifier nor an unsigned image is admissible.
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "releases/download/v3.1.4/cosign-linux-amd64" in workflow
    assert "424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa" in workflow
    assert "sha256sum --check --status" in workflow
    assert "releases/download/v2.4.1/cosign-linux-amd64" not in workflow
    host_verifier = (ROOT / "verify" / "cosign-init.sh").read_text()
    assert 'if [[ "$organ" == "a11oy" ]]; then' in host_verifier
    assert "cosign verify-attestation --type slsaprovenance" in host_verifier
    assert '--certificate-github-workflow-sha "$workflow_sha"' in host_verifier
    assert '--certificate-github-workflow-trigger "push"' in host_verifier
    assert "exact A11oy signed provenance absent or invalid; no L1 downgrade" in host_verifier

    manifest = _deployment(ROOT / "manifests" / "organs" / "a11oy.yaml")
    pod = manifest["spec"]["template"]["spec"]
    gate = next(item for item in pod["initContainers"] if item["name"] == "cosign-gate")
    assert gate["image"] == (
        "ghcr.io/sigstore/cosign/cosign@sha256:"
        "0f06154836d2de34f887a5c273506d52ca43edaa1d9e53e6510fa78a48f69c2d"
    )
    assert gate["args"][0] == "verify"
    assert "--certificate-github-workflow-sha" in gate["args"]
    assert "--certificate-identity-regexp" not in gate["args"]


def test_signed_a11oy_image_has_measured_memory_admission_headroom():
    # Signed image bab6cbec bundles a 986,047,904-byte GGUF.
    # CI 44118532387 observed OOMKilled (exit 137) at 512Mi.
    model_bytes = 986_047_904
    allocated_bytes = 3 * 1024**3
    assert allocated_bytes > 3 * model_bytes
    for path in (
        ROOT / "manifests" / "organs" / "a11oy.yaml",
        ROOT / "deploy" / "organs" / "a11oy-deployment.yaml",
    ):
        app = _deployment(path)["spec"]["template"]["spec"]["containers"][0]
        assert app["resources"]["requests"]["memory"] == "1Gi"
        assert app["resources"]["limits"]["memory"] == "3Gi"
    for other in ("sentra", "amaru", "killinchu", "rosie"):
        app = _deployment(ROOT / "deploy" / "organs" / f"{other}-deployment.yaml")[
            "spec"
        ]["template"]["spec"]["containers"][0]
        assert app["resources"]["limits"]["memory"] == "512Mi"


@pytest.mark.skipif(os.name == "nt", reason="Unix executable and native Bash required")
@pytest.mark.parametrize("attestation_accepted", [False, True])
def test_a11oy_signed_provenance_is_a_hard_gate_not_l1_downgrade(
    tmp_path, attestation_accepted
):
    binary = tmp_path / "bin"
    binary.mkdir()
    cosign = binary / "cosign"
    cosign.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "verify-attestation" ]; then\n'
        "  exit " + ("0" if attestation_accepted else "7") + "\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    cosign.chmod(0o755)
    slsa = binary / "slsa-verifier"
    slsa.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    slsa.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{binary}{os.pathsep}{environment['PATH']}"
    environment["COSIGN_PUB"] = str(ROOT / "keys" / "cosign.pub")
    outcome = subprocess.run(
        ["bash", "verify/cosign-init.sh", "--all"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )
    if attestation_accepted:
        assert outcome.returncode == 0, outcome.stdout.decode(errors="replace")
        assert b"exact signed provenance verified" in outcome.stdout
    else:
        assert outcome.returncode != 0
        assert b"exact A11oy signed provenance absent or invalid" in outcome.stdout
        assert b"a11oy      FAIL" in outcome.stdout
        assert b"sentra     PASS" in outcome.stdout


def test_all_organs_are_digest_pinned_to_exact_runtime_and_certificate_contracts():
    verifier = (ROOT / "verify" / "cosign-init.sh").read_text()
    generator = (ROOT / "scripts" / "gen_organ_deployments.py").read_text()

    assert "ORGAN_IMAGE_TAG" not in generator
    assert "IMAGE_TAG" not in generator
    assert "ORGAN_TAG" not in verifier
    assert "--certificate-identity-regexp" not in verifier
    assert "slsa-verifier not installed — L2 checks will be skipped" not in verifier
    assert 'then red "slsa-verifier not installed' in verifier

    for organ, spec in ORGAN_RUNTIME_SPECS.items():
        image = f"ghcr.io/szl-holdings/{organ}@{spec['digest']}"
        primary_path = ROOT / "manifests" / "organs" / f"{organ}.yaml"
        generated_path = ROOT / "deploy" / "organs" / f"{organ}-deployment.yaml"
        primary = _deployment(primary_path)
        generated = _deployment(generated_path)

        assert re.fullmatch(rf"ghcr\.io/szl-holdings/{organ}@sha256:[0-9a-f]{{64}}", image)
        for deployment in (primary, generated):
            pod = deployment["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            application = pod["containers"][0]
            assert application["image"] == image
            assert application["ports"] == [{"name": "http", "containerPort": 7860}]

        pod = primary["spec"]["template"]["spec"]
        gate = next(item for item in pod["initContainers"] if item["name"] == "cosign-gate")
        assert gate["args"][-1] == image
        assert "--certificate-identity" in gate["args"]
        assert "--certificate-identity-regexp" not in gate["args"]
        expected_identity = f"https://github.com/szl-holdings/{organ}/.github/workflows/ghcr-build-push.yml@{spec['ref']}"
        def argument_value(flag):
            index = gate["args"].index(flag)
            return gate["args"][index + 1]

        assert argument_value("--certificate-identity") == expected_identity
        assert argument_value("--certificate-oidc-issuer") == "https://token.actions.githubusercontent.com"
        assert argument_value("--certificate-github-workflow-repository") == f"szl-holdings/{organ}"
        assert argument_value("--certificate-github-workflow-ref") == spec["ref"]
        assert argument_value("--certificate-github-workflow-sha") == spec["source"]
        assert argument_value("--certificate-github-workflow-trigger") == "push"
        assert {item["name"]: item.get("value") for item in gate["env"]}["ORGAN_IMAGE"] == image

        application = pod["containers"][0]
        if organ == "a11oy":
            # This opt-in is isolated to the kind acceptance deployment.
            # Source feature existence, not a substitute for traced runtime readback.
            env = {item["name"]: item.get("value") for item in application["env"]}
            assert env["A11OY_BUILD_ENV_FANOUT"] == "1"
            assert env["OTEL_SERVICE_NAME"] == "a11oy"
            assert env["OTEL_TRACES_EXPORTER"] == "otlp"
        expected_probe = {"path": spec["health"], "port": "http"}
        assert application["readinessProbe"]["httpGet"] == expected_probe
        assert application["livenessProbe"]["httpGet"] == expected_probe
        service = _document(primary_path, "Service")
        assert service["spec"]["ports"] == [{"name": "http", "port": 8080, "targetPort": "http"}]

        for manifest_path in (primary_path, generated_path):
            manifest_text = manifest_path.read_text()
            assert image in manifest_text
            assert f"ghcr.io/szl-holdings/{organ}:uds-v0.2.0" not in manifest_text
            assert f"ghcr.io/szl-holdings/{organ}:latest" not in manifest_text

        assert image in verifier
        assert spec["source"] in verifier
        assert spec["ref"] in verifier
        assert image in generator

    honest_gaps = (ROOT / "HONEST_GAPS.md").read_text()
    assert KILLINCHU_SOURCE_SHA in honest_gaps
    assert KILLINCHU_DIGEST in honest_gaps
    assert "44182142894" in honest_gaps

def test_generated_deployments_are_reproducible():
    paths = [ROOT / "deploy" / "organs" / f"{organ}-deployment.yaml" for organ in ORGANS]
    before = {path: path.read_bytes() for path in paths}
    environment = os.environ.copy()
    environment["ORGAN_IMAGE_TAG"] = "latest"
    completed = subprocess.run(
        [sys.executable, "scripts/gen_organ_deployments.py"],
        cwd=ROOT,
        env=environment,
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
    }
    cleanup = by_name["Remove host-side registry auth"]
    assert cleanup["if"] == "${{ always() }}"
    assert 'rm -- "$auth_config"' in cleanup["run"]

    checkout_steps = [step for step in build_steps if "uses" in step and "actions/checkout@" in step["uses"]]
    assert checkout_steps[0]["with"]["persist-credentials"] == "false"


def test_registry_or_crypto_failure_is_never_downgraded_to_known_gap(tmp_path):
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

    result = subprocess.run(
        ["bash", "verify/cosign-init.sh", "--all"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode != 0
    assert b"killinchu  FAIL" in result.stdout
    assert b"KNOWN-GAP" not in result.stdout
