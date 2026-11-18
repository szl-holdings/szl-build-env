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

NS = "szl"
# Every organ is selected by a reviewed immutable digest. There is deliberately
# no environment-variable tag override: governed generation must not be retagged.
ORGAN_SPECS = {
    "a11oy": {
        "image": "ghcr.io/szl-holdings/a11oy@sha256:c08839a06002b88c7c5c11dc8140099f8dd56ba0a618c1b5457bdedf17404cef",
        "container_port": 7860,
        "private": False,
        # 986 MB signed-model image; kind acceptance proved 512Mi OOMKilled.
        "memory_request": "1Gi",
        "memory_limit": "3Gi",
    },
    "sentra": {
        "image": "ghcr.io/szl-holdings/sentra@sha256:60a0efc14366ba392bfe3f3cd4196863fe148bb87a17428be6a57f0a05ac3639",
        "container_port": 7860,
        "private": False,
    },
    "amaru": {
        "image": "ghcr.io/szl-holdings/amaru@sha256:53301e26adcde49e73df28d8c3b790f2496da9d495307fe8587ffa7452b289ff",
        "container_port": 7860,
        "private": False,
    },
    "killinchu": {
        "image": "ghcr.io/szl-holdings/killinchu@sha256:d7702fa2e9d1f844ccb6c4e62c27e516b2651724dfe8997e2c83c3a3609c9045",
        "container_port": 7860,
        "private": True,
    },
    "rosie": {
        "image": "ghcr.io/szl-holdings/rosie@sha256:1984a15f53c2e1b91c7dafaa0ed5df9148d57e3e86eb73db879c2b0443302848",
        "container_port": 7860,
        "private": False,
    },
}
ORGANS = tuple(ORGAN_SPECS)
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
      automountServiceAccountToken: false
{runtime_auth}      containers:
        - name: {organ}
          image: {image}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: {container_port}
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
              memory: {memory_request}
            limits:
              cpu: "1"
              memory: {memory_limit}
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
        spec = ORGAN_SPECS[organ]
        image = spec["image"]
        container_port = spec["container_port"]
        runtime_auth = ""
        if spec["private"]:
            runtime_auth = """      # Runtime registry auth is restricted to the one private-image pod.
      imagePullSecrets:
        - name: szl-ghcr-pull
"""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                TEMPLATE.format(
                    organ=organ,
                    ns=NS,
                    image=image,
                    container_port=container_port,
                    runtime_auth=runtime_auth,
                    memory_request=spec.get("memory_request", "128Mi"),
                    memory_limit=spec.get("memory_limit", "512Mi"),
                )
            )
        print("wrote", path)


if __name__ == "__main__":
    main()
