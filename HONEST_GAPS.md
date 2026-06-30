# HONEST_GAPS.md

> **Doctrine: HONESTY OVER CHECKLIST.** This file lists everything in `szl-build-env`
> that is stubbed, blocked, or not-yet-real. If it is not in this file, it is real.
> Doctrine v11 LOCKED `749/14/163` @ kernel `c7c0ba17`. Λ = Conjecture 1 (never theorem).

---

## 1. killinchu image is PRIVATE on GHCR

- `ghcr.io/szl-holdings/killinchu:uds-v0.2.0` is **pushed and cosign-signed**
  (tlog `1705054225`) but the GHCR package visibility is **private**.
- **Effect:** `manifests/organs/killinchu.yaml` will `ImagePullBackOff` on a fresh
  cluster. `make verify` reports killinchu as `KNOWN-GAP` (not `FAIL`) and
  `make trace` shows 4/5 organs in the trace tree.
- **We do NOT fake this green.** No assumption is made that killinchu is public.
- **Unblock (founder, one click):**
  https://github.com/orgs/szl-holdings/packages/container/killinchu/settings
  → *Change visibility* → **Public**.
  Alternatively, add an image pull secret (see FOUNDER_BUILD_ENV.md troubleshooting).
- The other 4 organs (`a11oy`, `sentra`, `amaru`, `rosie`) pull anonymously today,
  contingent on their `uds-v0.2.0` images being public. As of the last build ledger,
  `a11oy`, `sentra`, `amaru` had a separate org-level GHCR push block; if their
  `uds-v0.2.0` tags are not yet public they will surface the same `KNOWN-GAP`.

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
- For a fast local dev loop, organs pull their **individual** images
  `ghcr.io/szl-holdings/<organ>:uds-v0.2.0` (faster than rehydrating a full Zarf
  bundle into kind every `make up`). Bundle-based deploy (`zarf package deploy`)
  is a documented follow-up, not wired into `make up` today.

## 5. Waypoint L7 route is minimal

- `manifests/mesh/waypoint.yaml` declares the ambient waypoint and a single
  pass-through `HTTPRoute` that preserves `traceparent`. It does not yet encode
  the full a11oy→sentra→amaru→killinchu→rosie call graph as explicit routes —
  organs do their own downstream fan-out. The waypoint guarantees L7 + traceparent
  preservation; the topology is owned by the organs.

## 6. Organ `/route?fanout=...` endpoint assumption

- `verify/run-acceptance.sh` POSTs to a `/route?fanout=...` endpoint on a11oy to
  trigger the cross-organ call. If the deployed organ build does not expose that
  endpoint yet, the script falls back to `/healthz` and the trace will show only
  the organs that actually got called. This is reported honestly, not hidden.

## Secrets — what is and is NOT in this repo

- **Committed (safe):** `keys/cosign.pub` — the szl-holdings cosign **public** key.
  Public keys are not secrets.
- **NOT committed (never):** cosign private key, GHCR PAT, Sigstore tokens, any
  `*.key` / `*.pem` private material, image pull secrets. The CI workflow uses the
  ambient `GITHUB_TOKEN` with read-only `packages: read` and no extra secrets.
- If you add a pull secret for killinchu, create it at runtime
  (`kubectl create secret docker-registry ...`) — do **not** commit it.
