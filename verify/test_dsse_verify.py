# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 SZL Holdings
"""Tests for verify/dsse_verify.py — proves the honest verdict contract.

  * good signature   -> verified
  * tampered payload  -> FAIL (loud)
  * keyless envelope  -> unsigned-honest (NEVER verified)

Requires szl-receipt (installed in CI via the git tag) to build envelopes.
"""
from __future__ import annotations

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import dsse_verify  # noqa: E402

szl_receipt = pytest.importorskip("szl_receipt")
from szl_receipt import Receipt, generate_keypair, sign_receipt  # noqa: E402


BODY = {"action": "deploy", "workload": "a11oy", "verdict": "admit", "tier": 3}


def _receipt() -> Receipt:
    return Receipt(kind="dsse.receipt", body=dict(BODY))


def test_good_signature_verified():
    priv, pub = generate_keypair()
    env = sign_receipt(_receipt(), priv, organ="a11oy")
    verdict, _ = dsse_verify.verify_envelope(env, pub)
    assert verdict == dsse_verify.VERIFIED


def test_tampered_payload_fails_loud():
    priv, pub = generate_keypair()
    env = sign_receipt(_receipt(), priv, organ="a11oy")
    # Adversary flips the verdict in the signed payload but keeps the signature.
    body = json.loads(base64.b64decode(env["payload"]).decode())
    body["verdict"] = "deny"
    env["payload"] = base64.b64encode(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    verdict, _ = dsse_verify.verify_envelope(env, pub)
    assert verdict == dsse_verify.FAIL


def test_wrong_key_fails_loud():
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    env = sign_receipt(_receipt(), priv, organ="a11oy")
    verdict, _ = dsse_verify.verify_envelope(env, other_pub)
    assert verdict == dsse_verify.FAIL


def test_keyless_is_unsigned_honest():
    env = sign_receipt(_receipt(), None, organ="a11oy")  # keyless
    # Even when a public key is supplied, an unsigned envelope is never a pass.
    priv, pub = generate_keypair()
    verdict, detail = dsse_verify.verify_envelope(env, pub)
    assert verdict == dsse_verify.UNSIGNED
    assert detail == "unsigned-honest"


def test_cli_span_attribute_form(tmp_path):
    priv, pub = generate_keypair()
    env = sign_receipt(_receipt(), priv, organ="a11oy")
    # The collector promotes the envelope as a base64 span attribute.
    span = {"szl.dsse.receipt": base64.b64encode(json.dumps(env).encode()).decode()}
    pubf = tmp_path / "cosign.pub"
    pubf.write_bytes(pub)
    inf = tmp_path / "span.json"
    inf.write_text(json.dumps(span))
    rc = dsse_verify.main([str(inf), "--pubkey", str(pubf)])
    assert rc == 0


def test_cli_tampered_returns_nonzero(tmp_path):
    priv, pub = generate_keypair()
    env = sign_receipt(_receipt(), priv, organ="a11oy")
    env["signature"] = base64.b64encode(b"not-a-real-signature").decode()
    pubf = tmp_path / "cosign.pub"
    pubf.write_bytes(pub)
    inf = tmp_path / "r.json"
    inf.write_text(json.dumps(env))
    rc = dsse_verify.main([str(inf), "--pubkey", str(pubf)])
    assert rc == 2
