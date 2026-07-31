# kubecrate-kind-smoke

Consumer-side smoke suite for [Kubecrate](https://github.com/42aei/kubecrate) substrate updates.

Kubecrate stays the upstream platform/application-service distribution. This repository owns the
kind-local smoke fixtures that prove a pinned Kubecrate substrate actually works when consumed:
the smoke resources, the CrateCheck status application that evaluates them, and an invokable
kind CI flow that runs the whole stack on a disposable cluster.

## Consumption contract

A consumer Flux root (a cluster repository or CI) registers **two** Git sources:

1. a `GitRepository` for kubecrate, pinned to the substrate commit under test, reconciling
   `compositions/vanilla/entrypoint` (platform services: External-Secrets Operator,
   Envoy Gateway, cert-manager, Kyverno);
2. a `GitRepository` named `kind-smoke` for **this** repository, pinned to the smoke suite
   commit, reconciling this repository's `entrypoint/`.

Every Flux `Kustomization` under `entrypoint/` uses `sourceRef: GitRepository/kind-smoke`, and
its `dependsOn` entries name the kubecrate-side platform service Kustomizations
(`external-secrets-operator`, `envoy-gateway`, `cert-manager`, `kyverno`) that the kubecrate
Vanilla entrypoint reconciles. The smoke layer therefore orders itself after the substrate it
exercises without owning any part of it.

Reconciliation order:

```text
kubecrate substrate (compositions/vanilla/entrypoint)
  external-secrets-operator ──► external-secrets-operator-smoke ──► cratecheck
  cert-manager ────────────────────────────────┘        │
                                                        ▼
                                       cert-manager-local-issuer-smoke
  envoy-gateway ────────────────────────────────────────▼
                                       envoy-gateway-smoke
  kyverno ──► kyverno-smoke-policy ──► kyverno-smoke
```

## Paths

- `entrypoint/` — Flux `Kustomization` objects for all smoke fixtures.
- `platform-services/external-secrets-operator/` — ESO smoke Secret, SecretStore RBAC, ExternalSecret.
- `platform-services/envoy-gateway/` — smoke EnvoyProxy, GatewayClass, Gateway (HTTP+HTTPS), HTTPRoute, ReferenceGrant.
- `platform-services/cert-manager/local-issuer/` — local self-signed → CA → `cratecheck-tls` issuer chain that gives the Envoy smoke HTTPS path a TLS cert.
- `platform-services/kyverno/policy/` — `require-ns-label` ClusterPolicy fixture.
- `platform-services/kyverno/consumer/` — labeled `kyverno-smoke-allowed` Namespace fixture.
- `application-services/cratecheck/` — CrateCheck (`ghcr.io/42aei/cratecheck:v1`) plus its check config; `/status.json` is the single green/red status surface for both the platform HelmReleases and the smoke resources.
- `scripts/validate-cratecheck-status.py` — exact JSON contract validator for `/status.json` (green and controlled-red phases).
- `scripts/kind-smoke-e2e.sh` — the runnable disposable kind flow (also used by CI).

Run static validation with:

```sh
make validate
```

## Kind smoke flow (CI and local)

`.github/workflows/kind-smoke.yaml` is **invoked when needed** (`workflow_dispatch`, plus a weekly
schedule). It takes one input: `kubecrate_ref`, the full kubecrate commit SHA to smoke against,
defaulting to a pinned recent kubecrate `main` commit. Pull requests are covered by the static
manifest linting workflow only; the kind flow never runs implicitly.

The workflow is a thin wrapper around `scripts/kind-smoke-e2e.sh`, which:

1. creates a unique disposable kind cluster (`kubecrate-smoke-ci-<run_id>` in CI) and proves the
   active kubectl context matches it;
2. bootstraps Flux from the pinned `flux2` Helm chart (`2.18.4`, matching kubecrate);
3. applies a CI-rendered Flux root: `GitRepository/flux-system-sync` → the kubecrate repo at
   `KUBECRATE_REF`, and a root Kustomization reconciling `compositions/vanilla/entrypoint`;
4. waits for the kubecrate platform Kustomizations to become Ready;
5. creates `GitRepository/kind-smoke` → this repo at the checked-out commit (`github.sha` in CI)
   and applies `entrypoint/` from the checkout;
6. waits for every smoke Kustomization and the CrateCheck deployment to become ready;
7. polls CrateCheck `/status.json` through a port-forward until all checks are exactly green
   (the config uses `interval: 0`, so every request evaluates fresh state);
8. runs a controlled-red mutation — suspends the `external-secrets-operator-smoke` Kustomization
   and deletes the `eso-smoke-projection` ExternalSecret — and asserts exactly the
   `eso-externalsecret-ready` + `eso-projected-secret-exists` checks go red;
9. restores, asserts exact green again, deletes the cluster, and proves it is absent;
10. uploads the captured `status-green.json` / `status-red.json` / `status-restored.json`
    artifacts.

### Running locally

```sh
# against the default pinned kubecrate commit, smoke fixtures from HEAD (must be pushed)
make kind-smoke-ci

# against a different kubecrate substrate commit
KUBECRATE_REF=<full-40-char-sha> ./scripts/kind-smoke-e2e.sh

# smoke an unmerged smoke-suite branch after pushing it
SMOKE_REF=<full-40-char-sha-of-pushed-branch-tip> ./scripts/kind-smoke-e2e.sh
```

Prerequisites: `git kind kubectl kustomize helm flux curl python3 docker`. The script refuses to
touch any cluster it did not create, always deletes its own cluster, and verifies absence with
`kind get clusters`. Both Flux sources require their commits to be advertised by their remotes,
so push the smoke commit before running.

### Pinning the kubecrate revision

The kubecrate substrate is always pinned by full commit SHA — never a moving branch — so a smoke
result is attributable to an exact substrate state. To validate a kubecrate PR before merge,
dispatch the workflow (or run the script) with that PR's head SHA. Bump the default pin in
`workflow_dispatch.inputs.kubecrate_ref.default` and `KUBECRATE_REF` in
`scripts/kind-smoke-e2e.sh` when a new kubecrate `main` commit becomes the supported baseline.
