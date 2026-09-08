#!/usr/bin/env python3
"""Bounded router source/configuration checks and opt-in inference witnessing.

Only --infer permits a POST. SHA256 receipts establish consistency, not an
independent signature or proof that all five ecosystem organs are operational.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from typing import Any

MAX_RESPONSE_BYTES = 2_000_000
SHA = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}")
SOURCE_FILES = {
    "router_control/app.py", "router_control/static/index.html",
    "router_control/static/app.js", "router_control/static/styles.css",
}


class Failure(ValueError):
    """Contains only a constant diagnostic code, never remote or secret data."""


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid command arguments; use --help for usage.\n")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Failure(code)


def target_parts(target: str) -> urllib.parse.SplitResult:
    require(isinstance(target, str) and 0 < len(target) <= 512
            and not any(char.isspace() or ord(char) < 32 for char in target), "INVALID_TARGET")
    try:
        parsed = urllib.parse.urlsplit(target)
        host, port = parsed.hostname, parsed.port
        require(bool(host) and (port is None or 1 <= port <= 65535), "INVALID_TARGET")
        require(not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment, "INVALID_TARGET")
        require(parsed.scheme in {"https", "http"}, "INVALID_TARGET_SCHEME")
        if parsed.scheme == "http":
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            require(loopback, "HTTP_REQUIRES_LITERAL_LOOPBACK")
        path = parsed.path.rstrip("/")
        require(re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", path) is not None
                and not any(part in {".", ".."} for part in path.split("/")), "INVALID_TARGET_PATH")
        return parsed
    except (ValueError, TypeError) as error:
        if isinstance(error, Failure):
            raise
        raise Failure("INVALID_TARGET") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise Failure("NONFINITE_JSON")


def fetch(parts: urllib.parse.SplitResult, path: str, timeout: float,
          payload: dict | None = None, token: str | None = None) -> tuple[int, dict, dict]:
    """Direct HTTP(S): no proxy environment, redirects, retries, or raw errors.

    A wall-clock timer shuts down the connected socket, bounding slow response
    headers and bodies in addition to the connect/socket timeout. OS hostname
    resolution may take longer; a timed-out connection never sends its request.
    """
    connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parts.hostname, parts.port, timeout=timeout)
    expired = threading.Event()
    active_socket = []

    def abort():
        expired.set()
        for current in active_socket:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    deadline = time.monotonic() + timeout
    try:
        connection.connect()
        active_socket.append(connection.sock)
        require(not expired.is_set() and time.monotonic() < deadline, "REQUEST_TIMEOUT")
        headers = {"Accept": "application/json", "User-Agent": "szl-build-env-router-acceptance/1"}
        raw_payload = None
        if payload is not None:
            raw_payload = canonical(payload)
            headers["Content-Type"] = "application/json"
            headers["Authorization"] = "Bearer " + (token or "")
        connection.request("POST" if payload is not None else "GET",
                           parts.path.rstrip("/") + path, body=raw_payload, headers=headers)
        response = connection.getresponse()
        require(not 300 <= response.status < 400, "REDIRECT_REJECTED")
        require(response.getheader("Content-Encoding", "identity").lower() == "identity",
                "CONTENT_ENCODING_REJECTED")
        chunks, size = [], 0
        while True:
            require(not expired.is_set() and time.monotonic() < deadline, "REQUEST_TIMEOUT")
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= MAX_RESPONSE_BYTES, "RESPONSE_TOO_LARGE")
            chunks.append(chunk)
        require(not expired.is_set(), "REQUEST_TIMEOUT")
        value = json.loads(b"".join(chunks).decode("utf-8"),
                           object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        require(isinstance(value, dict), "RESPONSE_NOT_OBJECT")
        return response.status, value, {
            "cache_control": response.getheader("Cache-Control", ""),
            "receipt_digest": response.getheader("X-SZL-Receipt", ""),
        }
    except Failure:
        raise
    except (OSError, http.client.HTTPException, ValueError, RecursionError, UnicodeError):
        raise Failure("REQUEST_TIMEOUT" if expired.is_set() else "TRANSPORT_OR_JSON_FAILURE") from None
    finally:
        timer.cancel()
        connection.close()


def validate_source(value: dict, revision: str) -> dict:
    require(value.get("schema") == "szl.router-source/v1"
            and value.get("repository") == "szl-holdings/szl-router", "SOURCE_IDENTITY_MISMATCH")
    require(value.get("revision") == revision, "SOURCE_REVISION_MISMATCH")
    receipt = value.get("receipt")
    require(isinstance(receipt, dict) and receipt.get("algorithm") == "sha256", "SOURCE_RECEIPT_INVALID")
    body = {key: item for key, item in value.items() if key != "receipt"}
    require(receipt.get("digest") == digest(body), "SOURCE_DIGEST_MISMATCH")
    files = value.get("controlled_files")
    require(isinstance(files, dict) and set(files) == SOURCE_FILES
            and all(isinstance(item, str) and SHA.fullmatch(item) for item in files.values()),
            "SOURCE_MANIFEST_INVALID")
    require(all(value.get(key) is False for key in
                ("default_egress", "secret_output", "arbitrary_url_routing")), "SOURCE_CONTRACT_MISMATCH")
    return {"state": "REVISION_MATCHED", "revision": revision, "source_digest": receipt["digest"],
            "receipt": "SHA256_CONSISTENT_NOT_SIGNED", "files_verified_against_git": False}


def validate_cache(headers: dict) -> None:
    require("no-store" in {part.strip().lower() for part in headers["cache_control"].split(",")},
            "CACHE_CONTROL_NO_STORE_REQUIRED")


def validate_readiness(value: dict) -> dict:
    checks = value.get("checks")
    require(isinstance(checks, dict) and set(checks) == {
        "registry_valid", "egress_enabled", "caller_auth_configured", "credentialed_provider"
    } and all(item is True for item in checks.values()), "INFERENCE_CONFIGURATION_UNAVAILABLE")
    require(value.get("status") == "configured" and value.get("ready_for_requests") is True,
            "READINESS_CONTRACT_MISMATCH")
    require(value.get("basis") == "LOCAL_CONFIGURATION_ONLY"
            and value.get("provider_reachability") == "UNVERIFIED"
            and value.get("inference_witness") == "UNAVAILABLE", "READINESS_OVERCLAIMS_INFERENCE")
    return {"state": "CONFIGURATION_ADMITTED", "basis": "LOCAL_CONFIGURATION_ONLY",
            "provider_reachability": "UNVERIFIED"}


def validate_models(value: dict, model: str) -> dict:
    rows = value.get("data")
    require(value.get("object") == "list" and value.get("szl_registry_state") == "VALIDATED"
            and isinstance(rows, list) and 0 < len(rows) <= 2048, "MODEL_CATALOG_INVALID")
    ids = []
    for row in rows:
        require(isinstance(row, dict) and row.get("object") == "model"
                and isinstance(row.get("id"), str) and bool(IDENTIFIER.fullmatch(row["id"])),
                "MODEL_CATALOG_INVALID")
        ids.append(row["id"])
    require(len(set(ids)) == len(ids) and model in ids, "REQUESTED_MODEL_UNAVAILABLE")
    return {"state": "MODEL_DISCOVERED", "model": model, "model_count": len(ids)}


def inference_request(model: str, max_cost_tier: int) -> dict:
    # Exact ChatRequest.model_dump(mode="json") defaults, including Message.name.
    return {"model": model, "messages": [{"role": "user", "name": None,
            "content": "Reply with ROUTER_ACCEPTANCE_OK. Acceptance nonce: " + secrets.token_hex(16)}], "temperature": 0.0,
            "top_p": None, "max_tokens": 16, "stream": False, "stop": None,
            "user": None, "data_classification": "public", "max_cost_tier": max_cost_tier}


def validate_inference(value: dict, request: dict) -> dict:
    require(value.get("error") is None, "INFERENCE_RETURNED_ERROR")
    choices = value.get("choices")
    require(isinstance(choices, list) and bool(choices), "COMPLETION_INVALID")
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        require(isinstance(message, dict) and message.get("role") == "assistant"
                and isinstance(message.get("content"), str) and bool(message["content"].strip()),
                "COMPLETION_INVALID")
    receipt = value.get("szl_receipt")
    require(isinstance(receipt, dict) and receipt.get("schema") == "szl.router-receipt/v1"
            and receipt.get("algorithm") == "sha256", "INFERENCE_RECEIPT_INVALID")
    body = {key: item for key, item in receipt.items() if key not in {"algorithm", "digest"}}
    require(receipt.get("digest") == digest(body), "INFERENCE_RECEIPT_DIGEST_MISMATCH")
    require(receipt.get("request_digest") == digest(request), "INFERENCE_REQUEST_DIGEST_MISMATCH")
    completion = {key: item for key, item in value.items() if key != "szl_receipt"}
    require(receipt.get("response_digest") == digest(completion), "INFERENCE_RESPONSE_DIGEST_MISMATCH")
    require(receipt.get("public_model") == request["model"]
            and receipt.get("classification") == request["data_classification"]
            and receipt.get("secret_material_recorded") is False, "INFERENCE_RECEIPT_BINDING_MISMATCH")
    require(all(isinstance(receipt.get(key), str) and IDENTIFIER.fullmatch(receipt[key])
                for key in ("provider_id", "upstream_model"))
            and isinstance(receipt.get("plan_digest"), str) and bool(SHA.fullmatch(receipt["plan_digest"])),
            "INFERENCE_ROUTE_BINDING_INVALID")
    attempts = receipt.get("attempts")
    require(isinstance(attempts, list) and bool(attempts) and isinstance(attempts[-1], dict)
            and attempts[-1].get("state") == "SUCCESS" and attempts[-1].get("status_code") == 200
            and attempts[-1].get("provider_id") == receipt.get("provider_id"), "INFERENCE_ATTEMPT_INVALID")
    return {"state": "INFERENCE_WITNESSED", "request_digest": receipt["request_digest"],
            "response_digest": receipt["response_digest"], "receipt_digest": receipt["digest"],
            "receipt": "SHA256_CONSISTENT_NOT_SIGNED"}


def run(target: str, revision: str, model: str, *, infer: bool = False,
        timeout: float = 10.0, max_cost_tier: int = 0) -> dict:
    evidence = {"schema": "szl.build-env.router-acceptance/v1", "result": "FAIL",
                "source": {"state": "NOT_CHECKED"}, "configuration": {"state": "NOT_CHECKED"},
                "models": {"state": "NOT_CHECKED"}, "inference": {"state": "NOT_REQUESTED"},
                "five_organ_acceptance": "NOT_ESTABLISHED"}
    try:
        parts = target_parts(target)
        require(isinstance(revision, str) and bool(REVISION.fullmatch(revision)), "EXPECTED_REVISION_INVALID")
        require(isinstance(model, str) and bool(IDENTIFIER.fullmatch(model)), "MODEL_INVALID")
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 0.1 <= timeout <= 60,
                "TIMEOUT_INVALID")
        require(type(max_cost_tier) is int and 0 <= max_cost_tier <= 10, "COST_TIER_INVALID")
    except (Failure, OverflowError) as error:
        evidence["failure"] = str(error) if isinstance(error, Failure) else "TIMEOUT_INVALID"
        return evidence
    failed = False
    for name, path, validate in (
        ("source", "/api/source", lambda value: validate_source(value, revision)),
        ("configuration", "/readyz/inference", validate_readiness),
        ("models", "/v1/models", lambda value: validate_models(value, model)),
    ):
        try:
            status, value, headers = fetch(parts, path, timeout)
            require(status == 200, "HTTP_STATUS_NOT_OK")
            validate_cache(headers)
            evidence[name] = validate(value)
        except (Failure, ValueError, TypeError, RecursionError) as error:
            # Keep only diagnostics created here; never exception text from a library.
            evidence[name] = {"state": "FAIL", "failure": str(error) if isinstance(error, Failure)
                              else "CONTRACT_INVALID"}
            failed = True
    if infer:
        evidence["inference"] = {"state": "BLOCKED_BY_PREREQUISITES"}
        if not failed:
            try:
                token = os.getenv("SZL_ROUTER_TOKEN", "").strip()
                require(bool(token) and len(token) <= 4096 and all(32 <= ord(c) <= 126 for c in token),
                        "ROUTER_TOKEN_UNAVAILABLE_OR_INVALID")
                request = inference_request(model, max_cost_tier)
                status, value, headers = fetch(parts, "/v1/chat/completions", timeout, request, token)
                require(status == 200, "INFERENCE_HTTP_STATUS_NOT_OK")
                validate_cache(headers)
                evidence["inference"] = validate_inference(value, request)
                require(headers["receipt_digest"] == evidence["inference"]["receipt_digest"],
                        "INFERENCE_RECEIPT_HEADER_MISMATCH")
                # The receipt has no source revision of its own. Require the same
                # self-reported source manifest on both sides of this request.
                status, value, headers = fetch(parts, "/api/source", timeout)
                require(status == 200, "SOURCE_RECHECK_HTTP_STATUS_NOT_OK")
                validate_cache(headers)
                recheck = validate_source(value, revision)
                require(recheck["source_digest"] == evidence["source"]["source_digest"],
                        "SOURCE_CHANGED_DURING_INFERENCE")
                evidence["source"]["post_inference_state"] = "MANIFEST_UNCHANGED"
            except (Failure, ValueError, TypeError, RecursionError) as error:
                evidence["inference"] = {"state": "FAIL", "failure": str(error) if isinstance(error, Failure)
                                         else "INFERENCE_CONTRACT_INVALID"}
                failed = True
    if not failed:
        evidence["result"] = "PASS_INFERENCE" if infer else "PASS_READ_ONLY"
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Router base URL (never written to evidence)")
    parser.add_argument("--expected-revision", required=True, help="Expected exact GitHub router commit SHA")
    parser.add_argument("--model", required=True)
    parser.add_argument("--infer", action="store_true", help="Explicitly authorize one inference POST")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-cost-tier", type=int, default=0, help="Inference cost ceiling (default 0)")
    args = parser.parse_args(argv)
    result = run(args.target, args.expected_revision, args.model, infer=args.infer,
                 timeout=args.timeout, max_cost_tier=args.max_cost_tier)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result["result"].startswith("PASS_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
