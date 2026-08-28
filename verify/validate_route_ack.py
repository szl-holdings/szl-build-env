#!/usr/bin/env python3
"""Strict validator for the five-organ fan-out acknowledgement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

MAX_RESPONSE_BYTES = 65536
SCHEMA = "szl.build-env.fanout-ack/v1"
TRACEPARENT = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01")
REQUIRED_KEYS = {"accepted", "fanout", "schema", "traceparent"}


class ValidationError(ValueError):
    """The route response is not exact governed evidence."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate object key: {key}")
        result[key] = value
    return result


def validate(path: Path, expected_traceparent: str, expected_fanout: tuple[str, ...]) -> None:
    if TRACEPARENT.fullmatch(expected_traceparent) is None:
        raise ValidationError("expected traceparent is malformed")
    if not expected_fanout or len(set(expected_fanout)) != len(expected_fanout):
        raise ValidationError("expected fanout must be nonempty and unique")

    raw = path.read_bytes()
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ValidationError("response size is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError("response is not UTF-8") from error
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValidationError) as error:
        raise ValidationError("response is not strict JSON") from error

    if not isinstance(payload, dict) or set(payload) != REQUIRED_KEYS:
        raise ValidationError("response keys do not match the acknowledgement schema")
    if payload["schema"] != SCHEMA or payload["accepted"] is not True:
        raise ValidationError("route did not return an accepted v1 acknowledgement")
    if payload["traceparent"] != expected_traceparent:
        raise ValidationError("acknowledgement is not bound to the injected traceparent")
    fanout = payload["fanout"]
    if not isinstance(fanout, list) or any(not isinstance(item, str) for item in fanout):
        raise ValidationError("fanout is not a string array")
    if tuple(fanout) != expected_fanout:
        raise ValidationError("fanout does not exactly match the governed order")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--traceparent", required=True)
    parser.add_argument("--fanout", required=True)
    arguments = parser.parse_args()
    try:
        validate(
            arguments.path,
            arguments.traceparent,
            tuple(arguments.fanout.split(",")),
        )
    except (OSError, ValidationError) as error:
        print(f"invalid route acknowledgement: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
