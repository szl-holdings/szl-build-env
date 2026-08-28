#!/usr/bin/env python3
"""Validate one fresh, connected Jaeger trace for the governed five-organ route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TRACE_ID = re.compile(r"[0-9a-f]{32}")
SPAN_ID = re.compile(r"[0-9a-f]{16}")
REFERENCE_TYPES = {"CHILD_OF", "FOLLOWS_FROM"}


class TraceDataError(ValueError):
    """Jaeger returned data that cannot be accepted as governed evidence."""

    def __init__(self, failure: str):
        super().__init__(failure)
        self.failure = failure


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TraceDataError("duplicate_json_key")
        result[key] = value
    return result


def _observation(
    state: str,
    expected: tuple[str, ...],
    *,
    failure: str | None = None,
    span_count: int = 0,
    fresh_span_count: int = 0,
    observed_services=(),
    connected_services=(),
):
    observed = sorted(set(observed_services))
    connected = sorted(set(connected_services))
    return {
        "connected_services": connected,
        "failure": failure,
        "fresh_span_count": fresh_span_count,
        "missing_services": [name for name in expected if name not in connected],
        "observed_services": observed,
        "span_count": span_count,
        "state": state,
    }


def _load_payload(path: Path):
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_RESPONSE_BYTES:
            raise TraceDataError("invalid_jaeger_response_size")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_object)
    except TraceDataError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TraceDataError("malformed_jaeger_json") from error


def evaluate(
    path: Path,
    expected_trace_id: str,
    root_span_id: str,
    seed_epoch_us: int,
    expected_services: tuple[str, ...],
):
    if TRACE_ID.fullmatch(expected_trace_id) is None:
        raise ValueError("expected trace ID must be 32 lowercase hexadecimal characters")
    if SPAN_ID.fullmatch(root_span_id) is None:
        raise ValueError("root span ID must be 16 lowercase hexadecimal characters")
    if seed_epoch_us <= 0:
        raise ValueError("seed epoch must be positive")
    if not expected_services or len(set(expected_services)) != len(expected_services):
        raise ValueError("expected services must be nonempty and unique")

    payload = _load_payload(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TraceDataError("malformed_jaeger_envelope")
    traces = payload["data"]
    if not traces:
        return _observation("absent", expected_services, failure="trace_not_found")

    matching = []
    for trace in traces:
        if not isinstance(trace, dict) or not isinstance(trace.get("spans"), list):
            raise TraceDataError("malformed_jaeger_trace")
        ids = {
            span.get("traceID")
            for span in trace["spans"]
            if isinstance(span, dict) and isinstance(span.get("traceID"), str)
        }
        if expected_trace_id in ids:
            matching.append(trace)

    if not matching:
        return _observation("absent", expected_services, failure="trace_not_found")
    if len(matching) != 1:
        raise TraceDataError("ambiguous_trace_identity")

    trace = matching[0]
    spans = trace["spans"]
    if not spans:
        raise TraceDataError("trace_has_no_spans")
    processes = trace.get("processes", {})
    if not isinstance(processes, dict):
        raise TraceDataError("malformed_jaeger_processes")

    span_services = {}
    parents = {}
    observed_services = set()
    for span in spans:
        if not isinstance(span, dict):
            raise TraceDataError("malformed_jaeger_span")
        if span.get("traceID") != expected_trace_id:
            raise TraceDataError("contradictory_span_trace_id")

        current_span_id = span.get("spanID")
        if not isinstance(current_span_id, str) or SPAN_ID.fullmatch(current_span_id) is None:
            raise TraceDataError("malformed_span_id")
        if current_span_id in span_services:
            raise TraceDataError("duplicate_span_id")

        start_time = span.get("startTime")
        duration = span.get("duration")
        if type(start_time) is not int or start_time <= 0:
            raise TraceDataError("malformed_span_start_time")
        if start_time < seed_epoch_us:
            raise TraceDataError("span_precedes_trace_seed")
        if type(duration) is not int or duration < 0:
            raise TraceDataError("malformed_span_duration")

        process = span.get("process")
        if process is None:
            process_id = span.get("processID")
            process = processes.get(process_id) if isinstance(process_id, str) else None
        if not isinstance(process, dict):
            raise TraceDataError("missing_span_process")
        service_name = process.get("serviceName")
        if not isinstance(service_name, str) or not service_name.strip():
            raise TraceDataError("missing_service_identity")
        service_name = service_name.strip()
        observed_services.add(service_name)
        span_services[current_span_id] = service_name

        references = span.get("references", [])
        if not isinstance(references, list):
            raise TraceDataError("malformed_span_references")
        parent_ids = set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"refType", "traceID", "spanID"}:
                raise TraceDataError("malformed_span_reference")
            if reference["refType"] not in REFERENCE_TYPES:
                raise TraceDataError("unsupported_span_reference_type")
            if reference["traceID"] != expected_trace_id:
                raise TraceDataError("cross_trace_reference")
            parent_id = reference["spanID"]
            if not isinstance(parent_id, str) or SPAN_ID.fullmatch(parent_id) is None:
                raise TraceDataError("malformed_parent_span_id")
            if parent_id == current_span_id:
                raise TraceDataError("self_referential_span")
            if parent_id in parent_ids:
                raise TraceDataError("duplicate_span_reference")
            parent_ids.add(parent_id)
        parents[current_span_id] = parent_ids

    memo = {}

    def connected_to_root(current_span_id: str, trail: frozenset[str]) -> bool:
        if current_span_id == root_span_id:
            return True
        if current_span_id in memo:
            return memo[current_span_id]
        if current_span_id in trail:
            return False
        next_trail = trail | {current_span_id}
        connected = any(
            parent_id == root_span_id
            or (
                parent_id in span_services
                and connected_to_root(parent_id, next_trail)
            )
            for parent_id in parents[current_span_id]
        )
        memo[current_span_id] = connected
        return connected

    connected_services = {
        span_services[current_span_id]
        for current_span_id in span_services
        if connected_to_root(current_span_id, frozenset())
    }
    expected_set = set(expected_services)
    if expected_set <= connected_services:
        return _observation(
            "complete",
            expected_services,
            span_count=len(spans),
            fresh_span_count=len(spans),
            observed_services=observed_services,
            connected_services=connected_services,
        )
    return _observation(
        "incomplete",
        expected_services,
        failure="incomplete_connected_service_set",
        span_count=len(spans),
        fresh_span_count=len(spans),
        observed_services=observed_services,
        connected_services=connected_services,
    )


def _write_observation(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, separators=(",", ":"), sort_keys=True)
        output.write("\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--root-span-id", required=True)
    parser.add_argument("--seed-epoch-us", type=int, required=True)
    parser.add_argument("--services", required=True)
    arguments = parser.parse_args()
    expected = tuple(arguments.services.split(","))
    try:
        observation = evaluate(
            arguments.response,
            arguments.trace_id,
            arguments.root_span_id,
            arguments.seed_epoch_us,
            expected,
        )
    except TraceDataError as error:
        observation = _observation(
            "malformed",
            expected,
            failure=error.failure,
        )
    except ValueError as error:
        print(f"invalid validator input: {error}", file=sys.stderr)
        return 2

    try:
        _write_observation(arguments.observation, observation)
    except OSError as error:
        print(f"cannot write trace observation: {error}", file=sys.stderr)
        return 2
    print(observation["state"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
