#!/usr/bin/env bash
# Fail-closed end-to-end acceptance for the five-organ runtime path.
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-szl}"
READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-300}"
READINESS_POLL_SECONDS="${READINESS_POLL_SECONDS:-5}"
PORT_FORWARD_TIMEOUT_SECONDS="${PORT_FORWARD_TIMEOUT_SECONDS:-20}"
HTTP_TIMEOUT_SECONDS="${HTTP_TIMEOUT_SECONDS:-15}"
TRACE_TIMEOUT_SECONDS="${TRACE_TIMEOUT_SECONDS:-90}"
TRACE_POLL_SECONDS="${TRACE_POLL_SECONDS:-4}"
EVIDENCE_PATH="${ACCEPTANCE_EVIDENCE_PATH:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/szl-build-env-acceptance.json}"

ORGANS=(a11oy sentra amaru killinchu rosie)
EXPECTED_SERVICES="a11oy,sentra,amaru,killinchu,rosie"
STATE_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/szl-acceptance.XXXXXX")"
TRACE_RESPONSE_PATH="${STATE_DIR}/trace-response.json"
TRACE_OBSERVATION_PATH="${STATE_DIR}/trace-observation.json"

deployment_exists=(false false false false false)
deployment_ready=(false false false false false)
health_http_code=("" "" "" "" "")
service_result=(FAIL FAIL FAIL FAIL FAIL)
service_failure=(not_evaluated not_evaluated not_evaluated not_evaluated not_evaluated)

github_sha="${GITHUB_SHA:-}"
github_run_id="${GITHUB_RUN_ID:-}"
github_run_attempt="${GITHUB_RUN_ATTEMPT:-}"
execution_context=""
local_run_nonce=""
run_identity=""
event_name=""
source_ref=""
source_repository=""
head_repository=""
base_sha=""
candidate_sha=""
overall_result="FAIL"
overall_failure="initialization_failed"
gate_failed=0
trace_id=""
traceparent=""
trace_seed_http_code=""
trace_result="FAIL"
trace_failure="not_run"
PF_PID=""

record_failure() {
  gate_failed=1
  if [[ -z "$overall_failure" ]]; then
    overall_failure="$1"
  fi
}

epoch_milliseconds() {
  python3 -c 'import time; print(time.time_ns() // 1_000_000)'
}

sha256_hex() {
  python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

remaining_milliseconds() {
  local deadline_ms="$1"
  local now_ms
  now_ms="$(epoch_milliseconds)"
  if (( now_ms >= deadline_ms )); then
    printf '0\n'
  else
    printf '%s\n' "$((deadline_ms - now_ms))"
  fi
}

milliseconds_as_seconds() {
  local milliseconds="$1"
  printf '%d.%03d\n' "$((milliseconds / 1000))" "$((milliseconds % 1000))"
}

sleep_milliseconds() {
  local milliseconds="$1"
  local duration
  (( milliseconds > 0 )) || return 0
  duration="$(milliseconds_as_seconds "$milliseconds")"
  sleep "$duration"
}

cleanup_port_forward() {
  if [[ -n "$PF_PID" ]]; then
    kill "$PF_PID" >/dev/null 2>&1 || true
    wait "$PF_PID" >/dev/null 2>&1 || true
    PF_PID=""
  fi
}

emit_evidence() {
  local service_state_path="${STATE_DIR}/services.tsv"
  local organ organ_index

  : > "$service_state_path"
  for organ_index in "${!ORGANS[@]}"; do
    organ="${ORGANS[$organ_index]}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$organ" \
      "${deployment_exists[$organ_index]}" \
      "${deployment_ready[$organ_index]}" \
      "${health_http_code[$organ_index]}" \
      "${service_result[$organ_index]}" \
      "${service_failure[$organ_index]}" >> "$service_state_path"
  done

  EVIDENCE_PATH="$EVIDENCE_PATH" \
  SERVICE_STATE_PATH="$service_state_path" \
  TRACE_OBSERVATION_PATH="$TRACE_OBSERVATION_PATH" \
  EXPECTED_SERVICES="$EXPECTED_SERVICES" \
  NAMESPACE="$NAMESPACE" \
  EVIDENCE_GITHUB_SHA="$github_sha" \
  EVIDENCE_GITHUB_RUN_ID="$github_run_id" \
  EVIDENCE_GITHUB_RUN_ATTEMPT="$github_run_attempt" \
  EVIDENCE_EXECUTION_CONTEXT="$execution_context" \
  EVIDENCE_LOCAL_RUN_NONCE="$local_run_nonce" \
  EVIDENCE_EVENT_NAME="$event_name" \
  EVIDENCE_SOURCE_REF="$source_ref" \
  EVIDENCE_SOURCE_REPOSITORY="$source_repository" \
  EVIDENCE_HEAD_REPOSITORY="$head_repository" \
  EVIDENCE_BASE_SHA="$base_sha" \
  EVIDENCE_CANDIDATE_SHA="$candidate_sha" \
  EVIDENCE_RESULT="$overall_result" \
  EVIDENCE_FAILURE="$overall_failure" \
  EVIDENCE_TRACE_ID="$trace_id" \
  EVIDENCE_TRACEPARENT="$traceparent" \
  EVIDENCE_TRACE_SEED_HTTP_CODE="$trace_seed_http_code" \
  EVIDENCE_TRACE_RESULT="$trace_result" \
  EVIDENCE_TRACE_FAILURE="$trace_failure" \
  EVIDENCE_READINESS_TIMEOUT="$READINESS_TIMEOUT_SECONDS" \
  EVIDENCE_TRACE_TIMEOUT="$TRACE_TIMEOUT_SECONDS" \
  python3 - <<'PY'
import json
import os
from pathlib import Path


def optional_http_code(raw):
    if not raw or raw == "000":
        return None
    return int(raw)


def optional_positive_integer(raw):
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


expected = os.environ["EXPECTED_SERVICES"].split(",")
services = []
with open(os.environ["SERVICE_STATE_PATH"], encoding="utf-8") as state_file:
    for line in state_file:
        name, exists, ready, http_code, result, failure = line.rstrip("\n").split("\t")
        services.append(
            {
                "deployment_exists": exists == "true",
                "deployment_ready": ready == "true",
                "failure": None if not failure else failure,
                "health_http_code": optional_http_code(http_code),
                "name": name,
                "result": result,
            }
        )

trace_observation = {
    "failure": None,
    "missing_services": expected,
    "observed_services": [],
    "span_count": 0,
    "state": "not_observed",
}
observation_path = Path(os.environ["TRACE_OBSERVATION_PATH"])
if observation_path.is_file():
    try:
        with observation_path.open(encoding="utf-8") as observation_file:
            trace_observation = json.load(observation_file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        trace_observation["failure"] = "invalid_internal_trace_observation"
        trace_observation["state"] = "malformed"

evidence = {
    "execution": {
        "base_sha": os.environ["EVIDENCE_BASE_SHA"],
        "candidate_sha": os.environ["EVIDENCE_CANDIDATE_SHA"],
        "context": os.environ["EVIDENCE_EXECUTION_CONTEXT"],
        "event_name": os.environ["EVIDENCE_EVENT_NAME"],
        "head_repository": os.environ["EVIDENCE_HEAD_REPOSITORY"],
        "local_run_nonce": os.environ["EVIDENCE_LOCAL_RUN_NONCE"] or None,
        "ref": os.environ["EVIDENCE_SOURCE_REF"],
        "repository": os.environ["EVIDENCE_SOURCE_REPOSITORY"],
    },
    "failure": os.environ["EVIDENCE_FAILURE"] or None,
    "github_run_attempt": optional_positive_integer(
        os.environ["EVIDENCE_GITHUB_RUN_ATTEMPT"]
    ),
    "github_run_id": os.environ["EVIDENCE_GITHUB_RUN_ID"] or None,
    "github_sha": os.environ["EVIDENCE_GITHUB_SHA"],
    "namespace": os.environ["NAMESPACE"],
    "parameters": {
        "readiness_timeout_seconds": optional_positive_integer(
            os.environ["EVIDENCE_READINESS_TIMEOUT"]
        ),
        "trace_timeout_seconds": optional_positive_integer(
            os.environ["EVIDENCE_TRACE_TIMEOUT"]
        ),
    },
    "result": os.environ["EVIDENCE_RESULT"],
    "schema": "https://szl.dev/schemas/build-env-acceptance/v1",
    "services": services,
    "trace": {
        "failure": os.environ["EVIDENCE_TRACE_FAILURE"] or None,
        "missing_services": trace_observation.get("missing_services", expected),
        "observation_failure": trace_observation.get("failure"),
        "observed_services": trace_observation.get("observed_services", []),
        "result": os.environ["EVIDENCE_TRACE_RESULT"],
        "seed_http_code": optional_http_code(
            os.environ["EVIDENCE_TRACE_SEED_HTTP_CODE"]
        ),
        "span_count": trace_observation.get("span_count", 0),
        "state": trace_observation.get("state", "not_observed"),
        "trace_id": os.environ["EVIDENCE_TRACE_ID"] or None,
        "traceparent": os.environ["EVIDENCE_TRACEPARENT"] or None,
    },
}

output = Path(os.environ["EVIDENCE_PATH"])
output.parent.mkdir(parents=True, exist_ok=True)
temporary_output = output.with_name(output.name + ".tmp")
with temporary_output.open("w", encoding="utf-8", newline="\n") as evidence_file:
    json.dump(evidence, evidence_file, indent=2, sort_keys=True)
    evidence_file.write("\n")
os.replace(temporary_output, output)
PY
}

finalize() {
  local exit_code=$?
  local evidence_exit=0
  trap - EXIT
  set +e
  cleanup_port_forward
  emit_evidence
  evidence_exit=$?
  if (( evidence_exit != 0 )); then
    echo "[FAIL] could not emit acceptance evidence to ${EVIDENCE_PATH}" >&2
    exit_code=1
  else
    echo "Acceptance evidence: ${EVIDENCE_PATH}"
  fi
  rm -rf -- "$STATE_DIR"
  exit "$exit_code"
}
trap finalize EXIT

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    overall_failure="invalid_${name}"
    echo "[FAIL] ${name} must be a positive integer, got: ${value}" >&2
    exit 1
  fi
}

derive_execution_identity() {
  local checkout_sha event_identity event_path git_parents head_ref pr_number script_dir source_repo status_output

  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    execution_context="github-actions"
    if [[ ! "$github_run_id" =~ ^[1-9][0-9]*$ ]]; then
      overall_failure="invalid_github_run_id"
      echo "[FAIL] GITHUB_RUN_ID must identify this workflow run" >&2
      exit 1
    fi
    if [[ ! "$github_run_attempt" =~ ^[1-9][0-9]*$ ]]; then
      overall_failure="invalid_github_run_attempt"
      echo "[FAIL] GITHUB_RUN_ATTEMPT must identify this workflow attempt" >&2
      exit 1
    fi
    run_identity="${github_run_id}:${github_run_attempt}"
    source_repository="${GITHUB_REPOSITORY:-}"
    event_name="${GITHUB_EVENT_NAME:-}"
    source_ref="${GITHUB_REF:-}"
    if [[ "$source_repository" != "szl-holdings/szl-build-env" ]]; then
      overall_failure="invalid_github_repository"
      echo "[FAIL] GITHUB_REPOSITORY must be szl-holdings/szl-build-env" >&2
      exit 1
    fi
    checkout_sha="$(git rev-parse --verify HEAD 2>/dev/null || true)"
    if [[ "$checkout_sha" != "$github_sha" ]]; then
      overall_failure="github_checkout_sha_mismatch"
      echo "[FAIL] checked-out commit does not equal GITHUB_SHA" >&2
      exit 1
    fi

    case "$event_name" in
      pull_request)
        event_path="${GITHUB_EVENT_PATH:-}"
        if [[ ! -f "$event_path" ]]; then
          overall_failure="missing_github_event_payload"
          echo "[FAIL] pull_request evidence requires GITHUB_EVENT_PATH" >&2
          exit 1
        fi
        if ! event_identity="$(python3 - "$event_path" <<'PY'
import json
import re
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit("invalid pull_request event payload")

pull_request = payload.get("pull_request")
if not isinstance(pull_request, dict) or pull_request.get("state") != "open":
    raise SystemExit("pull request must be open")
if payload.get("action") not in {"opened", "reopened", "synchronize"}:
    raise SystemExit("unsupported pull_request action")
number = payload.get("number")
base = pull_request.get("base")
head = pull_request.get("head")
if not isinstance(number, int) or number <= 0:
    raise SystemExit("invalid pull request number")
if not isinstance(base, dict) or not isinstance(head, dict):
    raise SystemExit("missing pull request base or head")
base_repo = base.get("repo")
head_repo = head.get("repo")
if not isinstance(base_repo, dict) or base_repo.get("full_name") != "szl-holdings/szl-build-env":
    raise SystemExit("invalid pull request base repository")
if base.get("ref") != "main":
    raise SystemExit("invalid pull request base ref")
if not isinstance(head_repo, dict) or not isinstance(head_repo.get("full_name"), str):
    raise SystemExit("invalid pull request head repository")
base_sha = base.get("sha")
head_sha = head.get("sha")
head_ref = head.get("ref")
if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
    raise SystemExit("invalid pull request base sha")
if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
    raise SystemExit("invalid pull request head sha")
if not isinstance(head_ref, str) or not head_ref:
    raise SystemExit("invalid pull request head ref")
print("|".join((str(number), base_sha, head_sha, head_repo["full_name"], head_ref)))
PY
        )"; then
          overall_failure="invalid_github_event_payload"
          echo "[FAIL] pull_request event payload failed structural validation" >&2
          exit 1
        fi
        IFS='|' read -r pr_number base_sha candidate_sha head_repository head_ref <<< "$event_identity"
        if [[ "$source_ref" != "refs/pull/${pr_number}/merge" || "${GITHUB_BASE_REF:-}" != "main" || "${GITHUB_HEAD_REF:-}" != "$head_ref" ]]; then
          overall_failure="github_pull_request_ref_mismatch"
          echo "[FAIL] GitHub pull request refs do not match the event payload" >&2
          exit 1
        fi
        if ! git_parents="$(python3 - <<'PY'
import re
import subprocess


raw_commit = subprocess.run(
    ["git", "cat-file", "commit", "HEAD"],
    check=True,
    stdout=subprocess.PIPE,
).stdout
headers, separator, _message = raw_commit.partition(b"\n\n")
if not separator:
    raise SystemExit("commit object has no header/message boundary")

parents = []
for line in headers.splitlines():
    if not line.startswith(b"parent "):
        continue
    parent = line[len(b"parent ") :]
    if not re.fullmatch(rb"[0-9a-f]{40}", parent):
        raise SystemExit("commit object has a malformed parent header")
    parents.append(parent.decode("ascii"))

if len(parents) != 2:
    raise SystemExit("pull_request checkout must be an exact two-parent merge commit")
print(" ".join(parents))
PY
        )"; then
          overall_failure="invalid_github_merge_commit"
          echo "[FAIL] checked-out pull request commit object is not a valid two-parent merge" >&2
          exit 1
        fi
        if [[ "$git_parents" != "${base_sha} ${candidate_sha}" ]]; then
          overall_failure="github_pull_request_parent_mismatch"
          echo "[FAIL] checked-out merge commit parents do not bind the exact base and candidate" >&2
          exit 1
        fi
        ;;
      workflow_dispatch)
        if [[ "$source_ref" != "refs/heads/main" || -n "${GITHUB_BASE_REF:-}" || -n "${GITHUB_HEAD_REF:-}" ]]; then
          overall_failure="invalid_workflow_dispatch_ref"
          echo "[FAIL] workflow_dispatch acceptance is restricted to protected main" >&2
          exit 1
        fi
        base_sha="$github_sha"
        candidate_sha="$github_sha"
        head_repository="$source_repository"
        ;;
      *)
        overall_failure="invalid_github_event_name"
        echo "[FAIL] acceptance supports only pull_request and workflow_dispatch events" >&2
        exit 1
        ;;
    esac
  else
    execution_context="local"
    script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
    source_repo="${script_dir}/.."
    github_sha="$(git -C "$source_repo" rev-parse --verify HEAD 2>/dev/null || true)"
    github_run_id=""
    github_run_attempt=""
    event_name="local"
    source_repository="local"
    head_repository="local"
    source_ref="$(git -C "$source_repo" symbolic-ref --quiet --short HEAD 2>/dev/null || printf 'detached')"
    base_sha="$github_sha"
    candidate_sha="$github_sha"
    local_run_nonce="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
    if [[ ! "$local_run_nonce" =~ ^[0-9a-f]{32}$ ]]; then
      overall_failure="invalid_local_run_nonce"
      echo "[FAIL] locally generated run identity must be exactly 32 lowercase hex characters" >&2
      exit 1
    fi
    if ! status_output="$(git -C "$source_repo" status --porcelain=v1 --untracked-files=all 2>/dev/null)"; then
      overall_failure="source_status_unavailable"
      echo "[FAIL] local source status could not be established" >&2
      exit 1
    fi
    if [[ -n "$status_output" ]]; then
      overall_failure="dirty_source_tree"
      echo "[FAIL] local acceptance evidence requires a clean tracked and untracked source tree" >&2
      exit 1
    fi
    run_identity="$local_run_nonce"
  fi

  if [[ ! "$github_sha" =~ ^[0-9a-f]{40}$ ]]; then
    overall_failure="invalid_github_sha"
    echo "[FAIL] source identity must be an exact lowercase 40-character Git commit SHA" >&2
    exit 1
  fi
}

deployment_is_ready() {
  local organ="$1"
  local deadline_ms="$2"
  local remaining_ms raw generation observed desired replicas updated ready available unavailable
  remaining_ms="$(remaining_milliseconds "$deadline_ms")"
  (( remaining_ms > 0 )) || return 2
  if ! raw="$(kubectl --request-timeout="${remaining_ms}ms" -n "$NAMESPACE" get deployment "$organ" \
    -o jsonpath='{.metadata.generation}{"|"}{.status.observedGeneration}{"|"}{.spec.replicas}{"|"}{.status.replicas}{"|"}{.status.updatedReplicas}{"|"}{.status.readyReplicas}{"|"}{.status.availableReplicas}{"|"}{.status.unavailableReplicas}')"; then
    if (( $(remaining_milliseconds "$deadline_ms") == 0 )); then
      return 2
    fi
    return 1
  fi
  IFS='|' read -r generation observed desired replicas updated ready available unavailable <<< "$raw"
  replicas="${replicas:-0}"
  updated="${updated:-0}"
  ready="${ready:-0}"
  available="${available:-0}"
  unavailable="${unavailable:-0}"
  [[ "$generation" =~ ^[0-9]+$ ]] || return 1
  [[ "$observed" =~ ^[0-9]+$ ]] || return 1
  [[ "$desired" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$replicas" =~ ^[0-9]+$ ]] || return 1
  [[ "$updated" =~ ^[0-9]+$ ]] || return 1
  [[ "$ready" =~ ^[0-9]+$ ]] || return 1
  [[ "$available" =~ ^[0-9]+$ ]] || return 1
  [[ "$unavailable" =~ ^[0-9]+$ ]] || return 1
  (( observed >= generation && replicas == desired && updated == desired && ready == desired && available == desired && unavailable == 0 ))
}

image_pull_failure() {
  local organ="$1"
  local deadline_ms="$2"
  local remaining_ms reasons
  remaining_ms="$(remaining_milliseconds "$deadline_ms")"
  (( remaining_ms > 0 )) || return 2
  if ! reasons="$(kubectl --request-timeout="${remaining_ms}ms" -n "$NAMESPACE" get pods -l "app=${organ}" \
    -o jsonpath='{range .items[*]}{range .status.initContainerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null)"; then
    if (( $(remaining_milliseconds "$deadline_ms") == 0 )); then
      return 2
    fi
    return 1
  fi
  grep -Eq '^(ErrImagePull|ImagePullBackOff|ErrImageNeverPull|InvalidImageName|RegistryUnavailable|CreateContainerConfigError)$' <<< "$reasons"
}

mark_readiness_timeout() {
  local organ organ_index
  for organ_index in "${!ORGANS[@]}"; do
    organ="${ORGANS[$organ_index]}"
    if [[ "${deployment_ready[$organ_index]}" != "true" ]]; then
      service_failure[$organ_index]="deployment_readiness_timeout"
      echo "   [FAIL] ${organ}: readiness deadline exceeded" >&2
    fi
  done
  record_failure "deployment_readiness_timeout"
}

start_port_forward() {
  local resource="$1"
  local local_port="$2"
  local remote_port="$3"
  local log_path="$4"
  local deadline

  kubectl -n "$NAMESPACE" port-forward --address 127.0.0.1 \
    "$resource" "${local_port}:${remote_port}" > "$log_path" 2>&1 &
  PF_PID=$!
  deadline=$(( $(date +%s) + PORT_FORWARD_TIMEOUT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if grep -Fq "Forwarding from 127.0.0.1:${local_port}" "$log_path"; then
      return 0
    fi
    if ! kill -0 "$PF_PID" >/dev/null 2>&1; then
      return 1
    fi
    sleep 1
  done
  return 1
}

stop_port_forward() {
  cleanup_port_forward
}

parse_trace_response() {
  TRACE_RESPONSE_PATH="$TRACE_RESPONSE_PATH" \
  TRACE_OBSERVATION_PATH="$TRACE_OBSERVATION_PATH" \
  EXPECTED_SERVICES="$EXPECTED_SERVICES" \
  EXPECTED_TRACE_ID="$trace_id" \
  python3 - <<'PY'
import json
import os
import re
from pathlib import Path

response_path = Path(os.environ["TRACE_RESPONSE_PATH"])
observation_path = Path(os.environ["TRACE_OBSERVATION_PATH"])
expected = os.environ["EXPECTED_SERVICES"].split(",")
expected_set = set(expected)
trace_id = os.environ["EXPECTED_TRACE_ID"]


def finish(state, spans=0, services=(), failure=None):
    observed = sorted(set(services))
    payload = {
        "failure": failure,
        "missing_services": [name for name in expected if name not in observed],
        "observed_services": observed,
        "span_count": spans,
        "state": state,
    }
    with observation_path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, separators=(",", ":"), sort_keys=True)
        output.write("\n")
    print(state)
    raise SystemExit(0)


try:
    with response_path.open(encoding="utf-8") as response_file:
        payload = json.load(response_file)
except (OSError, UnicodeError, json.JSONDecodeError):
    finish("malformed", failure="malformed_jaeger_json")

if not isinstance(payload, dict) or "data" not in payload:
    finish("malformed", failure="malformed_jaeger_envelope")
traces = payload["data"]
if not isinstance(traces, list):
    finish("malformed", failure="malformed_jaeger_data")
if not traces:
    finish("absent", failure="trace_not_found")

matching = []
for trace in traces:
    if not isinstance(trace, dict) or not isinstance(trace.get("spans"), list):
        finish("malformed", failure="malformed_jaeger_trace")
    span_ids = {
        span.get("traceID", "").lower()
        for span in trace["spans"]
        if isinstance(span, dict)
    }
    if trace_id in span_ids:
        matching.append(trace)

if len(matching) != 1:
    finish("malformed", failure="trace_identity_mismatch")

trace = matching[0]
spans = trace["spans"]
if not spans:
    finish("malformed", failure="trace_has_no_spans")
processes = trace.get("processes", {})
if not isinstance(processes, dict):
    finish("malformed", failure="malformed_jaeger_processes")

services = set()
for span in spans:
    if not isinstance(span, dict):
        finish("malformed", failure="malformed_jaeger_span")
    span_trace_id = span.get("traceID")
    if not isinstance(span_trace_id, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", span_trace_id):
        finish("malformed", failure="malformed_span_trace_id")
    if span_trace_id.lower() != trace_id:
        finish("malformed", failure="contradictory_span_trace_id")

    process = span.get("process")
    if process is None:
        process_id = span.get("processID")
        process = processes.get(process_id) if isinstance(process_id, str) else None
    if not isinstance(process, dict):
        finish("malformed", failure="missing_span_process")
    service_name = process.get("serviceName")
    if not isinstance(service_name, str) or not service_name.strip():
        finish("malformed", failure="missing_service_identity")
    services.add(service_name.strip())

if expected_set <= services:
    finish("complete", spans=len(spans), services=services)
finish(
    "incomplete",
    spans=len(spans),
    services=services,
    failure="incomplete_service_set",
)
PY
}

require_positive_integer "readiness_timeout_seconds" "$READINESS_TIMEOUT_SECONDS"
require_positive_integer "readiness_poll_seconds" "$READINESS_POLL_SECONDS"
require_positive_integer "port_forward_timeout_seconds" "$PORT_FORWARD_TIMEOUT_SECONDS"
require_positive_integer "http_timeout_seconds" "$HTTP_TIMEOUT_SECONDS"
require_positive_integer "trace_timeout_seconds" "$TRACE_TIMEOUT_SECONDS"
require_positive_integer "trace_poll_seconds" "$TRACE_POLL_SECONDS"

overall_failure=""
derive_execution_identity

trace_id="$(printf 'szl-build-env-acceptance-trace-v1\0%s\0%s\0%s' \
  "$github_sha" "$execution_context" "$run_identity" | sha256_hex | cut -c1-32)"
span_id="$(printf 'szl-build-env-acceptance-span-v1\0%s\0%s\0%s' \
  "$github_sha" "$execution_context" "$run_identity" | sha256_hex | cut -c1-16)"
traceparent="00-${trace_id}-${span_id}-01"

readiness_deadline_ms=$(( $(epoch_milliseconds) + READINESS_TIMEOUT_SECONDS * 1000 ))

echo "==> 1. Require all five deployments"
missing_deployment=0
deployment_existence_timeout=0
for organ_index in "${!ORGANS[@]}"; do
  organ="${ORGANS[$organ_index]}"
  remaining_ms="$(remaining_milliseconds "$readiness_deadline_ms")"
  if (( remaining_ms == 0 )); then
    deployment_existence_timeout=1
    service_failure[$organ_index]="deployment_existence_timeout"
    echo "   [FAIL] ${organ}: deployment lookup deadline exceeded" >&2
  elif kubectl --request-timeout="${remaining_ms}ms" -n "$NAMESPACE" get deployment "$organ" >/dev/null 2>&1; then
    deployment_exists[$organ_index]="true"
    service_failure[$organ_index]="deployment_not_ready"
    echo "   [OK] ${organ}: deployment exists"
  elif (( $(remaining_milliseconds "$readiness_deadline_ms") == 0 )); then
    deployment_existence_timeout=1
    service_failure[$organ_index]="deployment_existence_timeout"
    echo "   [FAIL] ${organ}: deployment lookup deadline exceeded" >&2
  else
    missing_deployment=1
    service_failure[$organ_index]="deployment_missing"
    echo "   [FAIL] ${organ}: deployment missing" >&2
  fi
done

if (( missing_deployment != 0 )); then
  record_failure "deployment_missing"
fi
if (( deployment_existence_timeout != 0 )); then
  record_failure "deployment_existence_timeout"
fi

if (( missing_deployment != 0 || deployment_existence_timeout != 0 )); then
  :
else
  echo "==> 2. Wait for every deployment to be fully ready"
  while :; do
    remaining_ms="$(remaining_milliseconds "$readiness_deadline_ms")"
    if (( remaining_ms == 0 )); then
      mark_readiness_timeout
      break
    fi

    pending=0
    pull_failure=0
    deadline_exhausted=0
    for organ_index in "${!ORGANS[@]}"; do
      organ="${ORGANS[$organ_index]}"
      if deployment_is_ready "$organ" "$readiness_deadline_ms"; then
        deployment_ready[$organ_index]="true"
        service_failure[$organ_index]="health_not_evaluated"
      else
        readiness_status=$?
        if (( readiness_status == 2 )); then
          deadline_exhausted=1
          break
        fi
        pending=1
        deployment_ready[$organ_index]="false"
        if image_pull_failure "$organ" "$readiness_deadline_ms"; then
          pull_failure=1
          service_failure[$organ_index]="image_pull_failure"
          echo "   [FAIL] ${organ}: image pull or registry credential failure" >&2
        else
          image_pull_status=$?
          if (( image_pull_status == 2 )); then
            deadline_exhausted=1
            break
          fi
          service_failure[$organ_index]="deployment_not_ready"
        fi
      fi
    done

    if (( deadline_exhausted != 0 )); then
      mark_readiness_timeout
      break
    fi
    if (( pull_failure != 0 )); then
      record_failure "image_pull_failure"
      break
    fi
    if (( pending == 0 )); then
      echo "   [OK] all five deployments are fully ready"
      break
    fi

    remaining_ms="$(remaining_milliseconds "$readiness_deadline_ms")"
    if (( remaining_ms == 0 )); then
      mark_readiness_timeout
      break
    fi
    sleep_ms=$((READINESS_POLL_SECONDS * 1000))
    if (( sleep_ms > remaining_ms )); then
      sleep_ms="$remaining_ms"
    fi
    sleep_milliseconds "$sleep_ms"
  done
fi

all_deployments_ready=1
for organ_index in "${!ORGANS[@]}"; do
  if [[ "${deployment_exists[$organ_index]}" != "true" || "${deployment_ready[$organ_index]}" != "true" ]]; then
    all_deployments_ready=0
  fi
done

if (( all_deployments_ready != 0 )); then
  echo "==> 3. Require HTTP 200 from every /healthz endpoint"
  health_port=18080
  for organ_index in "${!ORGANS[@]}"; do
    organ="${ORGANS[$organ_index]}"
    pf_log="${STATE_DIR}/health-${organ}-port-forward.log"
    if ! start_port_forward "service/${organ}" "$health_port" 8080 "$pf_log"; then
      service_failure[$organ_index]="health_port_forward_failed"
      record_failure "health_probe_failed"
      echo "   [FAIL] ${organ}: could not establish health port-forward" >&2
      stop_port_forward
      health_port=$((health_port + 1))
      continue
    fi

    health_body="${STATE_DIR}/health-${organ}.body"
    if health_code="$(curl --silent --show-error --output "$health_body" \
      --write-out '%{http_code}' --connect-timeout "$HTTP_TIMEOUT_SECONDS" \
      --max-time "$HTTP_TIMEOUT_SECONDS" "http://127.0.0.1:${health_port}/healthz")"; then
      health_http_code[$organ_index]="$health_code"
      if [[ "$health_code" == "200" ]]; then
        service_result[$organ_index]="PASS"
        service_failure[$organ_index]=""
        echo "   [OK] ${organ}: /healthz returned 200"
      else
        service_failure[$organ_index]="health_http_${health_code}"
        record_failure "health_probe_failed"
        echo "   [FAIL] ${organ}: /healthz returned ${health_code}" >&2
      fi
    else
      health_http_code[$organ_index]=""
      service_failure[$organ_index]="health_unreachable"
      record_failure "health_probe_failed"
      echo "   [FAIL] ${organ}: /healthz was unreachable" >&2
    fi
    stop_port_forward
    health_port=$((health_port + 1))
  done

  echo "==> 4. Inject one exact traceparent through the five-organ route"
  route_pf_log="${STATE_DIR}/route-port-forward.log"
  if start_port_forward "service/a11oy" 18090 8080 "$route_pf_log"; then
    if route_code="$(curl --silent --show-error --output "${STATE_DIR}/route.body" \
      --write-out '%{http_code}' --connect-timeout "$HTTP_TIMEOUT_SECONDS" \
      --max-time "$HTTP_TIMEOUT_SECONDS" -H "traceparent: ${traceparent}" \
      'http://127.0.0.1:18090/route?fanout=sentra,amaru,killinchu,rosie')"; then
      trace_seed_http_code="$route_code"
      if [[ "$route_code" != "200" ]]; then
        trace_failure="seed_request_http_${route_code}"
        record_failure "trace_seed_failed"
        echo "   [FAIL] trace seed returned HTTP ${route_code}" >&2
      else
        trace_failure="trace_not_observed"
        echo "   [OK] injected ${traceparent}"
      fi
    else
      trace_seed_http_code=""
      trace_failure="seed_request_unreachable"
      record_failure "trace_seed_failed"
      echo "   [FAIL] trace seed route was unreachable" >&2
    fi
    stop_port_forward
  else
    trace_failure="seed_port_forward_failed"
    record_failure "trace_seed_failed"
    echo "   [FAIL] could not establish a11oy trace-seed port-forward" >&2
    stop_port_forward
  fi

  echo "==> 5. Require Jaeger to return the exact trace with all five services"
  jaeger_pf_log="${STATE_DIR}/jaeger-port-forward.log"
  if start_port_forward "service/jaeger" 16686 16686 "$jaeger_pf_log"; then
    trace_deadline_ms=$(( $(epoch_milliseconds) + TRACE_TIMEOUT_SECONDS * 1000 ))
    last_trace_state="unreachable"
    while :; do
      remaining_ms="$(remaining_milliseconds "$trace_deadline_ms")"
      if (( remaining_ms == 0 )); then
        case "$last_trace_state" in
          absent) trace_failure="trace_not_found_before_deadline" ;;
          incomplete) trace_failure="trace_incomplete_before_deadline" ;;
          *) trace_failure="jaeger_unreachable_before_deadline" ;;
        esac
        record_failure "$trace_failure"
        echo "   [FAIL] trace deadline exceeded: ${trace_failure}" >&2
        break
      fi
      jaeger_timeout="$(milliseconds_as_seconds "$remaining_ms")"
      if jaeger_code="$(curl --silent --show-error --output "$TRACE_RESPONSE_PATH" \
        --write-out '%{http_code}' --connect-timeout "$jaeger_timeout" \
        --max-time "$jaeger_timeout" \
        "http://127.0.0.1:16686/api/traces/${trace_id}")"; then
        if [[ "$jaeger_code" == "200" ]]; then
          if last_trace_state="$(parse_trace_response)"; then
            case "$last_trace_state" in
              complete)
                trace_result="PASS"
                trace_failure=""
                echo "   [OK] Jaeger returned all five service identities"
                break
                ;;
              malformed)
                trace_failure="malformed_jaeger_trace"
                record_failure "malformed_jaeger_trace"
                echo "   [FAIL] Jaeger returned malformed or contradictory trace data" >&2
                break
                ;;
              absent|incomplete)
                ;;
              *)
                trace_failure="trace_parser_invalid_state"
                record_failure "trace_parser_failed"
                echo "   [FAIL] trace parser returned an invalid state" >&2
                break
                ;;
            esac
          else
            trace_failure="trace_parser_failed"
            record_failure "trace_parser_failed"
            echo "   [FAIL] trace parser could not evaluate Jaeger data" >&2
            break
          fi
        else
          last_trace_state="unreachable"
          trace_failure="jaeger_http_${jaeger_code}"
        fi
      else
        last_trace_state="unreachable"
        trace_failure="jaeger_unreachable"
      fi

      remaining_ms="$(remaining_milliseconds "$trace_deadline_ms")"
      if (( remaining_ms == 0 )); then
        case "$last_trace_state" in
          absent) trace_failure="trace_not_found_before_deadline" ;;
          incomplete) trace_failure="trace_incomplete_before_deadline" ;;
          *) trace_failure="jaeger_unreachable_before_deadline" ;;
        esac
        record_failure "$trace_failure"
        echo "   [FAIL] trace deadline exceeded: ${trace_failure}" >&2
        break
      fi
      sleep_ms=$((TRACE_POLL_SECONDS * 1000))
      if (( sleep_ms > remaining_ms )); then
        sleep_ms="$remaining_ms"
      fi
      sleep_milliseconds "$sleep_ms"
    done
    stop_port_forward
  else
    trace_failure="jaeger_port_forward_failed"
    record_failure "jaeger_port_forward_failed"
    echo "   [FAIL] could not establish Jaeger port-forward" >&2
    stop_port_forward
  fi
else
  trace_failure="not_run_deployments_unready"
  record_failure "deployment_gate_failed"
  echo "[FAIL] health and trace checks require all five ready deployments" >&2
fi

if (( gate_failed == 0 )); then
  overall_result="PASS"
  overall_failure=""
  echo "[PASS] five deployments, five health probes, and one five-service trace verified"
  exit 0
fi

overall_result="FAIL"
echo "[FAIL] five-organ acceptance failed: ${overall_failure}" >&2
exit 1
