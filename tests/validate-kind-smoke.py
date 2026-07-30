#!/usr/bin/env python3
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "entrypoint"
ROOTS = [
    ENTRYPOINT,
    ROOT / "platform-services/external-secrets-operator",
    ROOT / "platform-services/envoy-gateway",
    ROOT / "platform-services/kyverno/policy",
    ROOT / "platform-services/kyverno/consumer",
]
EXPECTED = {
    "external-secrets-operator-smoke": "./platform-services/external-secrets-operator",
    "envoy-gateway-smoke": "./platform-services/envoy-gateway",
    "kyverno-smoke-policy": "./platform-services/kyverno/policy",
    "kyverno-smoke": "./platform-services/kyverno/consumer",
}
GATEWAY = ROOT / "platform-services/envoy-gateway/smoke-gateway.yaml"
ROUTE = ROOT / "platform-services/envoy-gateway/smoke-httproute.yaml"
REFERENCE_GRANT = ROOT / "platform-services/envoy-gateway/smoke-referencegrant.yaml"

def load(path: Path):
    with path.open(encoding="utf-8") as fh:
        value = yaml.safe_load(fh)
    assert isinstance(value, dict), path
    return value

def main() -> int:
    wrapper = load(ENTRYPOINT / "kustomization.yaml")
    resources = set(wrapper.get("resources", []))
    for name, path in EXPECTED.items():
        filename = f"./{name}-kustomization.yaml"
        assert filename in resources
        doc = load(ENTRYPOINT / f"{name}-kustomization.yaml")
        assert doc["kind"] == "Kustomization"
        assert doc["metadata"]["name"] == name
        assert doc["spec"]["path"] == path
        assert doc["spec"]["sourceRef"] == {"kind": "GitRepository", "name": "kind-smoke"}
    assert load(ENTRYPOINT / "kyverno-smoke-policy-kustomization.yaml")["metadata"]["labels"]["kubecrate.io/workload-category"] == "platform-services"
    assert load(ENTRYPOINT / "kyverno-smoke-kustomization.yaml")["metadata"]["labels"]["kubecrate.io/workload-category"] == "application-services"
    gateway = load(GATEWAY)
    https = next(listener for listener in gateway["spec"]["listeners"] if listener["name"] == "https")
    assert https["protocol"] == "HTTPS" and https["port"] == 443
    assert https["tls"]["certificateRefs"] == [{
        "group": "", "kind": "Secret", "name": "cratecheck-tls", "namespace": "cratecheck"
    }]
    route = load(ROUTE)
    assert {parent["sectionName"] for parent in route["spec"]["parentRefs"]} == {"http", "https"}
    grant = load(REFERENCE_GRANT)
    assert grant["metadata"]["namespace"] == "cratecheck"
    assert grant["spec"]["to"] == [{"group": "", "kind": "Secret", "name": "cratecheck-tls"}]
    for root in ROOTS:
        result = subprocess.run(["kustomize", "build", str(root)], cwd=ROOT, text=True, capture_output=True)
        assert result.returncode == 0, f"kustomize build {root.relative_to(ROOT)} failed: {result.stderr}"
    print("kind smoke validation passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
