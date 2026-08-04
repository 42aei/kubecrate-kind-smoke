#!/usr/bin/env python3
"""Validate the kubecrate-kind-smoke fixture and entrypoint contract.

Static, red-capable checks for the consumer-side smoke suite:
- entrypoint Flux Kustomizations match the expected fixture set, paths,
  sourceRef, dependency order, and workload-category labels
- cert-manager local issuer chain and CrateCheck fixtures keep the exact
  contract the CrateCheck check config evaluates
- CrateCheck check IDs stay in lockstep with scripts/validate-cratecheck-status.py
- every fixture root renders with kustomize
- every namespace referenced by rendered fixture objects is either created by a
  smoke fixture Namespace object or provided by the kubecrate substrate
"""

from pathlib import Path
import runpy
import subprocess
import re

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "entrypoint"
ROOTS = [
    ENTRYPOINT,
    ROOT / "platform-services/external-secrets-operator",
    ROOT / "platform-services/envoy-gateway",
    ROOT / "platform-services/cert-manager/local-issuer",
    ROOT / "platform-services/kyverno/policy",
    ROOT / "platform-services/kyverno/consumer",
    ROOT / "application-services/cratecheck",
]
EXPECTED = {
    "external-secrets-operator-smoke": "./platform-services/external-secrets-operator",
    "cratecheck": "./application-services/cratecheck",
    "envoy-gateway-smoke": "./platform-services/envoy-gateway",
    "cert-manager-local-issuer-smoke": "./platform-services/cert-manager/local-issuer",
    "kyverno-smoke-policy": "./platform-services/kyverno/policy",
    "kyverno-smoke": "./platform-services/kyverno/consumer",
}
DEPENDS_ON = {
    "external-secrets-operator-smoke": ["external-secrets-operator"],
    "cratecheck": ["external-secrets-operator-smoke"],
    "envoy-gateway-smoke": ["envoy-gateway", "cratecheck", "cert-manager-local-issuer-smoke"],
    "cert-manager-local-issuer-smoke": ["cert-manager", "cratecheck"],
    "kyverno-smoke-policy": ["kyverno"],
    "kyverno-smoke": ["kyverno-smoke-policy"],
}
APPLICATION_SERVICES = {"cratecheck", "kyverno-smoke"}
GATEWAY = ROOT / "platform-services/envoy-gateway/smoke-gateway.yaml"
ROUTE = ROOT / "platform-services/envoy-gateway/smoke-httproute.yaml"
REFERENCE_GRANT = ROOT / "platform-services/envoy-gateway/smoke-referencegrant.yaml"
ENVOYPROXY = ROOT / "platform-services/envoy-gateway/smoke-envoyproxy.yaml"
ISSUERS = ROOT / "platform-services/cert-manager/local-issuer/local-ca-issuer.yaml"
POLICY = ROOT / "platform-services/kyverno/policy/require-ns-label-policy.yaml"
KYVERNO_FIXTURE = ROOT / "platform-services/kyverno/consumer/smoke-allowed-namespace.yaml"
CRATECHECK_CONFIGMAP = ROOT / "application-services/cratecheck/configmap.yaml"
CRATECHECK_RBAC = ROOT / "application-services/cratecheck/clusterrole.yaml"
STATUS_VALIDATOR = ROOT / "scripts/validate-cratecheck-status.py"
DENIAL_MESSAGE = "Namespace requires kubecrate.io/validated=true"
# Namespaces the pinned kubecrate substrate (or Kubernetes itself) provides;
# every other referenced namespace must be created by a smoke fixture.
KUBECRATE_NAMESPACES = {
    "core-external-secrets-operator",
    "core-envoy-gateway",
    "core-cert-manager",
    "core-kyverno",
    "flux-system",
    "default",
    "kube-system",
}
KUBECRATE_SOURCE_NAME = "flux-system-sync"
KUBECRATE_TAG = "v0.4.0"
TAG = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def load(path: Path):
    with path.open(encoding="utf-8") as fh:
        value = yaml.safe_load(fh)
    assert isinstance(value, dict), path
    return value


def load_all(path: Path):
    with path.open(encoding="utf-8") as fh:
        return [doc for doc in yaml.safe_load_all(fh) if isinstance(doc, dict)]


def assert_entrypoint_contract() -> None:
    wrapper = load(ENTRYPOINT / "kustomization.yaml")
    resources = set(wrapper.get("resources", []))
    assert resources == {f"./{name}-kustomization.yaml" for name in EXPECTED}, resources
    for name, path in EXPECTED.items():
        doc = load(ENTRYPOINT / f"{name}-kustomization.yaml")
        assert doc["apiVersion"] == "kustomize.toolkit.fluxcd.io/v1", name
        assert doc["kind"] == "Kustomization", name
        assert doc["metadata"]["name"] == name, name
        assert doc["metadata"]["namespace"] == "flux-system", name
        labels = doc["metadata"]["labels"]
        assert labels["app.kubernetes.io/part-of"] == "kubecrate-kind-smoke", name
        expected_category = (
            "application-services" if name in APPLICATION_SERVICES else "platform-services"
        )
        assert labels["kubecrate.io/workload-category"] == expected_category, name
        assert doc["spec"]["path"] == path, name
        assert doc["spec"]["sourceRef"] == {"kind": "GitRepository", "name": "kind-smoke"}, name
        depends_on = [item["name"] for item in doc["spec"].get("dependsOn", [])]
        assert depends_on == DEPENDS_ON[name], (name, depends_on)


def assert_kubecrate_source_contract() -> None:
    script = (ROOT / "scripts/kind-smoke-e2e.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/kind-smoke.yaml").read_text(encoding="utf-8")
    assert f'KUBECRATE_REF="${{KUBECRATE_REF:-{KUBECRATE_TAG}}}"' in script
    assert f"default: {KUBECRATE_TAG}" in workflow
    assert TAG.fullmatch(KUBECRATE_TAG)
    assert f"name: {KUBECRATE_SOURCE_NAME}" in script
    assert "tag: $KUBECRATE_REF" in script
    assert "commit: $KUBECRATE_REF" not in script
    obsolete_source = "kubecrate" + "-upstream"
    assert obsolete_source not in script


def assert_envoy_tls_contract() -> None:
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
    provider = load(ENVOYPROXY)["spec"]["provider"]
    assert provider["type"] == "Kubernetes"
    service = provider["kubernetes"]["envoyService"]
    assert service["type"] == "NodePort"
    assert service["patch"]["value"]["spec"]["ports"] == [
        {"name": "http", "port": 80, "nodePort": 30080},
        {"name": "https", "port": 443, "nodePort": 30443},
    ]


def assert_local_issuer_chain() -> None:
    docs = load_all(ISSUERS)
    by_name = {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs}
    selfsigned = by_name[("ClusterIssuer", "kubecrate-local-selfsigned")]
    assert selfsigned["spec"] == {"selfSigned": {}}
    ca_certificate = by_name[("Certificate", "cratecheck-local-ca")]
    assert ca_certificate["metadata"]["namespace"] == "core-cert-manager"
    assert ca_certificate["spec"]["isCA"] is True
    assert ca_certificate["spec"]["secretName"] == "cratecheck-local-ca"
    assert ca_certificate["spec"]["issuerRef"]["name"] == "kubecrate-local-selfsigned"
    ca_issuer = by_name[("ClusterIssuer", "kubecrate-local-ca")]
    assert ca_issuer["spec"]["ca"]["secretName"] == "cratecheck-local-ca"
    tls_certificate = by_name[("Certificate", "cratecheck-tls")]
    assert tls_certificate["metadata"]["namespace"] == "cratecheck"
    assert tls_certificate["spec"]["dnsNames"] == ["cratecheck.local"]
    assert tls_certificate["spec"]["secretName"] == "cratecheck-tls"
    assert tls_certificate["spec"]["issuerRef"]["name"] == "kubecrate-local-ca"


def assert_kyverno_policy_contract() -> None:
    policy = load(POLICY)
    assert policy["spec"]["validationFailureAction"] == "Enforce"
    assert len(policy["spec"]["rules"]) == 1
    rule = policy["spec"]["rules"][0]
    assert rule["match"]["any"] == [{"resources": {"kinds": ["Namespace"], "names": ["kyverno-smoke-*"]}}]
    assert rule["validate"]["message"] == DENIAL_MESSAGE
    assert rule["validate"]["pattern"]["metadata"]["labels"] == {"kubecrate.io/validated": "true"}
    fixture = load(KYVERNO_FIXTURE)
    assert fixture["kind"] == "Namespace"
    assert fixture["metadata"]["name"] == "kyverno-smoke-allowed"
    assert fixture["metadata"]["labels"]["kubecrate.io/validated"] == "true"
    assert fixture["metadata"]["labels"]["kubecrate.io/workload-category"] == "application-services"


def assert_cratecheck_contract() -> None:
    configmap = load(CRATECHECK_CONFIGMAP)
    status = yaml.safe_load(configmap["data"]["status.yaml"])
    assert status["interval"] == 0, "checks must evaluate on every request"
    configured_ids = {check["id"] for check in status["checks"]}
    validator = runpy.run_path(str(STATUS_VALIDATOR))
    assert configured_ids == validator["EXPECTED_IDS"], "check IDs and status validator drifted"
    assert validator["RED_IDS"]["eso-red"] == {"eso-externalsecret-ready", "eso-projected-secret-exists"}
    role = load(CRATECHECK_RBAC)
    for group, resources in (
        ("cert-manager.io", ["clusterissuers", "certificates"]),
        ("kyverno.io", ["clusterpolicies"]),
        ("external-secrets.io", ["secretstores", "externalsecrets"]),
        ("gateway.networking.k8s.io", ["gatewayclasses", "gateways", "httproutes"]),
    ):
        rules = [rule for rule in role["rules"] if group in rule.get("apiGroups", [])]
        assert rules == [{"apiGroups": [group], "resources": resources, "verbs": ["get"]}], group


def assert_roots_render() -> None:
    for root in ROOTS:
        result = subprocess.run(
            ["kustomize", "build", str(root)], cwd=ROOT, text=True, capture_output=True
        )
        assert result.returncode == 0, f"kustomize build {root.relative_to(ROOT)} failed: {result.stderr}"


def render(root: Path) -> list:
    result = subprocess.run(
        ["kustomize", "build", str(root)], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, f"kustomize build {root.relative_to(ROOT)} failed: {result.stderr}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def assert_namespace_coherence() -> None:
    fixture_roots = [root for root in ROOTS if root != ENTRYPOINT]
    created = set()
    referenced = set()
    for root in fixture_roots:
        for doc in render(root):
            metadata = doc.get("metadata") or {}
            if doc.get("kind") == "Namespace":
                created.add(metadata.get("name"))
            namespace = metadata.get("namespace")
            if namespace:
                referenced.add(namespace)
    missing = referenced - created - KUBECRATE_NAMESPACES
    assert not missing, (
        f"fixture objects reference namespaces that no smoke fixture creates and the "
        f"kubecrate substrate does not provide: {sorted(missing)}"
    )


def main() -> int:
    assert_entrypoint_contract()
    assert_kubecrate_source_contract()
    assert_envoy_tls_contract()
    assert_local_issuer_chain()
    assert_kyverno_policy_contract()
    assert_cratecheck_contract()
    assert_roots_render()
    assert_namespace_coherence()
    print("kind smoke validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
