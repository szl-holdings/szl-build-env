# HONEST_GAPS.md

> **Doctrine: HONESTY OVER CHECKLIST.** This file lists everything in `szl-build-env`
> that is stubbed, blocked, or not-yet-real. If it is not in this file, it is real.
> Doctrine v11 LOCKED `749/14/163` @ kernel `c7c0ba17`. Λ = Conjecture 1 (never theorem).

---

## 1. killinchu image is PRIVATE on GHCR

- The legacy mutable `ghcr.io/szl-holdings/killinchu:uds-v0.2.0` pointer does
  **not** carry the required legacy-discoverable signature and is no longer consumed.
- Protected Killinchu main `cc49a0cc5fa03405fc7894c64040e013911a63bc`
  produced immutable digest
  `sha256:1620a0f38054121f1c11705889bc17ed376412934387f07358f354e5d1a0d2c9`
  in workflow run `32365110327`. That run keyless-signed the digest, verified the
  exact protected workflow identity and SLSA provenance, published legacy Cosign
  signature and attestation objects, and read back the immutable tag. This
  deployment pins that digest. The GHCR package remains private.
- **Effect:** an unauthenticated local cluster cannot start killinchu. Both
  `make verify` and the five-organ acceptance gate fail closed; neither presents
  4/5 as success or downgrades a registry, certificate, or signature error.
- **We do NOT fake this green.** No assumption is made that killinchu is public.
- **Authenticated path:** pass a `read:packages` credential on standard input to
  `bootstrap/configure-ghcr-pull-auth.py`. The helper creates a fresh, immutable
  `szl/szl-ghcr-pull` Secret. Only the killinchu pod references it for kubelet
  image pulls; only its Cosign init container mounts it, read-only. The application
  container never receives the credential as a volume or environment variable.
- CI uses the job-scoped `GITHUB_TOKEN` with `packages: read`, rejects forked PR
  acceptance before authentication, and removes the host-side Docker config after
  verification. The killinchu package must separately grant the repository Actions
  access; a missing grant or credential is a hard failure, never an anonymous pass.
- Making the package public remains an alternative founder action:
  https://github.com/orgs/szl-holdings/packages/container/killinchu/settings.
- The other four organs pull anonymously today, but they are also selected by
  immutable digest. Their signed source/run bindings are recorded below; no mutable
  `uds-v0.2.0` or `latest` selector participates in acceptance.

| Organ | Immutable digest | Signed source | Publisher run | Source status |
|---|---|---|---|---|
| a11oy | `c08839a0...04cef` | `bab6cbec341f4206edf22d13e93af690f656650f` | `44097341241` | source-bound signed build; runtime acceptance pending |
| sentra | `60a0efc1...3639` | `84c24336f7ce00aeda454c213c08da38f53e4c45` | `27040271560` | historical; source repository unavailable |
| amaru | `53301e26...89ff` | `324c3d60c2e2195e89cfefb28613ff26d94e67f8` | `27040273859` | historical; source repository unavailable |
| killinchu | `1620a0f3...d2c9` | `cc49a0cc5fa03405fc7894c64040e013911a63bc` | `32365110327` | exact protected main at publication |
| rosie | `1984a15f...2848` | `97be4e52695e8141036b1c4269a4722148852d4a` | `27043537785` | historical tag; source repository unavailable |

A signature proves publisher identity and subject digest. It does not make an
unavailable historical source repository current or reproducible.

## 2. DSSE receipt verification is REAL (was a collector stub)

- **Resolved.** DSSE/ECDSA-P256-SHA256 verification is now performed for real by
  `verify/dsse_verify.py` (run via `make verify-dsse`). It reuses the SAME
  primitive as `szl-receipt` / `vsp-otel` (canonical_json + DSSEv1 PAE +
  ECDSA-P256-SHA256): it prefers the installed `szl_receipt` package
  (`verify_receipt`) and falls back to a byte-for-byte identical inline
  implementation, so it never silently degrades.
- Honest verdicts (never a silent pass): good signature → `verified`; bad/tampered
  signature → `FAIL` (loud, non-zero exit); no/empty signature → `unsigned-honest`
  (explicitly NOT verified). Proven by `verify/test_dsse_verify.py`.
- **Remaining honest limit:** OTTL/YAML cannot run crypto in-path, so the
  `transform/dsse` processor in `manifests/otel/collector.yaml` only *promotes*
  the `szl.dsse.receipt` attribute and labels it `pending-sidecar-verify` with a
  `szl.dsse.verify_hook` pointer; the authoritative verdict comes from the
  sidecar above, keyed by `keys/cosign.pub` (public key — no secret committed).
- Organs must still actually emit `szl.dsse.receipt` attributes for the chain to
  populate; the demo prints whatever is emitted and says so if empty.

## 3. SLSA: L1 honest, L2 only where provenance exists

- Per doctrine: **SLSA L1 honest** — there is no build-provenance attestation
  workflow in this repo yet, so no L2 is produced. L2 is enforced *only where a
  provenance attestation already exists* on an image. We are **not** Iron Bank,
  **not** FedRAMP, **not** CMMC L2+.
- `verify/cosign-init.sh` runs `slsa-verifier verify-image` against every organ.
  Where a provenance attestation exists, it is enforced (L2). Where none exists,
  the organ is marked `[L1]` honest — we print that there is no provenance rather
  than claiming a passing L2 check.
- The in-pod init container (`manifests/organs/*.yaml`) enforces **cosign** as the
  hard fail-closed gate; it treats missing SLSA provenance as L1-honest so a valid
  cosign signature is still allowed to start. The authoritative full gate is
  `verify/cosign-init.sh` (run by `make verify`).

## 4. The published bundle vs. per-image pulls

- Doctrine target is the published bundle
  `oci://ghcr.io/szl-holdings/szl-uds-bundle:uds-v0.2.0`.
- For a fast local dev loop, organs pull the five **individual digest-pinned**
  images enumerated above (faster than rehydrating a full Zarf bundle into kind
  every `make up`). The bundle tag is a distribution reference, not an accepted
  runtime image selector. Bundle-based deploy (`zarf package deploy`) remains a
  documented follow-up and is not wired into `make up` today.

## 5. Waypoint L7 route is minimal

- `manifests/mesh/waypoint.yaml` declares the ambient waypoint and a minimal
  pass-through `HTTPRoute`. That configuration alone does not prove application
  fan-out, W3C context continuation, OTLP export, or five distinct Jaeger service
  identities. Those properties require runtime code in each immutable organ image.

## 6. Five-service fan-out and OTLP trace are blocked, fail-closed

- `verify/run-acceptance.sh` requires
  `GET /route?fanout=sentra,amaru,killinchu,rosie`, then requires one fresh Jaeger
  span graph connecting all five services to the exact injected root span. It never
  accepts an unconnected service-name bag or substitutes a health response.
- The previous signed A11oy image `c285293c...d21526` was built from source
  `a29f43251e63aa20469413bc006896be803a289d`, which did not contain `a11oy_build_env_fanout.py`.
  The observed trace-seed response exceeding 65,536 bytes is consistent with
  a SPA fallback, not an acknowledged fan-out; the response body was not retained.
- This change selects image `sha256:c08839a06002b88c7c5c11dc8140099f8dd56ba0a618c1b5457bdedf17404cef`
  from protected source `bab6cbec341f4206edf22d13e93af690f656650f`, published,
  keyless-signed and provenance-verified by workflow run `44097341241`.
  That source registers the fixed fan-out route and the isolated kind manifest
  explicitly enables `A11OY_BUILD_ENV_FANOUT=1`. Publisher identity is **not**
  evidence that the new image was deployed, the route acknowledged, or Jaeger
  observed five connected service spans.
- Sentra has previously documented only in-process propagation; Amaru's
  historical image disables generic OpenTelemetry; Rosie's historical root
  health handler was not instrumented. Their current deployed trace behavior
  remains unverified until the exact Jaeger acceptance artifact is inspected.
- First [CI on this review branch](https://github.com/szl-holdings/szl-build-env/actions/runs/44109044471)
  stopped at `make verify`: Cosign v2.4.1 returned `no signatures found` for
  the newly published A11oy artifact. A11oy's publisher run uses Cosign v3.1.4,
  whose OCI-referrer signature format requires the matching verifier; this
  review upgrades the host binary with its official SHA-256 locked and the
  isolated A11oy init gate to the exact immutable v3.1.4 image. The identity,
  issuer, workflow commit, and push-trigger certificate predicates remain exact.
  A successful signing log is NOT evidence of a successful verification.
  The newer A11oy publisher also logs a self-attested SLSA predicate which
  older slsa-verifier 2.6 may misclassify as absent. Therefore A11oy uniquely
  requires a separate Cosign v3 `verify-attestation --type slsaprovenance`
  check with the same exact source certificate binding, failing hard on
  missing/invalid attestation. This is signed org-owned provenance, **not**
  independent SLSA L2 or runtime admission evidence.
- [Follow-up CI 44115434984](https://github.com/szl-holdings/szl-build-env/actions/runs/44115434984)
  **passed all five** strict Cosign signature verifications. It then exposed
  the next independent failure: the new A11oy application container entered
  `CrashLoopBackOff`, and exact `/healthz` readiness timed out after 300s.
  The sanitized acceptance artifact reports `deployment_readiness_timeout`;
  it does not identify the application exception or establish a successful trace.
  The subsequent diagnostic collector records only allowlisted container
  states, restart/exit codes and exception-class counts from bounded previous
  logs. It never exports pod log lines, credentials, response text or env vars.
- [CI 44118532387](https://github.com/szl-holdings/szl-build-env/actions/runs/44118532387)
  returned the bounded diagnostic needed for the owned repair: the A11oy
  application container had **five restarts**, last termination
  **`OOMKilled` / exit code 137**; its Cosign init container had completed,
  and all four other deployments were ready. The signed A11oy runtime
  bundles a **986,047,904-byte GGUF**, while the K8s limit was only 512Mi.
  The isolated kind manifest and generated A11oy deployment now request
  1Gi and cap at 3Gi, preserving 512Mi caps for other organs. This is a
  source-bound memory-admission correction, **not** proof of runtime success.
  Two `ModuleNotFoundError` string occurrences in truncated prior logs are
  not treated as a verified fatal cause without a complete exception trace.
- [CI 44160741070](https://github.com/szl-holdings/szl-build-env/actions/runs/44160741070)
  proved all five deployments ready with HTTP 200 health and verified signatures,
  but the exact governed fan-out responded HTTP 503. Its sanitized, bounded
  readback recorded `TRACING_NOT_CONFIGURED`; Jaeger returned HTTP 404 for the
  injected trace and saw zero verified spans. In protected A11oy source,
  `vsp_otel.middleware._endpoint_policy` rejects non-loopback DNS hosts unless
  explicitly listed in `A11OY_OTEL_ALLOWED_HOSTS`. The isolated kind manifest
  already declared `OTEL_EXPORTER_OTLP_ENDPOINT=http://opentelemetry-collector.szl.svc:4317`
  but omitted that exact private DNS host from its allowlist. The reviewed
  repair adds only `opentelemetry-collector.szl.svc`, with a source-contract
  assertion; it does not admit wildcard or public OTLP destinations. A configured
  tracer is not collector delivery evidence, and downstream organs may still
  lack exported spans. A new exact signed-image acceptance run is required.
- Therefore five-service trace acceptance remains **HOLD** until A11oy boots
  and its signed container reaches readiness, CI proves a fresh exact
  traceparent, the validated route acknowledgement, distinct connected spans
  from all five services, and post-deployment readback.
  Missing downstream trace capabilities require separately owned source and
  signed-image repairs; neither skipped gates nor health-only checks count.
- **Causal peer diagnostic (source candidate, not admitted release):**
  [A11oy #3673](https://github.com/szl-holdings/a11oy/pull/3673)
  preserves the existing exact fan-out contract while returning a deterministic
  `peer_failures` list containing only one of the four fixed names and a finite
  `PEER_*` failure class. The build-env diagnostic now accepts and retains that
  list only when the source response has the exact denial schema, the root
  failure matches the first peer failure, and all names/reasons/order/duplicates
  pass explicit checks. Missing or malformed maps are `null` (UNKNOWN), never
  an empty successful list. No raw body, headers, token, host, upstream exception,
  private data, or signed/settled-transaction claim is stored.
  **The currently tested GHCR image does not contain #3673**. A new
  source-bound, signed immutable image, narrow manifest rebind and exact
  post-deploy five-organ readback remain required; the diagnostic alone does
  not repair or certify any downstream exporter.

## Secrets — what is and is NOT in this repo

- **Committed (safe):** `keys/cosign.pub` — the szl-holdings cosign **public** key.
  Public keys are not secrets.
- **NOT committed (never):** cosign private key, GHCR PAT, Sigstore tokens, any
  `*.key` / `*.pem` private material, image pull secrets. The CI workflow uses the
  ambient `GITHUB_TOKEN` with read-only `packages: read` and no extra secrets.
- Create killinchu pull auth only at runtime with
  `bootstrap/configure-ghcr-pull-auth.py`. It consumes the credential from standard
  input, not a command argument, and suppresses kubectl output that could expose
  the submitted Secret. Do **not** commit the resulting Docker config or Secret.
