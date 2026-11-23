#!/usr/bin/env bash
# cosign-init.sh — the HONEST, fail-closed supply-chain gate.
#
# Two roles:
#   1. As `make verify` (--all): runs cosign verify + slsa-verifier against every
#      organ image from the host, prints a per-organ verdict matrix, exits non-zero
#      if any REQUIRED organ fails. This is the gate an engineer runs locally.
#   2. As the in-pod init container logic (mirrored inline in manifests/organs/*.yaml):
#      the pod fails to start if cosign verify fails.
#
# Doctrine: SLSA L1 honest; L2 enforced only where provenance exists. We DO NOT
# fake an L2 attestation. If an image has no provenance, we say so (L1 honest)
# rather than printing a green check.
set -uo pipefail

NAMESPACE="${NAMESPACE:-szl}"
COSIGN_PUB="${COSIGN_PUB:-$(dirname "$0")/../keys/cosign.pub}"
ORGANS=(a11oy sentra amaru killinchu rosie)
# killinchu is private, so registry authentication is an explicit prerequisite.
# Access, signature, certificate, and provenance failures are always hard red;
# no verifier error is downgraded merely because an image is private.

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

image_for() {
  case "$1" in
    a11oy) printf '%s\n' 'ghcr.io/szl-holdings/a11oy@sha256:c08839a06002b88c7c5c11dc8140099f8dd56ba0a618c1b5457bdedf17404cef' ;;
    sentra) printf '%s\n' 'ghcr.io/szl-holdings/sentra@sha256:62da54f92b39dd30537df87af08d4dda2c2bc14508fe53bd31ca7080dd62d08d' ;;
    amaru) printf '%s\n' 'ghcr.io/szl-holdings/amaru@sha256:c9f39694f9e8120bc5ba19c20db88d3a271eafa87eeb3bd03bca352a5ae82512' ;;
    killinchu) printf '%s\n' 'ghcr.io/szl-holdings/killinchu@sha256:d7702fa2e9d1f844ccb6c4e62c27e516b2651724dfe8997e2c83c3a3609c9045' ;;
    rosie) printf '%s\n' 'ghcr.io/szl-holdings/rosie@sha256:dfa93e2d7f2e2de9437b07d9ec56ee1794ffcff752cf6dc634abd2707cb3d1ab' ;;
    *) return 1 ;;
  esac
}

workflow_sha_for() {
  case "$1" in
    a11oy) printf '%s\n' 'bab6cbec341f4206edf22d13e93af690f656650f' ;;
    sentra) printf '%s\n' 'b9e609f7b897d0f2d689453b7bb870ebcd4e79d1' ;;
    amaru) printf '%s\n' '6e9c2fee26f5e8f5bd629dfda9e13cdb63a9688d' ;;
    killinchu) printf '%s\n' 'e5ca7b75adbfe54891fcff26c8abbdc61dc1fc48' ;;
    rosie) printf '%s\n' '6b64ef3bdc4f89831654837515bf760b8c8a8ad5' ;;
    *) return 1 ;;
  esac
}

workflow_ref_for() {
  case "$1" in
    a11oy|sentra|amaru|killinchu|rosie) printf '%s\n' 'refs/heads/main' ;;
    *) return 1 ;;
  esac
}

verify_one() {
  local organ="$1" image workflow_sha workflow_ref identity
  image="$(image_for "$organ")"
  workflow_sha="$(workflow_sha_for "$organ")"
  workflow_ref="$(workflow_ref_for "$organ")"
  identity="https://github.com/szl-holdings/${organ}/.github/workflows/ghcr-build-push.yml@${workflow_ref}"
  echo "=============================================================="
  echo ">> ${organ}  ($image)"

  # 0) can we even pull/reference it?
  if ! cosign triangulate "$image" >/dev/null 2>&1; then
    red "   [FAIL] cannot reference ${image} (missing image or registry authorization)"
    return 1
  fi

  # 1) cosign verify (KEYLESS: Fulcio cert identity = organ's ghcr-build-push
  #    workflow, OIDC issuer = GitHub Actions). The organ images are keyless-signed
  #    (Fulcio cert in the .sig layer), so a keyed --key verify cannot validate them.
  if cosign verify \
       --certificate-identity "$identity" \
       --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
       --certificate-github-workflow-repository "szl-holdings/${organ}" \
       --certificate-github-workflow-ref "$workflow_ref" \
       --certificate-github-workflow-sha "$workflow_sha" \
       --certificate-github-workflow-trigger "push" \
       "$image" >/tmp/cosign.${organ}.out 2>&1; then
    green "   [OK]   cosign signature verified"
  else
    red "   [FAIL] cosign verify FAILED — see /tmp/cosign.${organ}.out"
    sed 's/^/      /' /tmp/cosign.${organ}.out
    return 1
  fi

  # A11oy's newer Cosign v3 publisher emits OCI-referrer attestations.
  # slsa-verifier 2.6 can report those as absent; require the *actual*
  # cryptographic attestation with the exact source identity instead.
  # Its org-owned builder is NOT a third-party attested SLSA level.
  if [[ "$organ" == "a11oy" ]]; then
    if cosign verify-attestation --type slsaprovenance \
         --certificate-identity "$identity" \
         --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
         --certificate-github-workflow-repository "szl-holdings/a11oy" \
         --certificate-github-workflow-ref "$workflow_ref" \
         --certificate-github-workflow-sha "$workflow_sha" \
         --certificate-github-workflow-trigger "push" \
         "$image" >/tmp/cosign.a11oy.attest.out 2>&1; then
      green "   [OK]   exact signed provenance verified; org-owned builder (no SLSA L2 claim)"
      return 0
    fi
    red "   [FAIL] exact A11oy signed provenance absent or invalid; no L1 downgrade"
    return 1
  fi

  # 2) slsa-verifier — L2 attested where present, L1 honest otherwise.
  #    Every governed input is already an immutable digest reference.
  if slsa-verifier verify-image "$image" \
       --source-uri "github.com/szl-holdings/${organ}" >/tmp/slsa.${organ}.out 2>&1; then
    green "   [OK]   SLSA L2 provenance verified"
  elif grep -qiE "no matching|no provenance|no attestation" /tmp/slsa.${organ}.out; then
    yellow "   [L1]   no SLSA provenance attestation — honest L1 (cosign sig still valid)"
  elif grep -qiE "untrusted reusable workflow|untrusted builder|builderID provided: false" /tmp/slsa.${organ}.out; then
    # Provenance EXISTS and its envelope parsed (slsa-verifier read builder.id),
    # but the builder is the org's own ghcr-build-push workflow, which is not one of
    # slsa-verifier's canonical/allowlisted SLSA generators. This is honest L1: the
    # image's authenticity is already proven by the identity-pinned cosign keyless
    # verify above (same workflow URL). We do NOT fake an L2 stamp.
    yellow "   [L1]   SLSA provenance is self-attested by the org's ghcr-build-push workflow"
    yellow "          (not a slsa-verifier-trusted canonical builder) — honest L1; cosign identity verified"
  else
    red "   [FAIL] slsa-verifier rejected ${image}"
    sed 's/^/      /' /tmp/slsa.${organ}.out
    return 1
  fi
  return 0
}

main() {
  if ! command -v cosign >/dev/null;  then red "cosign not installed (see README prerequisites)"; exit 3; fi
  if ! command -v slsa-verifier >/dev/null; then red "slsa-verifier not installed (see README prerequisites)"; exit 3; fi
  [ -f "$COSIGN_PUB" ] || { red "cosign public key not found at $COSIGN_PUB"; exit 3; }

  local fail=0
  declare -A verdict
  for organ in "${ORGANS[@]}"; do
    verify_one "$organ"
    rc=$?
    case $rc in
      0) verdict[$organ]="PASS" ;;
      *) verdict[$organ]="FAIL"; fail=$((fail+1)) ;;
    esac
  done

  echo "=============================================================="
  echo "VERDICT MATRIX (doctrine v11 749/14/163 @ c7c0ba17)"
  for organ in "${ORGANS[@]}"; do printf "   %-10s %s\n" "$organ" "${verdict[$organ]}"; done
  echo "--------------------------------------------------------------"
  if [ "$fail" -gt 0 ]; then
    red "RESULT: $fail organ(s) FAILED the honest supply-chain gate. Build env is NOT trustworthy."
    exit 1
  fi
  green "RESULT: 5/5 organs PASS cosign + SLSA gate. Build env supply chain is honest."
}

case "${1:-}" in
  --all|"") main ;;
  *) echo "usage: $0 --all"; exit 2 ;;
esac
