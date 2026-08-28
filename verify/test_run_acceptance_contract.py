import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "verify" / "run-acceptance.sh"
ROUTE_VALIDATOR = ROOT / "verify" / "validate_route_ack.py"
TRACE_VALIDATOR = ROOT / "verify" / "validate_jaeger_trace.py"
VALID_TRACE_ID = "0123456789abcdef0123456789abcdef"
VALID_ROOT_SPAN_ID = "ffffffffffffffff"
VALID_SEED_EPOCH_US = 1_800_000_000_000_000
VALID_TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
VALID_FANOUT = ["sentra", "amaru", "killinchu", "rosie"]


def _usable_bash() -> str | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    try:
        subprocess.run(
            [bash, "--version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bash


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _raw_commit_parents() -> list[str]:
    raw_commit = subprocess.run(
        ["git", "cat-file", "commit", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    headers, separator, _message = raw_commit.partition(b"\n\n")
    assert separator, "commit object must have a header/message boundary"
    return [
        line[len(b"parent ") :].decode("ascii")
        for line in headers.splitlines()
        if line.startswith(b"parent ")
    ]


def _pull_request_merge_parents() -> list[str]:
    parents = _raw_commit_parents()
    if len(parents) == 2:
        return parents
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    ):
        pytest.fail("pull_request CI must expose an exact two-parent merge commit object")
    pytest.skip("test requires the pull_request synthetic merge checkout")


def _write_pull_request_event(path: Path, base_sha: str, head_sha: str) -> None:
    path.write_text(
        json.dumps(
            {
                "action": "synchronize",
                "number": 20,
                "pull_request": {
                    "state": "open",
                    "base": {
                        "ref": "main",
                        "sha": base_sha,
                        "repo": {"full_name": "szl-holdings/szl-build-env"},
                    },
                    "head": {
                        "ref": "successor",
                        "sha": head_sha,
                        "repo": {"full_name": "szl-holdings/szl-build-env"},
                    },
                },
            }
        ),
        encoding="utf-8",
        newline="\n",
    )


def _pull_request_environment(event: Path) -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_BASE_REF": "main",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_HEAD_REF": "successor",
        "GITHUB_REF": "refs/pull/20/merge",
        "GITHUB_REPOSITORY": "szl-holdings/szl-build-env",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "123",
        "GITHUB_SHA": _git_head(),
    }


def _run_acceptance(tmp_path: Path, extra_env: dict[str, str]):
    bash = _usable_bash()
    if bash is None:
        pytest.skip("a runnable bash is required for the acceptance contract test")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    kubectl = bin_dir / "kubectl"
    kubectl.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IXUSR)
    python3 = bin_dir / "python3"
    python3.write_text(
        f"#!/bin/sh\nexec '{Path(sys.executable).as_posix()}' \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    python3.chmod(python3.stat().st_mode | stat.S_IXUSR)

    evidence = tmp_path / "evidence.json"
    env = os.environ.copy()
    for name in (
        "GITHUB_ACTIONS",
        "GITHUB_BASE_REF",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_HEAD_REF",
        "GITHUB_REF",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_RUN_ID",
        "GITHUB_SHA",
    ):
        env.pop(name, None)
    env.update(
        {
            "ACCEPTANCE_EVIDENCE_PATH": str(evidence),
            "HTTP_TIMEOUT_SECONDS": "1",
            "PATH": os.pathsep.join((str(bin_dir), env["PATH"])),
            "PORT_FORWARD_TIMEOUT_SECONDS": "1",
            "READINESS_POLL_SECONDS": "1",
            "READINESS_TIMEOUT_SECONDS": "1",
            "TRACE_POLL_SECONDS": "1",
            "TRACE_TIMEOUT_SECONDS": "1",
        }
    )
    env.update(extra_env)
    result = subprocess.run(
        [bash, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert evidence.is_file(), result.stderr
    return result, json.loads(evidence.read_text(encoding="utf-8"))


def test_local_run_derives_git_identity_and_emits_fail_closed_evidence(tmp_path):
    result, evidence = _run_acceptance(
        tmp_path,
        {"GITHUB_SHA": "not-a-source-identity"},
    )

    assert result.returncode == 1
    assert evidence["execution"]["context"] == "local"
    assert evidence["execution"]["event_name"] == "local"
    assert len(evidence["execution"]["local_run_nonce"]) == 32
    assert all(character in "0123456789abcdef" for character in evidence["execution"]["local_run_nonce"])
    assert evidence["execution"]["repository"] == "local"
    assert evidence["execution"]["base_sha"] == _git_head()
    assert evidence["execution"]["candidate_sha"] == _git_head()
    assert evidence["github_sha"] == _git_head()
    assert evidence["github_run_id"] is None
    assert evidence["github_run_attempt"] is None
    assert evidence["failure"] == "deployment_missing"
    assert evidence["result"] == "FAIL"
    assert all(service["failure"] == "deployment_missing" for service in evidence["services"])


def test_local_run_rejects_dirty_source_tree(tmp_path):
    marker = ROOT / ".acceptance-contract-dirty-test"
    marker.write_text("dirty\n", encoding="utf-8", newline="\n")
    try:
        result, evidence = _run_acceptance(tmp_path, {})
    finally:
        marker.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "clean tracked and untracked source tree" in result.stderr
    assert evidence["failure"] == "dirty_source_tree"


def test_local_nonce_is_fresh_and_bound_into_trace_identity(tmp_path):
    _, first = _run_acceptance(tmp_path / "first", {})
    _, second = _run_acceptance(tmp_path / "second", {})

    assert first["execution"]["local_run_nonce"] != second["execution"]["local_run_nonce"]
    assert len(first["execution"]["local_run_nonce"]) == 32
    assert len(second["execution"]["local_run_nonce"]) == 32
    assert first["trace"]["trace_id"] != second["trace"]["trace_id"]
    assert first["trace"]["traceparent"] != second["trace"]["traceparent"]


def test_github_actions_still_requires_governed_run_identity(tmp_path):
    result, evidence = _run_acceptance(
        tmp_path,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "",
            "GITHUB_SHA": _git_head(),
        },
    )

    assert result.returncode == 1
    assert "GITHUB_RUN_ID must identify this workflow run" in result.stderr
    assert evidence["execution"]["context"] == "github-actions"
    assert evidence["failure"] == "invalid_github_run_id"


def test_github_actions_rejects_wrong_repository(tmp_path):
    result, evidence = _run_acceptance(
        tmp_path,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REPOSITORY": "attacker/fork",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": _git_head(),
        },
    )

    assert result.returncode == 1
    assert evidence["failure"] == "invalid_github_repository"


def test_workflow_dispatch_binds_exact_protected_main_checkout(tmp_path):
    result, evidence = _run_acceptance(
        tmp_path,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REPOSITORY": "szl-holdings/szl-build-env",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": _git_head(),
        },
    )

    assert result.returncode == 1
    assert evidence["failure"] == "deployment_missing"
    assert evidence["execution"]["context"] == "github-actions"
    assert evidence["execution"]["event_name"] == "workflow_dispatch"
    assert evidence["execution"]["ref"] == "refs/heads/main"
    assert evidence["execution"]["base_sha"] == _git_head()
    assert evidence["execution"]["candidate_sha"] == _git_head()


def test_pull_request_checkout_must_bind_exact_base_and_head_parents(tmp_path):
    parents = _pull_request_merge_parents()

    event = tmp_path / "event.json"
    _write_pull_request_event(event, parents[0], parents[1])
    result, evidence = _run_acceptance(
        tmp_path / "run",
        _pull_request_environment(event),
    )

    assert result.returncode == 1
    assert evidence["failure"] == "deployment_missing"
    assert evidence["execution"]["base_sha"] == parents[0]
    assert evidence["execution"]["candidate_sha"] == parents[1]


def test_pull_request_rejects_event_parent_mismatch(tmp_path):
    parents = _pull_request_merge_parents()
    event = tmp_path / "event.json"
    _write_pull_request_event(event, "0" * 40, parents[1])

    result, evidence = _run_acceptance(
        tmp_path / "run",
        _pull_request_environment(event),
    )

    assert result.returncode == 1
    assert "parents do not bind the exact base and candidate" in result.stderr
    assert evidence["failure"] == "github_pull_request_parent_mismatch"


def _run_route_validator(tmp_path, payload):
    response = tmp_path / "route-response"
    if isinstance(payload, bytes):
        response.write_bytes(payload)
    else:
        response.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return subprocess.run(
        [
            sys.executable,
            str(ROUTE_VALIDATOR),
            "--path",
            str(response),
            "--traceparent",
            VALID_TRACEPARENT,
            "--fanout",
            ",".join(VALID_FANOUT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_route_acknowledgement_requires_exact_json_trace_and_fanout(tmp_path):
    payload = {
        "accepted": True,
        "fanout": VALID_FANOUT,
        "schema": "szl.build-env.fanout-ack/v1",
        "traceparent": VALID_TRACEPARENT,
    }
    assert _run_route_validator(tmp_path, payload).returncode == 0


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>application shell</html>",
        b"{}",
        b'{"accepted":true,"accepted":true,"fanout":[],"schema":"szl.build-env.fanout-ack/v1","traceparent":"x"}',
        {"accepted": True, "fanout": VALID_FANOUT, "schema": "wrong", "traceparent": VALID_TRACEPARENT},
        {"accepted": True, "fanout": VALID_FANOUT, "schema": "szl.build-env.fanout-ack/v1", "traceparent": "00-" + "0" * 32 + "-" + "0" * 16 + "-01"},
        {"accepted": True, "fanout": VALID_FANOUT[:-1], "schema": "szl.build-env.fanout-ack/v1", "traceparent": VALID_TRACEPARENT},
        {"accepted": True, "fanout": VALID_FANOUT + ["extra"], "schema": "szl.build-env.fanout-ack/v1", "traceparent": VALID_TRACEPARENT},
        {"accepted": True, "fanout": ["sentra", "amaru", "amaru", "rosie"], "schema": "szl.build-env.fanout-ack/v1", "traceparent": VALID_TRACEPARENT},
        {"accepted": True, "fanout": list(reversed(VALID_FANOUT)), "schema": "szl.build-env.fanout-ack/v1", "traceparent": VALID_TRACEPARENT},
        {"accepted": True, "fanout": VALID_FANOUT, "schema": "szl.build-env.fanout-ack/v1", "traceparent": VALID_TRACEPARENT, "extra": True},
        b"\xff\xfe",
    ],
)
def test_route_acknowledgement_rejects_soft_or_unbound_evidence(tmp_path, payload):
    assert _run_route_validator(tmp_path, payload).returncode == 1



def _valid_jaeger_payload():
    services = ["a11oy", "sentra", "amaru", "killinchu", "rosie"]
    spans = []
    parent_id = VALID_ROOT_SPAN_ID
    for index, service in enumerate(services, start=1):
        span_id = f"{index:016x}"
        spans.append(
            {
                "duration": 100,
                "operationName": "governed-fanout",
                "process": {"serviceName": service},
                "references": [
                    {
                        "refType": "CHILD_OF",
                        "spanID": parent_id,
                        "traceID": VALID_TRACE_ID,
                    }
                ],
                "spanID": span_id,
                "startTime": VALID_SEED_EPOCH_US + index,
                "traceID": VALID_TRACE_ID,
            }
        )
        parent_id = span_id
    return {"data": [{"processes": {}, "spans": spans}]}


def _run_trace_validator(tmp_path, payload):
    response = tmp_path / "jaeger-response.json"
    observation = tmp_path / "trace-observation.json"
    if isinstance(payload, bytes):
        response.write_bytes(payload)
    else:
        response.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    result = subprocess.run(
        [
            sys.executable,
            str(TRACE_VALIDATOR),
            "--response",
            str(response),
            "--observation",
            str(observation),
            "--trace-id",
            VALID_TRACE_ID,
            "--root-span-id",
            VALID_ROOT_SPAN_ID,
            "--seed-epoch-us",
            str(VALID_SEED_EPOCH_US),
            "--services",
            "a11oy,sentra,amaru,killinchu,rosie",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert observation.is_file(), result.stderr
    return result, json.loads(observation.read_text(encoding="utf-8"))


def test_jaeger_trace_requires_fresh_connected_five_service_topology(tmp_path):
    result, observation = _run_trace_validator(tmp_path, _valid_jaeger_payload())

    assert result.returncode == 0
    assert result.stdout.strip() == "complete"
    assert observation["state"] == "complete"
    assert observation["fresh_span_count"] == 5
    assert observation["connected_services"] == ["a11oy", "amaru", "killinchu", "rosie", "sentra"]
    assert observation["missing_services"] == []


def test_jaeger_trace_rejects_disconnected_service_name_bag(tmp_path):
    payload = _valid_jaeger_payload()
    for span in payload["data"][0]["spans"]:
        span["references"] = []

    result, observation = _run_trace_validator(tmp_path, payload)

    assert result.returncode == 0
    assert result.stdout.strip() == "incomplete"
    assert observation["failure"] == "incomplete_connected_service_set"
    assert observation["observed_services"] == ["a11oy", "amaru", "killinchu", "rosie", "sentra"]
    assert observation["connected_services"] == []


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("stale", "span_precedes_trace_seed"),
        ("duplicate-span", "duplicate_span_id"),
        ("cross-trace", "cross_trace_reference"),
    ],
)
def test_jaeger_trace_rejects_stale_or_contradictory_graphs(tmp_path, mutation, failure):
    payload = _valid_jaeger_payload()
    spans = payload["data"][0]["spans"]
    if mutation == "stale":
        spans[0]["startTime"] = VALID_SEED_EPOCH_US - 1
    elif mutation == "duplicate-span":
        spans[-1]["spanID"] = spans[0]["spanID"]
    else:
        spans[2]["references"][0]["traceID"] = "0" * 32

    result, observation = _run_trace_validator(tmp_path, payload)

    assert result.returncode == 0
    assert result.stdout.strip() == "malformed"
    assert observation["failure"] == failure

def test_acceptance_script_invokes_strict_route_validator():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'validate_route_ack.py" \\' in source
    assert 'trace_failure="seed_response_invalid"' in source
    assert "falls back to /healthz" not in source
    assert "HEALTH_PATHS=(/healthz /healthz /healthz /api/killinchu/healthz /healthz)" in source
    assert 'validate_jaeger_trace.py" \\' in source
    assert '--root-span-id "$span_id"' in source
    assert '--seed-epoch-us "$trace_seed_epoch_us"' in source


def test_script_avoids_bash4_and_gnu_coreutils_only_primitives():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "declare -A" not in source
    assert "date +%s%3N" not in source
    assert "sha256sum" not in source
    assert "status --porcelain=v1 --untracked-files=all" in source
    assert "ACCEPTANCE_LOCAL_RUN_NONCE" not in source
    assert '"git", "cat-file", "commit", "HEAD"' in source
    assert "git show --no-patch --format=%P" not in source
    assert '"$source_repository" != "szl-holdings/szl-build-env"' in source
    assert '"$git_parents" != "${base_sha} ${candidate_sha}"' in source


def test_deployment_readiness_rejects_surge_and_stale_replicas():
    source = SCRIPT.read_text(encoding="utf-8")

    assert ".status.replicas" in source
    assert ".status.readyReplicas" in source
    assert "replicas == desired" in source
    assert "updated == desired" in source
    assert "ready == desired" in source
    assert "available == desired" in source


def test_script_parses_as_bash():
    bash = _usable_bash()
    if bash is None:
        pytest.skip("a runnable bash is required for the syntax contract test")
    subprocess.run([bash, "-n", str(SCRIPT)], cwd=ROOT, check=True)
