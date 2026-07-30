.RECIPEPREFIX := >
SHELL := /bin/sh

.PHONY: validate

validate:
> python3 tests/validate-kind-smoke.py
