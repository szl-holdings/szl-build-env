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
| a11oy | `c285293c...d21526` | `a29f43251e63aa20469413bc006896be803a289d` | `27237732091` | historical; protected main has advanced |
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
- Neither the digest-pinned A11oy runtime nor the currently published protected-main
  A11oy runtime implements that root fan-out contract. Sentra documents only
  in-process propagation. Amaru explicitly disables generic OpenTelemetry and
  cannot emit the required OTLP span. Rosie's root health handler is not instrumented.
- Therefore health readiness can be repaired and verified, but five-service trace
  acceptance remains intentionally red. The unblock is protected-source organ
  successors that implement traceparent-aware inbound middleware, real downstream
  fan-out, OTLP export, and an exact machine-validated route acknowledgement,
  followed by signed/attested immutable image publication and digest relock here.

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
