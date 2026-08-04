.RECIPEPREFIX := >
SHELL := /bin/sh

.PHONY: validate kind-smoke-ci

validate:
> python3 tests/validate-kind-smoke.py

# Run the full disposable kind smoke flow locally. Override the pinned
# kubecrate substrate with KUBECRATE_REF=<exact-release-tag>; the smoke fixtures are
# reconciled from SMOKE_REF (default: HEAD, which must be pushed).
kind-smoke-ci:
> ./scripts/kind-smoke-e2e.sh
