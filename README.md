# kubecrate-kind-smoke

Disposable [kind](https://kind.sigs.k8s.io/) smoke tests for consuming a pinned
[Kubecrate](https://github.com/42aei/kubecrate) revision.

This repository contains consumer-side fixtures and a CrateCheck status service for verifying
Kubecrate's platform services with representative consumers:

- External Secrets Operator
- Envoy Gateway
- cert-manager
- Kyverno

## How it works

The smoke environment uses two Flux `GitRepository` sources:

1. Kubecrate, pinned to the full commit SHA under test and reconciling
   `compositions/vanilla/entrypoint`.
2. This repository, pinned to the smoke-suite commit and reconciling `entrypoint/`.

The fixtures depend on Kubecrate's platform-service Kustomizations; they do not own or redefine
those services. The suite creates its own `kubecrate-system` namespace where needed.

The smoke flow creates a disposable kind cluster, installs Flux, reconciles Kubecrate and the
fixtures, then verifies the following sequence through CrateCheck `/status.json`:

```text
green → controlled red → restored green
```

It deletes the cluster afterward and uploads the three status responses as workflow artifacts.

## Repository layout

- `entrypoint/` — Flux Kustomizations for the smoke fixtures.
- `platform-services/` — consumer fixtures for the platform services.
- `application-services/cratecheck/` — CrateCheck deployment and configuration.
- `scripts/kind-smoke-e2e.sh` — disposable kind smoke runner.
- `scripts/validate-cratecheck-status.py` — `/status.json` contract validator.
- `tests/validate-kind-smoke.py` — static fixture validation.

Run static validation with:

```sh
make validate
```

## Run locally

The GitHub Actions workflow is manual-only. It accepts `kubecrate_ref`, a full 40-character
Kubecrate commit SHA. Pull requests run static manifest validation; the kind smoke test never runs
implicitly.

Run against the default pinned Kubecrate revision:

```sh
make kind-smoke-ci
```

Override either revision when the commit is available remotely:

```sh
KUBECRATE_REF=<full-40-character-sha> ./scripts/kind-smoke-e2e.sh
SMOKE_REF=<full-40-character-sha> ./scripts/kind-smoke-e2e.sh
```

Prerequisites: `git`, `kind`, `kubectl`, `kustomize`, `helm`, `flux`, `curl`, `python3`, and
`docker`. Both Flux sources must be able to fetch the requested commits, so push the smoke-suite
commit before running it.

Kubecrate revisions are always pinned by full commit SHA, never by a moving branch. The default
Kubecrate SHA is defined in the workflow and smoke script; update both when changing the supported
baseline.

Both repositories are public, so Flux reads them anonymously over HTTPS. No deploy key or
Kubernetes Git credential is required.
