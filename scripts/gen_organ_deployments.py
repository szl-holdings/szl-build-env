#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# © 2026 Lutar, Stephen P. — SZL Holdings · ORCID 0009-0001-0110-4173
# Doctrine v11 LOCKED 749/14/163 · Λ Conjecture 1 · SLSA L1 honest
"""Generate per-organ Deployment manifests that mount the cosign private key
as the SZL_COSIGN_PRIVATE_KEY_PEM env var (optional: true — honest fallback).

Run from repo root:  python3 scripts/gen_organ_deployments.py
"""
from __future__ import annotations
import os

ORGANS = ["a11oy", "sentra", "amaru", "killinchu", "rosie"]
NS = "szl"
# Pinned to the published + cosign-signed organ tag (matches manifests/organs/*.yaml
# and STATUS.md: ghcr.io/szl-holdings/<organ>:uds-v0.2.0, keyless Fulcio/Rekor signed).
# NOT `:latest` — a floating tag breaks the
# SLSA L1 (honest) / image-pin doctrine and is non-verifiable. Override only for local
# dev:  ORGAN_IMAGE_TAG=latest python3 scripts/gen_organ_deployments.py
IMAGE_TAG = os.environ.get("ORGAN_IMAGE_TAG", "uds-v0.2.0")
OUT = os.path.join(os.path.dirname(__file__), "..", "deploy", "organs")

TEMPLATE = """# SPDX-License-Identifier: Apache-2.0
# © 2026 Lutar, Stephen P. — SZL Holdings · ORCID 0009-0001-0110-4173
# Doctrine v11 LOCKED 749/14/163 · Lambda Conjecture 1 · SLSA L1 honest
# {organ} organ Deployment — cosign private key mounted as an ENV VAR (not a file).
# The secret is optional: when absent the organ emits honest UNSIGNED receipts.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {organ}
  namespace: {ns}
  labels:
    app.kubernetes.io/name: {organ}
    app.kubernetes.io/part-of: szl-organs
    szl.holdings/doctrine: v11-749-14-163
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: {organ}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {organ}
        app.kubernetes.io/part-of: szl-organs
    spec:
      containers:
        - name: {organ}
          image: ghcr.io/szl-holdings/{organ}:{image_tag}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          env:
            # ---- Cosign DSSE signing key (runtime secret) -------------------
            # Canonical name read first by szl_dsse._load_private_key().
            # optional: true => if the secret is not set, the organ falls back
            # to honest UNSIGNED receipts (signatures: []) — never fabricated.
            - name: SZL_COSIGN_PRIVATE_KEY_PEM
              valueFrom:
                secretKeyRef:
                  name: szl-cosign
                  key: cosign.key
                  optional: true   # honest fallback
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop: ["ALL"]
"""


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for organ in ORGANS:
        path = os.path.join(OUT, f"{organ}-deployment.yaml")
        with open(path, "w") as f:
            f.write(TEMPLATE.format(organ=organ, ns=NS, image_tag=IMAGE_TAG))
        print("wrote", path)


if __name__ == "__main__":
    main()

