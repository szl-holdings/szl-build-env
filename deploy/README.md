<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- © 2026 Lutar, Stephen P. — SZL Holdings · ORCID 0009-0001-0110-4173 · Doctrine v11 LOCKED 749/14/163 · Λ Conjecture 1 · SLSA L1 honest -->

# SZL Organ Deployments — Cosign Signing Key Wiring

These manifests deploy the five SZL flagship organs (`a11oy`, `sentra`, `amaru`,
`killinchu`, `rosie`) into the `szl` namespace and mount the cosign **private**
signing key as the env var **`SZL_COSIGN_PRIVATE_KEY_PEM`** — **as an env var,
not a mounted file**.

## The env wiring (every organ)

```yaml
env:
  - name: SZL_COSIGN_PRIVATE_KEY_PEM
    valueFrom:
      secretKeyRef:
        name: szl-cosign
        key: cosign.key
        optional: true   # honest fallback
```

`optional: true` is the honesty contract: if the `szl-cosign` secret is not
present, the pod still starts and the organ emits **UNSIGNED** DSSE receipts
(`signatures: []`, `honesty: UNSIGNED`). No signature is ever fabricated. The
moment the secret exists, every organ's `szl_dsse` signer flips to **real
ECDSA-P256-SHA256** signatures.

The separate `szl-ghcr-pull` Secret authenticates image pulls for the private
killinchu image. It is not a signing key and is referenced by no public-image
organ. Create it after the `szl` namespace exists by piping a `read:packages`
token to `bootstrap/configure-ghcr-pull-auth.py`; never place that token on the
command line. See the root README's authenticated quickstart.

## Create the secret (founder, once)

```bash
kubectl create secret generic szl-cosign \
  --from-file=cosign.key=cosign.key.pem \
  --namespace szl
kubectl rollout restart deployment a11oy sentra amaru killinchu rosie -n szl
```

`cosign.key.pem` is a plain (unencrypted) PKCS#8 PEM of the cosign **private**
key — see the Cosign Bootstrap founder runbook. The private key is **never**
committed to this repo.

## Layouts

- `deploy/organs/*-deployment.yaml` — one Deployment per organ (per-file).
- `deploy/organs/kustomization.yaml` — `kubectl apply -k deploy/organs`.
- `deploy/helm/values-cosign.yaml` — Helm values overlay injecting the same env var.
- `scripts/gen_organ_deployments.py` — regenerates the per-organ manifests.

## Verify the flip

```bash
# public key is published at szl-holdings/.github keys/cosign.pub
cosign verify-blob --key keys/cosign.pub --signature <sig> <payload>
```
