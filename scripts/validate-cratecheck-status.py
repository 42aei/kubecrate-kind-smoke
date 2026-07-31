#!/usr/bin/env python3
"""Validate the exact JSON-only CrateCheck status contract used by direct E2E."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_IDS = {
    "cratecheck-deployment-ready",
    "cratecheck-namespace-exists",
    "cratecheck-configmap-present",
    "eso-helmrelease-ready",
    "eso-secretstore-ready",
    "eso-externalsecret-ready",
    "eso-projected-secret-exists",
    "envoy-helmrelease-ready",
    "envoy-gatewayclass-accepted",
    "envoy-gateway-ready",
    "envoy-httproute-ready",
    "cert-manager-helmrelease-ready",
    "cert-manager-selfsigned-issuer-ready",
    "cert-manager-ca-certificate-ready",
    "cert-manager-ca-issuer-ready",
    "cert-manager-tls-certificate-ready",
    "cert-manager-tls-secret-exists",
    "kyverno-helmrelease-ready",
    "kyverno-clusterpolicy-ready",
    "kyverno-smoke-namespace-exists",
}
RED_IDS = {
    "eso-red": {"eso-externalsecret-ready", "eso-projected-secret-exists"},
    "envoy-red": {"envoy-httproute-ready"},
    "cert-manager-red": {"cert-manager-tls-certificate-ready"},
    "kyverno-red": {"kyverno-clusterpolicy-ready"},
}
STATUSES = {"green", "red", "yellow", "unknown"}


def validate_status(data: Any, phase: str) -> None:
    assert isinstance(data, dict), "status response must be an object"
    checks = data.get("checks")
    summary = data.get("summary")
    assert isinstance(checks, list) and len(checks) == len(EXPECTED_IDS), (
        f"exactly {len(EXPECTED_IDS)} checks required"
    )
    assert all(isinstance(item, dict) for item in checks), "checks must be objects"
    ids = [item.get("id") for item in checks]
    assert all(isinstance(check_id, str) for check_id in ids), "check IDs must be strings"
    assert len(ids) == len(set(ids)), "duplicate check IDs"
    assert set(ids) == EXPECTED_IDS, "check ID set mismatch"

    statuses = {item["id"]: item.get("status") for item in checks}
    assert all(status in STATUSES for status in statuses.values()), "invalid check status"
    assert isinstance(summary, dict), "summary missing"
    counts = Counter(statuses.values())
    for status in STATUSES:
        assert type(summary.get(status)) is int and summary[status] == counts[status], (
            f"summary {status} mismatch"
        )
    assert type(summary.get("total")) is int and summary["total"] == len(EXPECTED_IDS), (
        "summary total mismatch"
    )

    if phase == "green":
        assert data.get("status") == "green", "overall status is not green"
        assert counts["green"] == len(EXPECTED_IDS), "not all checks are green"
        return

    assert data.get("status") == "red", "controlled-red overall status is not red"
    expected_red = RED_IDS[phase]
    changed = {check_id for check_id, status in statuses.items() if status != "green"}
    assert changed == expected_red, "controlled-red check set mismatch"
    assert all(statuses[check_id] == "red" for check_id in expected_red), (
        "controlled-red checks must be red"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("green", "eso-red", "envoy-red", "cert-manager-red", "kyverno-red"),
        required=True,
    )
    parser.add_argument("status_file", type=Path)
    args = parser.parse_args()
    try:
        validate_status(json.loads(args.status_file.read_text()), args.phase)
    except (AssertionError, OSError, json.JSONDecodeError) as error:
        print(f"validate-cratecheck-status: ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
