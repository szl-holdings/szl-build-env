"""Local HTTP fixtures exercise the actual verifier transport and fail-closed CLI."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_acceptance as verifier

REVISION = "a" * 40
TOKEN = "fixture-secret-do-not-record"


def fixture_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source():
    body = {"schema": "szl.router-source/v1", "repository": "szl-holdings/szl-router",
            "revision": REVISION, "controlled_files": {name: "b" * 64 for name in verifier.SOURCE_FILES},
            "default_egress": False, "secret_output": False, "arbitrary_url_routing": False}
    return {**body, "receipt": {"algorithm": "sha256", "digest": fixture_digest(body)}}


def completion(request):
    # Model the service's Pydantic normalization independently of the CLI.
    normalized = {"temperature": None, "top_p": None, "max_tokens": None,
                  "stream": False, "stop": None, "user": None,
                  "data_classification": "public", "max_cost_tier": 10, **request}
    normalized["messages"] = [{"name": None, **message} for message in request["messages"]]
    answer = {"id": "fixture", "object": "chat.completion", "model": "fixture-upstream",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "ROUTER_ACCEPTANCE_OK ✓"},
                           "finish_reason": "stop"}]}
    receipt = {"schema": "szl.router-receipt/v1", "request_digest": fixture_digest(normalized),
               "plan_digest": "c" * 64, "provider_id": "fixture", "public_model": "test-model",
               "upstream_model": "fixture-upstream", "classification": "public",
               "attempts": [{"provider_id": "fixture", "state": "SUCCESS", "status_code": 200}],
               "elapsed_ms": 1.0, "response_digest": fixture_digest(answer), "secret_material_recorded": False}
    return {**answer, "szl_receipt": {**receipt, "algorithm": "sha256", "digest": fixture_digest(receipt)}}


def resign_receipt(response):
    receipt = response["szl_receipt"]
    receipt["digest"] = fixture_digest({key: value for key, value in receipt.items()
                                       if key not in {"algorithm", "digest"}})


@pytest.fixture
def router(monkeypatch):
    state = {
        "requests": [], "mutate_completion": None, "source_reads": 0, "source_drift": False,
        "responses": {
            "/api/source": (200, source()),
            "/readyz/inference": (200, {"status": "configured", "ready_for_requests": True,
                "checks": {"registry_valid": True, "egress_enabled": True,
                           "caller_auth_configured": True, "credentialed_provider": True},
                "basis": "LOCAL_CONFIGURATION_ONLY", "provider_reachability": "UNVERIFIED",
                "inference_witness": "UNAVAILABLE"}),
            "/v1/models": (200, {"object": "list", "szl_registry_state": "VALIDATED",
                "data": [{"id": "test-model", "object": "model", "owned_by": "szl-router-registry"}]}),
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, value):
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", state.get("cache_control", "no-store"))
            if isinstance(value, dict) and "szl_receipt" in value:
                self.send_header("X-SZL-Receipt", state.get("receipt_header", value["szl_receipt"]["digest"]))
            if 300 <= status < 400:
                self.send_header("Location", "/redirect-was-followed")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            state["requests"].append(("GET", self.path, bool(self.headers.get("Authorization"))))
            if self.path == "/api/source":
                state["source_reads"] += 1
                if state["source_drift"] and state["source_reads"] > 1:
                    altered = source()
                    altered["controlled_files"]["router_control/app.py"] = "d" * 64
                    altered["receipt"]["digest"] = fixture_digest({k: v for k, v in altered.items() if k != "receipt"})
                    return self.respond(200, altered)
            self.respond(*state["responses"].get(self.path, (404, {})))

        def do_POST(self):
            state["requests"].append(("POST", self.path, self.headers.get("Authorization") == "Bearer " + TOKEN))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posted_request"] = body
            response = completion(body)
            if state["mutate_completion"]:
                state["mutate_completion"](response)
            self.respond(200, response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state["target"] = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("SZL_ROUTER_TOKEN", TOKEN)
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def run(router, **kwargs):
    return verifier.run(router["target"], REVISION, "test-model", timeout=1, **kwargs)


def test_read_only_runs_three_gets_without_token_or_inference(router, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    evidence = run(router)
    assert evidence["result"] == "PASS_READ_ONLY"
    assert evidence["inference"]["state"] == "NOT_REQUESTED"
    assert evidence["source"]["files_verified_against_git"] is False
    assert evidence["configuration"]["basis"] == "LOCAL_CONFIGURATION_ONLY"
    assert evidence["five_organ_acceptance"] == "NOT_ESTABLISHED"
    assert router["requests"] == [("GET", path, False) for path in
                                  ("/api/source", "/readyz/inference", "/v1/models")]


def test_explicit_inference_binds_receipt_request_completion_and_source(router):
    evidence = run(router, infer=True)
    assert evidence["result"] == "PASS_INFERENCE"
    assert evidence["inference"]["state"] == "INFERENCE_WITNESSED"
    assert evidence["source"]["post_inference_state"] == "MANIFEST_UNCHANGED"
    assert [request for request in router["requests"] if request[0] == "POST"] == [
        ("POST", "/v1/chat/completions", True)]
    sent = router["posted_request"]
    assert sent["max_cost_tier"] == 0 and sent["max_tokens"] == 16
    assert evidence["inference"]["request_digest"] == fixture_digest(sent)
    serialized = json.dumps(evidence)
    assert TOKEN not in serialized and router["target"] not in serialized
    assert sent["messages"][0]["content"] not in serialized
    assert "ROUTER_ACCEPTANCE_OK" not in serialized
    second = run(router, infer=True)
    assert second["inference"]["request_digest"] != evidence["inference"]["request_digest"]


@pytest.mark.parametrize("field,value,expected", [
    ("request_digest", "0" * 64, "INFERENCE_REQUEST_DIGEST_MISMATCH"),
    ("response_digest", "0" * 64, "INFERENCE_RESPONSE_DIGEST_MISMATCH"),
    ("public_model", "wrong-model", "INFERENCE_RECEIPT_BINDING_MISMATCH"),
    ("secret_material_recorded", True, "INFERENCE_RECEIPT_BINDING_MISMATCH"),
])
def test_self_consistent_but_wrong_receipt_bindings_fail(router, field, value, expected):
    def mutate(response):
        response["szl_receipt"][field] = value
        resign_receipt(response)
    router["mutate_completion"] = mutate
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL" and evidence["inference"]["failure"] == expected


@pytest.mark.parametrize("mutation,expected", [
    (lambda response: response["szl_receipt"].update(digest="0" * 64), "INFERENCE_RECEIPT_DIGEST_MISMATCH"),
    (lambda response: response["choices"][0]["message"].update(content="changed"), "INFERENCE_RESPONSE_DIGEST_MISMATCH"),
    (lambda response: response.update(choices=[]), "COMPLETION_INVALID"),
])
def test_tampered_or_malformed_completion_fails(router, mutation, expected):
    router["mutate_completion"] = mutation
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL" and evidence["inference"]["failure"] == expected


def test_source_change_during_inference_fails(router):
    router["source_drift"] = True
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL"
    assert evidence["inference"]["failure"] == "SOURCE_CHANGED_DURING_INFERENCE"


@pytest.mark.parametrize("header", ["", "0" * 64])
def test_receipt_header_must_match_body_digest(router, header):
    router["receipt_header"] = header
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL"
    assert evidence["inference"]["failure"] == "INFERENCE_RECEIPT_HEADER_MISMATCH"


@pytest.mark.parametrize("header", ["", "max-age=60", "no-store=false"])
def test_evidence_requires_no_store_response_header(router, header):
    router["cache_control"] = header
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL" and evidence["inference"]["state"] == "BLOCKED_BY_PREREQUISITES"
    assert all(evidence[part]["failure"] == "CACHE_CONTROL_NO_STORE_REQUIRED"
               for part in ("source", "configuration", "models"))


@pytest.mark.parametrize("part,key,value", [
    ("/api/source", "revision", "f" * 40),
    ("/api/source", "repository", "another/repository"),
    ("/readyz/inference", "basis", "LIVE_INFERENCE"),
    ("/readyz/inference", "inference_witness", "VERIFIED"),
    ("/readyz/inference", "ready_for_requests", 1),
    ("/v1/models", "data", []),
])
def test_independent_prerequisite_failures_block_post(router, part, key, value):
    router["responses"][part][1][key] = value
    evidence = run(router, infer=True)
    assert evidence["result"] == "FAIL"
    assert evidence["inference"]["state"] == "BLOCKED_BY_PREREQUISITES"
    assert len(router["requests"]) == 3 and all(row[0] == "GET" for row in router["requests"])
    assert sum(evidence[key]["state"] == "FAIL" for key in ("source", "configuration", "models")) == 1


@pytest.mark.parametrize("raw,expected", [
    (b'{"error":"fixture-secret-do-not-record"}', "SOURCE_IDENTITY_MISMATCH"),
    (b'{"a":1,"a":2}', "DUPLICATE_JSON_KEY"),
    (b'{"a":NaN}', "NONFINITE_JSON"),
    (b'{"a":', "TRANSPORT_OR_JSON_FAILURE"),
    (b'[]', "RESPONSE_NOT_OBJECT"),
])
def test_invalid_json_and_private_errors_never_escape(router, raw, expected):
    router["responses"]["/api/source"] = (200, raw)
    evidence = run(router)
    assert evidence["source"]["failure"] == expected and TOKEN not in json.dumps(evidence)


def test_redirect_not_followed_and_token_not_sent(router):
    router["responses"]["/api/source"] = (302, {})
    evidence = run(router, infer=True)
    assert evidence["source"]["failure"] == "REDIRECT_REJECTED"
    assert len(router["requests"]) == 3 and all(row[2] is False for row in router["requests"])


def test_response_limit_fails_closed(router, monkeypatch):
    monkeypatch.setattr(verifier, "MAX_RESPONSE_BYTES", 1024)
    router["responses"]["/api/source"] = (200, b"x" * 1025)
    assert run(router)["source"]["failure"] == "RESPONSE_TOO_LARGE"


def test_missing_token_blocks_post_after_independent_gets(router, monkeypatch):
    monkeypatch.delenv("SZL_ROUTER_TOKEN")
    evidence = run(router, infer=True)
    assert evidence["inference"]["failure"] == "ROUTER_TOKEN_UNAVAILABLE_OR_INVALID"
    assert len(router["requests"]) == 3


@pytest.mark.parametrize("target", [
    "http://example.com", "http://localhost", "http://127.0.0.1.evil.example",
    "ftp://example.com", "https://user:password@example.com", "https://example.com?token=secret",
    "https://example.com/#secret", "https://example.com/../other", "https://example.com/%2fother",
    "https://example.com\n/header", "https://example.com:99999", "https://[bad-ip]",
])
def test_invalid_targets_fail_before_any_transport(target, monkeypatch):
    monkeypatch.setattr(verifier, "fetch", lambda *_args: pytest.fail("transport must not run"))
    evidence = verifier.run(target, REVISION, "test-model")
    assert evidence["result"] == "FAIL" and target not in json.dumps(evidence)


def test_slow_headers_are_bounded():
    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            time.sleep(0.5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        parts = verifier.target_parts(f"http://127.0.0.1:{server.server_port}")
        started = time.monotonic()
        with pytest.raises(verifier.Failure):
            verifier.fetch(parts, "/slow", 0.1)
        assert time.monotonic() - started < 0.4
    finally:
        server.shutdown()
        server.server_close()


def test_cli_emits_sanitized_evidence_and_exit_status(router, capsys):
    assert verifier.main(["--target", router["target"], "--expected-revision", REVISION,
                          "--model", "test-model"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["result"] == "PASS_READ_ONLY"
    assert TOKEN not in output.out and router["target"] not in output.out
    assert output.err == ""


def test_argument_errors_do_not_echo_values(capsys):
    with pytest.raises(SystemExit) as caught:
        verifier.main(["--target", "https://private.invalid/", "--expected-revision", REVISION,
                       "--model", "test-model", "--token", TOKEN])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert TOKEN not in output.err and "private.invalid" not in output.err


def test_unrepresentable_timeout_fails_before_transport(monkeypatch):
    monkeypatch.setattr(verifier, "fetch", lambda *_args: pytest.fail("transport must not run"))
    evidence = verifier.run("https://example.com", REVISION, "test-model", timeout=10 ** 1000)
    assert evidence["failure"] == "TIMEOUT_INVALID"
