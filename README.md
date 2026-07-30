# kubecrate-kind-smoke

Kind smoke fixtures for Kubecrate reference consumers.

This repository owns kind-local smoke resources that exercise reusable Kubecrate platform services. A consumer Flux root imports Kubecrate for the platform/application service distribution and imports this repository for smoke fixtures.

## Paths

- `entrypoint/` contains Flux `Kustomization` objects for all smoke fixtures.
- `platform-services/external-secrets-operator/` contains the External Secrets smoke resources.
- `platform-services/envoy-gateway/` contains the Envoy Gateway smoke resources.
- `platform-services/kyverno/policy/` contains the Kyverno policy fixture.
- `platform-services/kyverno/consumer/` contains the Kyverno consumer fixture.

Run static validation with:

```sh
make validate
```
