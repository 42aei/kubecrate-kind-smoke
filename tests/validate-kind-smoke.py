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
    for root in ROOTS:
        result = subprocess.run(["kustomize", "build", str(root)], cwd=ROOT, text=True, capture_output=True)
        assert result.returncode == 0, f"kustomize build {root.relative_to(ROOT)} failed: {result.stderr}"
    print("kind smoke validation passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
