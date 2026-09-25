<!-- szl-investor-header -->
<div align="center">

# szl-build-env

### A local environment for SZL's full 5-organ governance stack — service mesh, telemetry, and signed-image verification — in under 10 minutes.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](LICENSE) [![Build](https://github.com/szl-holdings/szl-build-env/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/szl-holdings/szl-build-env/actions/workflows/ci.yml) [![Doctrine v11](https://img.shields.io/badge/Doctrine-v11_LOCKED-3b82f6?style=flat-square)](https://github.com/szl-holdings/.github/tree/main/doctrine) [![SLSA](https://img.shields.io/badge/SLSA-L1_honest-22c55e?style=flat-square)](https://slsa.dev/spec/v1.0/levels)

[Docs](https://szl-holdings.github.io/docs-site) · [Quickstart](https://szl-holdings.github.io/docs-site/quickstart) · [SZL Holdings](https://a-11-oy.com)

</div>

## 💡 Why it matters

Engineers and design partners can stand up the governed-AI topology on a laptop, verify exact image identities, and see every missing runtime proof fail closed instead of being presented as an end-to-end success.

## ▶️ Live demo

This is a **public** repository. There is no hosted demo by design — stand it up
on your own machine via the authenticated quickstart below, or see
[docs.szlholdings.com](https://szl-holdings.github.io/docs-site) for the public
product walkthrough.

## ⚡ Quick start

Follow the [authenticated 10-minute quickstart](#10-minute-quickstart). The
killinchu image is private, so a real five-organ run requires read-only GHCR
access; the setup does not pretend an anonymous 4/5 deployment is complete.

For the separate canonical router service, the [router acceptance guide](docs/router-acceptance.md)
checks an expected GitHub revision, configuration admission and model discovery.
Inference requires an explicit `--infer` invocation and a caller token supplied
through the environment; a read-only pass never establishes provider inference
or five-organ runtime acceptance.

## 🔍 How it works

In two sentences: this component is part of SZL's governed-AI mesh — it enforces policy and emits signed, replayable audit receipts so every AI action can be verified after the fact. The full mathematical foundation, formal proofs, and protocol details are documented below and in the [technical docs](https://szl-holdings.github.io/docs-site).

---

<details>
<summary><strong>📐 Full technical detail, math, and proofs (the proof, not the pitch)</strong></summary>

# szl-build-env

**Local build environment for SZL Holdings.** Brings up a full **5-organ + Istio
ambient mesh + OpenTelemetry** stack on a single-node [kind](https://kind.sigs.k8s.io)
cluster so any SZL engineer can develop against the real fleet topology in
**under 10 minutes**.

> Doctrine v11 LOCKED `749/14/163` @ kernel commit `c7c0ba17`.
> **NOT** Iron Bank · **NOT** FedRAMP · **NOT** CMMC L2+. SLSA **L1 honest** (no build-provenance attestation workflow — L2 not yet produced).
> Mesh: **Istio ambient** (ztunnel + waypoint), *not* sidecar injection — by doctrine we are not Iron Bank.

---

## What you get

| Layer | Component | Pin |
|-------|-----------|-----|
| Cluster | kind, single node | node image `v1.32.2` |
| Mesh | Istio **ambient** (ztunnel + waypoint) | `1.25.0` |
| Telemetry | OpenTelemetry Collector → Jaeger | collector `0.135.0` |
| Workloads | 5 organs by canonical role: **a11oy** (gate), **Policy** (egress immune-inspector, image `sentra`), **Provenance Anchor** (read-only reasoning cortex, image `amaru`), **killinchu** (counter-UAS), **Operator** (console, image `rosie`) | five immutable digests |
| Supply chain | `cosign verify` + `slsa-verifier` init gate | honest fail-closed |

> **Naming note (doctrine).** User-facing role names are the canonical ones: **Policy**,
> **Provenance Anchor**, and **Operator** (plus the Quechua organ names `a11oy` and `killinchu`).
> The lowercase code-formatted tokens `sentra`/`amaru`/`rosie` appearing in this repo are
> **immutable infrastructure coordinates only** — OCI image names, Zarf package keys, and k8s
> manifest filenames — kept verbatim because renaming them breaks image pulls. They are not
> product/role labels; always refer to the organs by their canonical roles above.

The bundle remains a distribution coordinate, but the cluster never deploys its
mutable tag. Every organ is selected by an exact `@sha256:` digest and checked
against an exact Fulcio workflow/repository/ref/source/trigger identity. The
source/run ledger and historical-source limitations are recorded in
[`HONEST_GAPS.md`](./HONEST_GAPS.md).

---

## 10-minute quickstart

```bash
# 0. prerequisites (see Host prerequisites below) — Docker, kubectl, kind, istioctl
git clone https://github.com/szl-holdings/szl-build-env.git
cd szl-build-env

# 1. create the cluster, then install read-only GHCR auth for private killinchu
make cluster
GHCR_USERNAME=your-github-login
GHCR_AUTH_DIR="$(mktemp -d)"
read -rsp "GHCR token (read:packages only): " GHCR_TOKEN; echo
printf '%s' "$GHCR_TOKEN" | python3 bootstrap/configure-ghcr-pull-auth.py \
  --username "$GHCR_USERNAME" --docker-config-dir "$GHCR_AUTH_DIR"
unset GHCR_TOKEN
export DOCKER_CONFIG="$GHCR_AUTH_DIR"

# 2. bring up the whole stack (kind + istio ambient + otel + 5 organs)
make up           # ~6-8 min on a warm Docker cache

# 3. prove the supply-chain gate is honest (cosign + slsa-verifier per organ)
make verify

# Remove the host copy after verification. The namespace-scoped pull Secret remains.
rm -- "$GHCR_AUTH_DIR/config.json" && rmdir "$GHCR_AUTH_DIR"
unset DOCKER_CONFIG GHCR_AUTH_DIR GHCR_USERNAME

# 4. run the strict trace gate (currently expected to fail on the missing
#    protected-source fan-out/OTLP runtime prerequisite documented below)
make trace

# 5. tear everything down
make down
```

The token needs only `read:packages`, must be authorized for the organization,
and the `killinchu` package must grant this repository or operator account read
access. The helper accepts the token only on standard input. It refuses to
overwrite an existing pull Secret and never places the token in process arguments
or command output. An unauthenticated local cluster remains an honest, fail-closed
4/5 environment; `make trace` will not claim five-organ acceptance.

One-shot honesty gate:

```bash
make demo         # exits non-zero unless image, readiness, trace, and DSSE gates all pass
```

---

## Host prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| Docker Desktop / Engine | 24+, **8 GB RAM** allocated | https://docs.docker.com/get-docker/ |
| kubectl | 1.30+ | https://kubernetes.io/docs/tasks/tools/ |
| kind | 0.32.0 | `go install sigs.k8s.io/kind@v0.32.0` |
| istioctl | 1.25.0 | `make istioctl` (downloads pinned binary into `./bin`) |
| cosign | 2.x | https://docs.sigstore.dev/cosign/system_config/installation/ |
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
bootstrap/configure-ghcr-pull-auth.py stdin-only, runtime GHCR credential installer
manifests/organs/*.yaml            5 organ Deployments (cosign-gated initContainer)
manifests/mesh/waypoint.yaml       ambient waypoint for inter-organ L7 routing
manifests/otel/collector.yaml      OTLP collector config (DSSE attr-promoting processor)
verify/cosign-init.sh              the fail-closed supply-chain gate
verify/dsse_verify.py              REAL ECDSA-P256 DSSE receipt verifier (make verify-dsse)
verify/run-acceptance.sh           exact per-image health + connected trace acceptance gate
verify/validate_route_ack.py       strict route acknowledgement validator
verify/validate_jaeger_trace.py    fresh parent-linked Jaeger graph validator
demo/greene-demo.sh                June 9 scripted demo
.github/workflows/ci.yml           PR CI: make up + make verify in kind
HONEST_GAPS.md                     everything currently stubbed and why
```

---

## Honest gaps

Read [`HONEST_GAPS.md`](./HONEST_GAPS.md) before you trust a green check.
Short version:

- **`killinchu` image is private.** Local operators must install a read-only
  runtime pull credential with `bootstrap/configure-ghcr-pull-auth.py`; without
  it, killinchu stays blocked and five-organ acceptance fails closed. CI derives
  the same runtime-only credential from its read-only `GITHUB_TOKEN`. The other
  4 organs pull anonymously. Killinchu is pinned to the keyless-signed,
  SLSA-attested protected-main digest; the unsigned legacy mutable tag is not
  accepted by this environment.
- **DSSE receipt verification is REAL** — `verify/dsse_verify.py`
  (`make verify-dsse`) verifies ECDSA-P256-SHA256 envelopes against
  `keys/cosign.pub`, emitting honest verdicts (`verified` / `unsigned-honest` /
  `FAIL`). The in-collector OTTL processor only promotes the attribute and points
  at this verify hook (OTTL cannot run crypto in-path).
- **The five-service trace is not operational yet.** The published A11oy image
  has no root fan-out route, Amaru cannot emit the required OTLP span, and Rosie's
  health path is not instrumented. `make trace` remains fail-closed until signed,
  immutable protected-source runtime successors provide those exact capabilities.
- **SLSA L1 is honest; stronger provenance is enforced only where independently
  accepted.** Every image is digest-pinned and identity-checked. Historical or
  unavailable source repositories remain explicitly labeled as such.

No secrets are committed to this repo. See `HONEST_GAPS.md` § Secrets.

---

## License

Apache-2.0. See [`LICENSE`](./LICENSE).


</details>


---

### Cross-references

- **First step for tower dry-run:** boot `szl-build-env` quickstart first, then proceed to [warhacker-demo](https://github.com/szl-holdings/warhacker-demo) for the full sovereign GPU tower deploy (`make tower-verify`).
- **Production deployment:** [szl-uds-deployment](https://github.com/szl-holdings/szl-uds-deployment) (archived 2026-08-31) was the UDS reference deployment; `szl-build-env` is the laptop dev environment that precedes it.
- **Formal proofs / kernel:** [lutar-lean](https://github.com/szl-holdings/lutar-lean) (kernel `c7c0ba17`)
- **Command platform:** [a11oy](https://github.com/szl-holdings/a11oy) · **Counter-UAS / drones:** [killinchu](https://github.com/szl-holdings/killinchu)

<!-- szl-doctrine-footer -->

---

### Citation & doctrine

Cite this work via [`CITATION.cff`](CITATION.cff). Math foundations: [szl-papers](https://github.com/szl-holdings/szl-papers) · [lutar-lean](https://github.com/szl-holdings/lutar-lean) (kernel `c7c0ba17`).

<sub>Λ Conjecture 1 (not a theorem) · 749/14/163 v11 LOCKED (kernel `c7c0ba17`) · SLSA L1 honest · Section 889 = 5 vendors · [SZL Holdings](https://a-11-oy.com) · Apache-2.0 code · CC-BY-4.0 papers</sub>
