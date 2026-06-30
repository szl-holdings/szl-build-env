#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 SZL Holdings
# ORCID: 0009-0001-0110-4173
"""dsse_verify.py — REAL DSSE/ECDSA-P256-SHA256 receipt verifier (sidecar).

The OTel collector (``manifests/otel/collector.yaml``) is OTTL/YAML config and
cannot run cryptographic verification in-path; OTTL can only promote the
``szl.dsse.receipt`` span attribute so the chain is queryable. This sidecar is
the authoritative verify hook the pipeline calls: it decodes the szl-receipt
DSSE envelope and verifies the ECDSA-P256-SHA256 signature using the SAME
primitive as szl-receipt / vsp-otel (canonical_json + DSSEv1 PAE).

Honest verdicts (doctrine — never a silent pass):
  * good signature              -> ``verified``        (exit 0)
  * no/empty signature          -> ``unsigned-honest`` (exit 0, NOT verified)
  * bad/tampered signature      -> ``FAIL``            (exit 2, LOUD)

It prefers the installed ``szl_receipt`` package (``verify_receipt``); if that
package is not importable it falls back to a byte-for-byte identical inline
implementation so the verifier is self-contained and never silently degrades.

Input forms accepted (``--input`` or stdin):
  * a single szl-receipt envelope object
  * a JSON array of envelopes
  * an OTel span-attribute form ``{"szl.dsse.receipt": "<base64-json-envelope>"}``
    (or an array thereof) — exactly what the collector promotes onto a span.

Usage::

    python3 verify/dsse_verify.py --pubkey keys/cosign.pub receipts.json
    cat span.json | python3 verify/dsse_verify.py --pubkey keys/cosign.pub
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

VERIFIED = "verified"
UNSIGNED = "unsigned-honest"
FAIL = "FAIL"


# --------------------------------------------------------------------------- #
# Verification primitive. Prefer szl-receipt; fall back to an identical inline
# implementation (same canonical_json + DSSEv1 PAE + ECDSA-P256-SHA256).
# --------------------------------------------------------------------------- #
def _verify_with_szl_receipt(env: Dict[str, Any], pub_pem: Optional[bytes]) -> Tuple[bool, str]:
    from szl_receipt import verify_receipt  # type: ignore

    return verify_receipt(env, pub_pem)


def _verify_inline(env: Dict[str, Any], pub_pem: Optional[bytes]) -> Tuple[bool, str]:
    # Mirrors szl_receipt.verify_receipt exactly, including the UNSIGNED-honest
    # contract: an envelope with signed==False ALWAYS returns unsigned-honest.
    import struct

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.exceptions import InvalidSignature

    PAYLOAD_TYPE = "application/vnd.szl.receipt+json"

    if not env.get("signed", False):
        return False, "unsigned-honest"
    if not pub_pem:
        return False, "no public key provided"

    def canonical_json(obj: object) -> bytes:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def pae(payload_type: str, body: bytes) -> bytes:
        def _enc(s: bytes) -> bytes:
            return struct.pack("<Q", len(s)) + s

        return b"DSSEv1 " + _enc(payload_type.encode("utf-8")) + b" " + _enc(body)

    try:
        payload_bytes = base64.b64decode(env["payload"])
        body_dict = json.loads(payload_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False, "signature mismatch"

    try:
        pub = serialization.load_pem_public_key(pub_pem)
        signing = pae(env.get("payloadType", PAYLOAD_TYPE), canonical_json(body_dict))
        der_sig = base64.b64decode(env["signature"])
        pub.verify(der_sig, signing, ec.ECDSA(hashes.SHA256()))
        return True, "ok"
    except InvalidSignature:
        return False, "signature mismatch"
    except Exception as exc:  # noqa: BLE001
        return False, f"invalid key or encoding: {exc}"


def verify_envelope(env: Dict[str, Any], pub_pem: Optional[bytes]) -> Tuple[str, str]:
    """Return (verdict, detail) for one szl-receipt envelope.

    verdict is one of: ``verified`` | ``unsigned-honest`` | ``FAIL``.
    """
    try:
        ok, detail = _verify_with_szl_receipt(env, pub_pem)
    except ImportError:
        ok, detail = _verify_inline(env, pub_pem)

    if ok:
        return VERIFIED, detail
    if detail == "unsigned-honest":
        return UNSIGNED, detail
    return FAIL, detail


# --------------------------------------------------------------------------- #
# Input normalisation — accept raw envelopes or the collector span-attr form.
# --------------------------------------------------------------------------- #
def _coerce_envelopes(doc: Any) -> List[Dict[str, Any]]:
    items = doc if isinstance(doc, list) else [doc]
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Span-attribute form promoted by the collector's transform/dsse.
        if "szl.dsse.receipt" in item and "payload" not in item:
            raw = item["szl.dsse.receipt"]
            out.append(json.loads(base64.b64decode(raw).decode("utf-8")))
        else:
            out.append(item)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Real DSSE/ECDSA-P256 receipt verifier (honest verdicts).")
    ap.add_argument("input", nargs="?", help="receipt/envelope JSON file (default: stdin)")
    ap.add_argument("--pubkey", help="PEM public key file (omit to test the keyless path)")
    args = ap.parse_args(argv)

    pub_pem: Optional[bytes] = None
    if args.pubkey:
        with open(args.pubkey, "rb") as fh:
            pub_pem = fh.read()

    raw = open(args.input, "r", encoding="utf-8").read() if args.input else sys.stdin.read()
    if not raw.strip():
        print("dsse_verify: no input", file=sys.stderr)
        return 2
    envelopes = _coerce_envelopes(json.loads(raw))
    if not envelopes:
        print("dsse_verify: no receipt envelopes found in input", file=sys.stderr)
        return 2

    any_fail = False
    for i, env in enumerate(envelopes):
        verdict, detail = verify_envelope(env, pub_pem)
        organ = env.get("organ", "?")
        line = f"receipt[{i}] organ={organ} -> {verdict} ({detail})"
        if verdict == FAIL:
            any_fail = True
            print(f"  [FAIL] {line}", file=sys.stderr)
        elif verdict == UNSIGNED:
            print(f"  [unsigned-honest] {line}")
        else:
            print(f"  [verified] {line}")

    # Loud failure on any bad signature; unsigned-honest is honest, not a pass.
    return 2 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
