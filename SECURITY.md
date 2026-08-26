# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Please report security vulnerabilities via email to **security@szlholdings.ai**.
Include affected component, reproduction steps, and impact. We acknowledge within
2 business days and aim to remediate or provide mitigation guidance within 30 days.

## Disclosure

We follow coordinated disclosure. Please give us a reasonable window to remediate
before any public disclosure.

## Registry Credentials

Never pass a GHCR token as a command-line argument or commit a Docker config or
Kubernetes pull Secret. `bootstrap/configure-ghcr-pull-auth.py` consumes a
read-only credential from standard input and creates a fresh immutable Secret in
the `szl` namespace. The credential is referenced only by the private killinchu
pod, mounted read-only only in its Cosign verification init container, and is not
mounted into the application container. CI rejects forked private-image acceptance
and uses only the job-scoped `GITHUB_TOKEN` permission `packages: read`.

The helper refuses to replace an existing Secret. Rotate intentionally by deleting
`szl/szl-ghcr-pull`, then run the helper again with the replacement credential.

---
Doctrine v11 LOCKED · 749/14/163 · kernel c7c0ba17 · Λ = Conjecture 1 · SLSA L1 honest
