# szl-build-env

**Local build environment for SZL Holdings.** Brings up a full **5-organ + Istio
ambient mesh + OpenTelemetry** stack on a single-node [kind](https://kind.sigs.k8s.io)
cluster so any SZL engineer can develop against the real fleet topology in
**under 10 minutes**.

> Doctrine v11 LOCKED `749/14/163` @ kernel commit `c7c0ba17`.
> **NOT** Iron Bank · **NOT** FedRAMP · **NOT** CMMC L2+. SLSA **L1 honest + L2 attested**.
> Mesh: **Istio ambient** (ztunnel + waypoint), *not* sidecar injection — by doctrine we are not Iron Bank.

---

## What you get

| Layer | Component | Pin |
|-------|-----------|-----|
| Cluster | kind, single node | node image `v1.32.2` |
| Mesh | Istio **ambient** (ztunnel + waypoint) | `1.25.0` |
| Telemetry | OpenTelemetry Collector → Jaeger | collector `0.135.0` |
| Workloads | 5 organs: `a11oy`, `sentra`, `amaru`, `killinchu`, `rosie` | bundle `uds-v0.2.0` |
| Supply chain | `cosign verify` + `slsa-verifier` init gate | honest fail-closed |

Organ images are pulled from the published bundle
`oci://ghcr.io/szl-holdings/szl-uds-bundle:uds-v0.2.0`
(individual images `ghcr.io/szl-holdings/<organ>:uds-v0.2.0`).

---

## 10-minute quickstart

```bash
# 0. prerequisites (see Host prerequisites below) — Docker, kubectl, kind, istioctl
git clone https://github.com/szl-holdings/szl-build-env.git
cd szl-build-env

# 1. bring up the whole stack (kind + istio ambient + otel + 5 organs)
make up           # ~6-8 min on a warm Docker cache

# 2. prove the supply-chain gate is honest (cosign + slsa-verifier per organ)
make verify

# 3. send a request and watch one traceparent propagate across all 5 organs
make trace        # opens / prints the Jaeger trace tree

# 4. tear everything down
make down
```

One-shot golden path for a demo:

```bash
make demo         # up -> verify -> seed request -> show cross-organ trace + DSSE chain
```

---

## Host prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| Docker Desktop / Engine | 24+, **8 GB RAM** allocated | https://docs.docker.com/get-docker/ |
| kubectl | 1.30+ | https://kubernetes.io/docs/tasks/tools/ |
| kind | 0.32.0 | `go install sigs.k8s.io/kind@v0.32.0` |
| istioctl | 1.25.0 | `make istioctl` (downloads pinned binary into `./bin`) |
| cosign | 2.x | https://docs.sigstore.dev/cosign/installation/ |
| slsa-verifier | 2.x | https://github.com/slsa-framework/slsa-verifier#installation |
| jq | any | `brew install jq` / `apt install jq` |

> **Docker memory:** the ambient mesh + 5 organs + collector need ~6 GB working set.
> Set Docker Desktop to **8 GB+** (Settings → Resources). Less than 6 GB will OOM ztunnel.

---

## Repository layout

```
kind/cluster.yaml                  single-node kind config (pinned node image)
bootstrap/install-istio-ambient.sh Istio ambient installer (pinned 1.25.0)
bootstrap/install-otel-collector.sh OTLP collector + Jaeger exporter
manifests/organs/*.yaml            5 organ Deployments (cosign-gated initContainer)
manifests/mesh/waypoint.yaml       ambient waypoint for inter-organ L7 routing
manifests/otel/collector.yaml      OTLP collector config (DSSE-aware processor stub)
verify/cosign-init.sh              the fail-closed supply-chain gate
verify/run-acceptance.sh           /healthz + end-to-end traceparent propagation check
demo/greene-demo.sh                June 9 scripted demo
.github/workflows/ci.yml           PR CI: make up + make verify in kind
HONEST_GAPS.md                     everything currently stubbed and why
```

---

## Honest gaps

Read [`HONEST_GAPS.md`](./HONEST_GAPS.md) before you trust a green check.
Short version:

- **`killinchu` image is private.** It will `ImagePullBackOff` until the founder
  flips the GHCR package to public (or you add a pull secret). The other 4 organs
  pull anonymously. `make verify` reports this honestly rather than skipping it.
- **DSSE receipt chain is signature-light** until a real `COSIGN_KEY` /
  Sigstore identity is wired. The collector's DSSE processor is a documented stub.
- **SLSA L1 is honest; L2 attestation is verified only where provenance exists.**
  `slsa-verifier` runs against every organ but only enforces on images that ship
  a provenance attestation.

No secrets are committed to this repo. See `HONEST_GAPS.md` § Secrets.

---

## License

Apache-2.0. See [`LICENSE`](./LICENSE).
