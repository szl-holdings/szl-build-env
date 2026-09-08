# Router ecosystem acceptance

`verify/router_acceptance.py` checks the canonical `router_control.app:app`
surface from `szl-holdings/szl-router`. It produces JSON evidence on stdout and
returns zero only when the requested checks pass. It uses Python 3.10+ and the
standard library. Tests require pytest.

Supply the exact router commit SHA from the protected GitHub merge or deployment
you intend to inspect. A source match confirms the runtime reports that revision
and provides a consistent SHA256 source receipt. It does **not** independently
compare the runtime files with GitHub, prove a container digest, validate a
signature, or establish that the revision was merged. Those proof layers remain
separate, and `files_verified_against_git` remains `false` in the evidence.

## Read-only admission

```bash
python3 verify/router_acceptance.py \
  --target "$ROUTER_URL" \
  --expected-revision "$ROUTER_REVISION" \
  --model "$ROUTER_MODEL" > router-admission.json
```

The equivalent Make target is:

```bash
make router-acceptance ROUTER_URL="$ROUTER_URL" \
  ROUTER_REVISION="$ROUTER_REVISION" ROUTER_MODEL="$ROUTER_MODEL"
```

This sends exactly three GET requests: `/api/source`, `/readyz/inference` and
`/v1/models`. No bearer token is sent on these public reads. The source response
must identify the canonical repository and exact expected commit, include the
four controlled router files and have a valid SHA256 receipt. The admission
response must explicitly say `LOCAL_CONFIGURATION_ONLY`, with provider
reachability unverified and inference unavailable. The requested model must
appear in the validated catalog. Evidence responses must use `Cache-Control:
no-store`.

These checks are recorded independently. A source failure does not erase the
observed configuration or model result. `PASS_READ_ONLY` means these three
contracts passed; it is never a claim that a provider answered a prompt.

## Explicit inference witness

Load the caller token into `SZL_ROUTER_TOKEN` using your normal secret-management
process. The CLI accepts no token argument. Then explicitly request inference:

```bash
python3 verify/router_acceptance.py \
  --target "$ROUTER_URL" \
  --expected-revision "$ROUTER_REVISION" \
  --model "$ROUTER_MODEL" \
  --infer > router-inference.json
```

Only `--infer` permits a POST, and only after all three admission checks pass.
It sends one short public test prompt with a fresh nonce and a 16-token response
limit. The default `max_cost_tier` is zero. To authorize an eligible higher-cost
route for this explicit probe, set `--max-cost-tier` to the required ceiling
between zero and ten. Provider-side charges and quotas still apply. There are
no retries or fallback probes in this verifier.

The witness requires a nonempty assistant text response and verifies:

- The `szl.router-receipt/v1` body digest, using canonical UTF-8 JSON and excluding
  the receipt's `algorithm` and `digest` fields from that digest.
- `request_digest` against the exact normalized request, including router defaults.
- `response_digest` against the returned completion with `szl_receipt` removed.
- The requested public model, classification and successful provider attempt.
- `X-SZL-Receipt` against the body receipt digest, and `Cache-Control: no-store`.
- An unchanged source manifest from a fresh `/api/source` read after inference.

`PASS_INFERENCE` witnesses this bounded router transaction and its digest
consistency. A SHA256 receipt is not a cryptographic signature or independent
provider attestation. It does not establish training, quality, all model routes,
all providers, or the existing five-organ runtime gate. `five_organ_acceptance`
remains `NOT_ESTABLISHED`; `make trace` and its full gate are unchanged.

## Transport and evidence boundaries

Targets must use HTTPS, except literal loopback HTTP addresses such as
`http://127.0.0.1:8000` or `http://[::1]:8000` for local testing. Plain HTTP
`localhost` names, URL credentials, query strings, fragments, ambiguous paths,
redirects and compressed responses are rejected. Connections ignore proxy
environment variables. TLS uses the platform's normal certificate validation.

Every response is capped at 2,000,000 bytes. `--timeout` sets a 0.1–60 second
deadline for each connected request (default ten seconds). A timer interrupts
slow headers and bodies; the operating system's hostname resolution may take
longer, but an expired connection never sends a late request. Inference mode
performs at most five requests including the source recheck.

JSON evidence contains fixed status codes, approved identifiers and digests.
It omits the target URL, bearer token, prompt, completion text, remote error
bodies and raw exception strings. Do not treat router-generated evidence as
independent proof of the deployment's Git file bytes.

## Local verification

```bash
python3 -m pytest -q verify/test_router_acceptance.py
# or: make test-router-acceptance
```

The tests use a local HTTP fixture server and no real model providers. They
exercise source/configuration mismatches, actual transport calls, no-POST
defaults, authentication, canonical receipt bindings, Unicode, response/header
tampering, source changes during inference, stale/nonfinite JSON, response
bounds, timeout interruption, redirect rejection and proxy isolation. The
existing verifier CI job collects them without weakening five-organ acceptance.
