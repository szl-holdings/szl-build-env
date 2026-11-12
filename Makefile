# szl-build-env — local 5-organ + Istio ambient + OTel stack on kind
# Doctrine v11 LOCKED 749/14/163 @ kernel c7c0ba17. NOT Iron Bank. SLSA L1 honest (L2 not yet produced).
SHELL := /bin/bash
.DEFAULT_GOAL := help

# ---- pins (single source of truth) ------------------------------------------
ISTIO_VERSION   ?= 1.25.0
# SHA-256 of istio-<version>-<platform>.tar.gz for the release below, copied from the
# release's <asset>.sha256 files (SCPIN-CURL-1). Another ISTIO_VERSION has no committed
# checksum and is refused.
ISTIO_SHA256_VERSION     := 1.25.0
ISTIO_SHA256_LINUX_AMD64 := 449dac28138c890a592f66420577e0ce299a6d8f89b64d528ccb3135a5c7d0ec
ISTIO_SHA256_LINUX_ARM64 := ad536def76062d5a4f83b2156e09cb80609fdd1d6e2fbcf1d7d831b233c39fae
ISTIO_SHA256_OSX_AMD64   := 734763685696154c8cb5bba186878b395c7fc4c3f3401b14ee6430991a1375ff
ISTIO_SHA256_OSX_ARM64   := ebac1c4ee977a45fc7b8f6c5a5bbfd28bcc168734c96d1f466a34a3d721a5a41
OTEL_VERSION    ?= 0.135.0
GATEWAY_API_VERSION ?= v1.2.1
KIND_CLUSTER    ?= szl-build-env
BUNDLE          ?= oci://ghcr.io/szl-holdings/szl-uds-bundle:uds-v0.2.0
ORGAN_TAG       ?= uds-v0.2.0
NAMESPACE       ?= szl
BIN             := $(CURDIR)/bin
ISTIOCTL        := $(BIN)/istioctl
export PATH     := $(BIN):$(PATH)

ORGANS := a11oy sentra amaru killinchu rosie

.PHONY: help up verify verify-dsse router-acceptance test-router-acceptance trace down demo istioctl cluster mesh otel organs clean status cosign-key

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

istioctl: ## Download pinned istioctl into ./bin
	@mkdir -p $(BIN)
	@if [ ! -x "$(ISTIOCTL)" ]; then \
	  echo ">> downloading istioctl $(ISTIO_VERSION)"; \
	  [ "$(ISTIO_VERSION)" = "$(ISTIO_SHA256_VERSION)" ] || { echo ">> no committed SHA-256 for ISTIO_VERSION=$(ISTIO_VERSION); refusing" >&2; exit 1; }; \
	  case "$$(uname -s)/$$(uname -m)" in \
	    Linux/x86_64) plat=linux-amd64; want="$(ISTIO_SHA256_LINUX_AMD64)" ;; \
	    Linux/aarch64|Linux/arm64) plat=linux-arm64; want="$(ISTIO_SHA256_LINUX_ARM64)" ;; \
	    Darwin/x86_64) plat=osx; want="$(ISTIO_SHA256_OSX_AMD64)" ;; \
	    Darwin/arm64) plat=osx-arm64; want="$(ISTIO_SHA256_OSX_ARM64)" ;; \
	    *) echo ">> no committed SHA-256 for $$(uname -s)/$$(uname -m); refusing" >&2; exit 1 ;; \
	  esac; \
	  tgz="istio-$(ISTIO_VERSION)-$$plat.tar.gz"; \
	  curl -fsSLo "$$tgz" "https://github.com/istio/istio/releases/download/$(ISTIO_VERSION)/$$tgz" || exit 1; \
	  got="$$( (sha256sum "$$tgz" 2>/dev/null || shasum -a 256 "$$tgz") | cut -d' ' -f1)"; \
	  [ "$$got" = "$$want" ] || { echo ">> $$tgz SHA-256 $$got does not match the committed value; refusing" >&2; rm -f "$$tgz"; exit 1; }; \
	  tar -xzf "$$tgz" || exit 1; rm -f "$$tgz"; \
	  cp istio-$(ISTIO_VERSION)/bin/istioctl $(ISTIOCTL); \
	  chmod +x $(ISTIOCTL); \
	fi
	@$(ISTIOCTL) version --remote=false

cluster: ## Create the single-node kind cluster
	@if ! kind get clusters 2>/dev/null | grep -qx "$(KIND_CLUSTER)"; then \
	  echo ">> creating kind cluster $(KIND_CLUSTER)"; \
	  kind create cluster --config kind/cluster.yaml --wait 120s; \
	else echo ">> kind cluster $(KIND_CLUSTER) already exists"; fi
	@kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@kubectl label namespace $(NAMESPACE) istio.io/dataplane-mode=ambient --overwrite

mesh: istioctl cluster ## Install Istio ambient mesh (ztunnel + CNI), no sidecars
	@echo ">> installing Gateway API CRDs ($(GATEWAY_API_VERSION))"
	@kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/$(GATEWAY_API_VERSION)/standard-install.yaml
	@kubectl wait --for=condition=Established --timeout=60s \
	  crd/gateways.gateway.networking.k8s.io \
	  crd/httproutes.gateway.networking.k8s.io
	@ISTIO_VERSION=$(ISTIO_VERSION) ISTIOCTL=$(ISTIOCTL) NAMESPACE=$(NAMESPACE) \
	  bash bootstrap/install-istio-ambient.sh
	@kubectl apply -n $(NAMESPACE) -f manifests/mesh/waypoint.yaml

otel: cluster ## Install OpenTelemetry Collector + Jaeger
	@OTEL_VERSION=$(OTEL_VERSION) NAMESPACE=$(NAMESPACE) \
	  bash bootstrap/install-otel-collector.sh

cosign-key: cluster ## Load the szl-holdings cosign PUBLIC key as a configmap (no secret)
	@kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@kubectl -n $(NAMESPACE) create configmap szl-cosign-pub \
	  --from-file=cosign.pub=keys/cosign.pub \
	  --dry-run=client -o yaml | kubectl apply -f -

organs: cluster cosign-key ## Deploy the 5 organ Deployments (cosign-gated)
	@for o in $(ORGANS); do \
	  echo ">> applying organ $$o"; \
	  kubectl apply -n $(NAMESPACE) -f manifests/organs/$$o.yaml; \
	done
	@echo ">> waiting for organs to settle (killinchu may block; see HONEST_GAPS.md)"
	@kubectl -n $(NAMESPACE) rollout status deploy/a11oy --timeout=180s || true
	@kubectl -n $(NAMESPACE) get pods -o wide

up: cluster mesh otel organs ## Bring up the entire stack
	@echo ""
	@echo "==> stack up. Jaeger UI: http://localhost:16686  |  demo gateway: http://localhost:8080"
	@$(MAKE) --no-print-directory status

status: ## Show pod + mesh status
	@echo "---- pods ----"; kubectl -n $(NAMESPACE) get pods -o wide || true
	@echo "---- ztunnel ----"; kubectl -n istio-system get pods -l app=ztunnel || true
	@echo "---- waypoint ----"; kubectl -n $(NAMESPACE) get gateways.gateway.networking.k8s.io || true

verify: ## Run the honest cosign + slsa-verifier supply-chain gate for every organ
	@ORGAN_TAG=$(ORGAN_TAG) NAMESPACE=$(NAMESPACE) bash verify/cosign-init.sh --all

verify-dsse: ## Verify a DSSE receipt envelope for real (ECDSA-P256; honest verdicts)
	@if [ -z "$(RECEIPT)" ]; then \
	  echo "usage: make verify-dsse RECEIPT=path/to/receipt.json [PUBKEY=keys/cosign.pub]"; exit 2; fi
	@python3 verify/dsse_verify.py "$(RECEIPT)" --pubkey "$(or $(PUBKEY),keys/cosign.pub)"

router-acceptance: ## Read router source, configuration admission and model catalog (no inference)
	@python3 verify/router_acceptance.py --target "$(ROUTER_URL)" \
	  --expected-revision "$(ROUTER_REVISION)" --model "$(ROUTER_MODEL)"

test-router-acceptance: ## Exercise router acceptance against local HTTP fixtures only
	@python3 -m pytest -q verify/test_router_acceptance.py

trace: ## Send a request and dump the cross-organ traceparent trace tree
	@NAMESPACE=$(NAMESPACE) bash verify/run-acceptance.sh

demo: ## Scripted June 9 demo: up + verify + seeded request + trace + DSSE chain
	@bash demo/greene-demo.sh

down: ## Delete the kind cluster and all resources
	@kind delete cluster --name $(KIND_CLUSTER) || true
	@echo ">> cluster deleted."

clean: down ## down + remove downloaded binaries
	@rm -rf $(BIN) istio-$(ISTIO_VERSION)
