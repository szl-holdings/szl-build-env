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


def test_script_parses_as_bash():
    bash = _usable_bash()
    if bash is None:
        pytest.skip("a runnable bash is required for the syntax contract test")
    subprocess.run([bash, "-n", str(SCRIPT)], cwd=ROOT, check=True)
