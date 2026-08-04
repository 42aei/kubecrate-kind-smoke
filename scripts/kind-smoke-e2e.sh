#!/usr/bin/env bash
# Disposable kind smoke run for kubecrate-kind-smoke.
#
# Proves a pinned kubecrate substrate (Flux + platform services) reconciles and
# stays healthy while this repository's smoke fixtures and CrateCheck are
# layered on top, using a unique disposable kind cluster per run:
#
#   1. create a unique disposable kind cluster and prove the active context
#   2. bootstrap Flux from the pinned flux2 Helm chart
#   3. point a Flux GitRepository at the pinned kubecrate commit and reconcile
#      compositions/vanilla/entrypoint (platform services only)
#   4. point a second Flux GitRepository (kind-smoke) at this repository at the
#      selected commit and apply entrypoint/ from the checkout
#   5. wait for every smoke Flux Kustomization and CrateCheck to become ready
#   6. poll CrateCheck /status.json through a port-forward until exact green
#   7. controlled red: suspend external-secrets-operator-smoke, delete the
#      eso-smoke-projection ExternalSecret, require exactly the eso-red set
#   8. restore, require exact green again, retain status artifacts
#   9. delete the exact cluster and prove absence
#
# Required tools: git kind kubectl kustomize helm flux curl python3 docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

KUBECRATE_REPO_URL="${KUBECRATE_REPO_URL:-https://github.com/42aei/kubecrate.git}"
# Default pin: latest supported Kubecrate release tag.
KUBECRATE_REF="${KUBECRATE_REF:-v0.4.0}"
SMOKE_REPO_URL="${SMOKE_REPO_URL:-https://github.com/42aei/kubecrate-kind-smoke.git}"
SMOKE_REF="${SMOKE_REF:-$(git rev-parse --verify 'HEAD^{commit}')}"
CLUSTER_NAME="${CLUSTER_NAME:-kubecrate-smoke-$(date +%s)-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)}"
CONTEXT="kind-$CLUSTER_NAME"
FLUX_NAMESPACE="flux-system"
FLUX_CHART="${FLUX_CHART:-oci://ghcr.io/fluxcd-community/charts/flux2}"
FLUX_CHART_VERSION="${FLUX_CHART_VERSION:-2.18.4}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT/.tmp/kind-smoke-e2e}"
PORT_FORWARD_LOCAL_PORT="${PORT_FORWARD_LOCAL_PORT:-18080}"
WAIT_LONG="${WAIT_LONG:-300s}"
STATUS_DEADLINE_SECONDS="${STATUS_DEADLINE_SECONDS:-300}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
PLATFORM_KUSTOMIZATIONS="external-secrets-operator envoy-gateway cert-manager kyverno"
SMOKE_KUSTOMIZATIONS="external-secrets-operator-smoke cratecheck cert-manager-local-issuer-smoke envoy-gateway-smoke kyverno-smoke-policy kyverno-smoke"

PF_PID=""
PHASE="preflight"
CREATED=0

log() { printf 'kind-smoke-e2e: %s\n' "$*"; }
fail() { printf 'kind-smoke-e2e: ERROR phase=%s message=%s\n' "$PHASE" "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }

kc() { kubectl --context "$CONTEXT" "$@"; }

assert_context() {
  local actual
  actual="$(kubectl config current-context 2>/dev/null || true)"
  test "$actual" = "$CONTEXT" || fail "expected current context $CONTEXT, got ${actual:-none}"
}

capture_diagnostics() {
  mkdir -p "$ARTIFACTS_DIR"
  kc get gitrepositories -n "$FLUX_NAMESPACE" >"$ARTIFACTS_DIR/diagnostics-gitrepositories.txt" 2>&1 || true
  kc get kustomizations -n "$FLUX_NAMESPACE" >"$ARTIFACTS_DIR/diagnostics-kustomizations.txt" 2>&1 || true
  kc get helmreleases -A >"$ARTIFACTS_DIR/diagnostics-helmreleases.txt" 2>&1 || true
  kc get events -A --field-selector type=Warning >"$ARTIFACTS_DIR/diagnostics-warning-events.txt" 2>&1 || true
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if test -n "$PF_PID"; then kill "$PF_PID" >/dev/null 2>&1 || true; PF_PID=""; fi
  if test $rc -ne 0 && test "$CREATED" = 1; then capture_diagnostics || true; fi
  if test "$CREATED" = 1; then
    if test "$KEEP_CLUSTER" = 1; then
      log "KEEP_CLUSTER=1: retaining cluster $CLUSTER_NAME (context $CONTEXT)"
      if test $rc -ne 0; then
        log "WARNING: run failed; retained cluster may be mid-controlled-red, with failing fixtures and red-state objects still present"
      fi
    else
      kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
      if kind get clusters | grep -Fx "$CLUSTER_NAME" >/dev/null 2>&1; then
        printf 'kind-smoke-e2e: ERROR phase=cleanup message=cluster cleanup failed: %s\n' "$CLUSTER_NAME" >&2
        exit 1
      fi
      log "deleted disposable kind cluster: $CLUSTER_NAME"
    fi
  fi
  exit $rc
}

check_prereqs() {
  PHASE="prerequisites"
  for c in git kind kubectl kustomize helm flux curl python3 docker; do require "$c"; done
  docker info >/dev/null 2>&1 || fail "Docker daemon unavailable"
  [[ "$CLUSTER_NAME" =~ ^kubecrate-smoke[a-z0-9-]*$ ]] && test ${#CLUSTER_NAME} -le 63 \
    || fail "refusing non-smoke cluster identity $CLUSTER_NAME; allowed: kubecrate-smoke[-<lowercase-alphanumeric-segments>]"
  [[ "$KUBECRATE_REF" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+][0-9A-Za-z.-]+)?$ ]] || fail "KUBECRATE_REF must be an exact SemVer tag"
  [[ "$SMOKE_REF" =~ ^[0-9a-f]{40}$ ]] || fail "SMOKE_REF must be a full 40-character commit SHA"
  if kind get clusters | grep -Fx "$CLUSTER_NAME" >/dev/null 2>&1; then
    fail "cluster $CLUSTER_NAME already exists; refusing to touch a cluster this run did not create"
  fi
  test -z "$(git status --porcelain=v1 --untracked-files=no)" \
    || log "warning: checkout has uncommitted changes; Flux reconciles fixtures from $SMOKE_REF, not the working tree"
}

verify_remote_identity() {
  PHASE="source-identity"
  if ! git ls-remote --tags --refs "$KUBECRATE_REPO_URL" "refs/tags/$KUBECRATE_REF" 2>/dev/null | grep -F "refs/tags/$KUBECRATE_REF" >/dev/null; then
    fail "kubecrate tag $KUBECRATE_REF is not advertised by $KUBECRATE_REPO_URL"
  fi
  if ! git ls-remote "$SMOKE_REPO_URL" 2>/dev/null | grep "$SMOKE_REF" >/dev/null; then
    fail "smoke commit $SMOKE_REF is not advertised by $SMOKE_REPO_URL; push it first"
  fi
}

create_cluster() {
  PHASE="cluster-create"
  kind create cluster --name "$CLUSTER_NAME"
  CREATED=1
  assert_context
  kc wait --for=condition=Ready node --all --timeout=180s
}

install_flux() {
  PHASE="flux-bootstrap"
  helm upgrade --install flux-system "$FLUX_CHART" \
    --kube-context "$CONTEXT" \
    --version "$FLUX_CHART_VERSION" \
    --namespace "$FLUX_NAMESPACE" --create-namespace \
    --wait --timeout "$WAIT_LONG"
  local d
  for d in source-controller kustomize-controller helm-controller; do
    kc wait --for=condition=Available "deployment/$d" -n "$FLUX_NAMESPACE" --timeout=180s
  done
}

apply_kubecrate_substrate() {
  PHASE="kubecrate-substrate"
  # The kubecrate Vanilla entrypoint children reference GitRepository
  # flux-system-sync by name; provide it as the pinned public kubecrate source.
  kc apply -f - <<EOF
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: flux-system-sync
  namespace: $FLUX_NAMESPACE
spec:
  interval: 1m0s
  url: $KUBECRATE_REPO_URL
  ref:
    tag: $KUBECRATE_REF
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: kubecrate-vanilla
  namespace: $FLUX_NAMESPACE
spec:
  interval: 1m0s
  path: ./compositions/vanilla/entrypoint
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system-sync
  timeout: 5m0s
  wait: false
EOF
  kc wait --for=condition=Ready "gitrepository/flux-system-sync" -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  kc wait --for=condition=Ready "kustomization/kubecrate-vanilla" -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  local k
  for k in $PLATFORM_KUSTOMIZATIONS; do
    kc wait --for=condition=Ready "kustomization/$k" -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  done
  log "kubecrate substrate Ready at $KUBECRATE_REF"
}

apply_smoke_fixtures() {
  PHASE="smoke-fixtures"
  kc apply -f - <<EOF
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: kind-smoke
  namespace: $FLUX_NAMESPACE
spec:
  interval: 1m0s
  url: $SMOKE_REPO_URL
  ref:
    commit: $SMOKE_REF
EOF
  kc apply -k "$ROOT/entrypoint"
  kc wait --for=condition=Ready "gitrepository/kind-smoke" -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  local k
  for k in $SMOKE_KUSTOMIZATIONS; do
    kc wait --for=condition=Ready "kustomization/$k" -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  done
  kc wait --for=condition=Available deployment/cratecheck -n cratecheck --timeout="$WAIT_LONG"
  log "smoke fixtures Ready at $SMOKE_REF"
}

start_port_forward() {
  PHASE="port-forward"
  kc port-forward svc/cratecheck -n cratecheck "$PORT_FORWARD_LOCAL_PORT:8080" >/dev/null 2>&1 &
  PF_PID=$!
  local deadline=$((SECONDS + 30))
  until curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$PORT_FORWARD_LOCAL_PORT/healthz" >/dev/null 2>&1; do
    kill -0 "$PF_PID" 2>/dev/null || { PF_PID=""; fail "port-forward exited before becoming ready"; }
    test $SECONDS -lt $deadline || fail "port-forward did not become ready"
    sleep 1
  done
}

# poll_status <phase-name> <artifact-file>: poll /status.json until the exact
# CrateCheck contract for the phase holds, bounded by STATUS_DEADLINE_SECONDS.
poll_status() {
  local phase="$1" artifact="$2" deadline last_rc=1
  mkdir -p "$ARTIFACTS_DIR"
  deadline=$((SECONDS + STATUS_DEADLINE_SECONDS))
  while test $SECONDS -lt $deadline; do
    if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$PORT_FORWARD_LOCAL_PORT/status.json" >"$ARTIFACTS_DIR/$artifact" 2>/dev/null \
      && python3 "$ROOT/scripts/validate-cratecheck-status.py" --phase "$phase" "$ARTIFACTS_DIR/$artifact" >/dev/null 2>&1; then
      log "status phase=$phase reached"
      return 0
    fi
    sleep 5
  done
  python3 "$ROOT/scripts/validate-cratecheck-status.py" --phase "$phase" "$ARTIFACTS_DIR/$artifact" >&2 || true
  fail "CrateCheck status did not reach phase=$phase within ${STATUS_DEADLINE_SECONDS}s"
}

prove_green_red_green() {
  PHASE="green-baseline"
  poll_status green status-green.json
  PHASE="controlled-red"
  flux --context "$CONTEXT" suspend kustomization external-secrets-operator-smoke -n "$FLUX_NAMESPACE" >/dev/null
  kc delete externalsecret eso-smoke-projection -n kubecrate-system --wait=true --timeout=60s
  poll_status eso-red status-red.json
  PHASE="restore-green"
  flux --context "$CONTEXT" resume kustomization external-secrets-operator-smoke -n "$FLUX_NAMESPACE" >/dev/null
  flux --context "$CONTEXT" reconcile kustomization external-secrets-operator-smoke -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG" >/dev/null
  kc wait --for=condition=Ready kustomization/external-secrets-operator-smoke -n "$FLUX_NAMESPACE" --timeout="$WAIT_LONG"
  poll_status green status-restored.json
}

main() {
  trap cleanup EXIT INT TERM
  check_prereqs
  verify_remote_identity
  create_cluster
  install_flux
  apply_kubecrate_substrate
  apply_smoke_fixtures
  start_port_forward
  prove_green_red_green
  PHASE="done"
  log "pass cluster=$CLUSTER_NAME kubecrate=$KUBECRATE_REF smoke=$SMOKE_REF artifacts=$ARTIFACTS_DIR"
}

main "$@"
