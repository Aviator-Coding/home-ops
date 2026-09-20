#!/usr/bin/env python3
"""Fleet gate: no container may declare limits.memory without requests.memory.

Intent (fm/homeops-fleet-memory-rightsize):

  Kubernetes defaults an absent `requests.memory` to the container's
  `limits.memory`. A block that reads as a ceiling therefore becomes a node
  RESERVATION of the same size, silently. AGENTS.md records 21 such sites in
  this repo's history and the 2026-09-14 cleanup that was believed to have
  closed them.

  It had not. Sixteen were live on 2026-09-20, and the fleet memory analysis
  that re-audited the class reported ZERO - because it audited the LIVE
  CLUSTER, where the defect is structurally invisible: the API server has
  already written requests == limits into every Pod by the time anything can
  read it, so a defaulted request and a deliberate Guaranteed-QoS request are
  byte-identical. Measured that day on monitoring/gatus:

      Deployment .spec.template ... resources:
          {"limits":{"memory":"256Mi"},"requests":{"cpu":"100m"}}
      Pod         .spec          ... resources:
          {"limits":{"memory":"256Mi"},"requests":{"cpu":"100m","memory":"256Mi"}}

  The defect is only visible in Git. Hence a repo-side gate rather than a
  cluster probe, and hence this file: the class cannot be shown closed by
  looking at the cluster, so it has to be held closed here.

What this asserts, and what it deliberately does not:

  - ASSERTS: every resources block declaring limits.memory also declares
    requests.memory. That is the relationship, not a literal. A site with no
    headroom left is expected to declare requests.memory EQUAL to its limit
    (network/echo and the gatus native sidecar both do) - the gate's objection
    is to the value being implicit, never to its size.
  - ASSERTS: requests.memory <= limits.memory, which the API server would
    reject anyway but which is cheaper to catch here.
  - DOES NOT assert any specific request value, or any request-to-peak ratio.
    Peaks move, and AGENTS.md is explicit that a gate freezing a resource
    literal goes red on the next legitimate change. Sizing rationale lives in
    a comment beside each value, where it can be revised without a CI edit.

Coverage, precisely (this gate does NOT "audit every possible way a resources
block can enter the cluster" - only these two shapes):

  - A full manifest's own `resources:` block, found by walking the parsed
    YAML tree of every kustomize.yaml under kubernetes/.
  - The embedded body of a Kustomize strategic-merge `patch: |` block scalar
    (a YAML dict), which yaml.safe_load otherwise leaves as an opaque string
    leaf - invisible to the tree walk above. Found live 2026-09-20:
    kubernetes/apps/base/flux-system/flux-instance/app/helmrelease.yaml
    patches `limits.memory: 2Gi` onto the Flux controllers with no
    requests.memory in the patch body - exactly this gate's target shape, and
    it was structurally unable to see it before this fix.

  A JSON6902 op-list patch (`- op: add/replace/remove, path: ..., value:
  ...`) is deliberately left opaque. Unlike a strategic-merge patch, judging
  one requires applying it against the target object the gate does not have -
  a `resources` value could arrive nested inside an op's `value:` at any
  path, or an op could remove the very requests key another op adds. Parsing
  it as a bare YAML tree without applying it would produce false positives
  and false negatives in about equal measure, so op-list patches are skipped
  rather than guessed at.

  A strategic-merge patch is also a genuinely different shape than a full
  manifest even once parsed: it MERGES onto an existing object, so a patch
  body declaring `limits.memory` alone is not necessarily reproducing the
  live defect - the target may already declare its own requests.memory, in
  which case the merge is complete and correct. The gate cannot resolve that
  ambiguity from the patch text alone (the target lives in an external chart
  in the flux-instance case above), so a patch-embedded site is handled the
  same way as cloudnative-pg's cluster-17 below: verified once by hand
  against the live object, then held open via a documented EXEMPT entry
  rather than silently passed or permanently blocked.

  Verified 2026-09-20: with the flux-instance EXEMPT entry removed, this
  gate fails on exactly that site
  (doc0/.../patches[1]/patch<embedded>/.../resources, limits.memory=2Gi) -
  proving the patch-block walk now sees it. Restoring the entry passes
  clean. test_patch_block_coverage() below pins the same detection
  synthetically so it does not depend on that live file's shape persisting.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1].parent
KUBE = REPO / "kubernetes"

# Documented exemptions. An entry here means "request == limit is correct and
# leaving it implicit is the lesser evil", never "not yet looked at".
EXEMPT: dict[str, str] = {
    # CNPG Cluster. 14d peak 3,610 Mi against this 4Gi limit leaves 12% of
    # headroom, so request == limit is the right answer and there is nothing to
    # reclaim. It is left IMPLICIT because writing it out changes the Cluster
    # spec and can trigger a CNPG rolling switchover of the cluster's shared
    # postgres for a change with zero effect on the rendered Pod. Revisit the
    # next time this object is being rolled for another reason.
    "kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml": (
        "CNPG Cluster; request==limit is correct (peak 3,610Mi vs 4Gi) and "
        "making it explicit risks a switchover for no effect"
    ),
    # Strategic-merge patch (values.instance.kustomize.patches[].patch) that
    # sets limits.memory: 2Gi on the flux-operator-managed manager container
    # of kustomize-controller/helm-controller/source-controller, with no
    # requests.memory in the patch body. Verified live 2026-09-20: all three
    # Deployments carry requests {cpu 100m|50m, memory 64Mi} alongside limits
    # {cpu 1, memory 2Gi} - the patch merges onto flux-operator's own base
    # manifest, which already declares requests.memory 64Mi for that
    # container, so this is NOT a Class-A site. The base manifest lives in an
    # external OCI chart this repo cannot read, so that fact cannot be
    # re-derived from git and has to be pinned here from the live check.
    "kubernetes/apps/base/flux-system/flux-instance/app/helmrelease.yaml": (
        "SMP patch onto flux-operator's own base Deployment, which already "
        "declares requests.memory 64Mi (verified live 2026-09-20); the "
        "patch's limit-only body does not reproduce the defect"
    ),
}

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "ok") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")


_QTY = re.compile(r"^(\d+(?:\.\d+)?)([EPTGMk]i?|m)?$")
_FACTOR = {
    None: 1.0, "": 1.0, "m": 1e-3,
    "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3,
    "Ti": 1024.0**4, "Pi": 1024.0**5, "Ei": 1024.0**6,
}


def to_bytes(q: Any) -> float | None:
    """Parse a Kubernetes quantity to bytes; None if unparseable (e.g. a
    ${VAR} Flux substitution token, which this gate must not choke on)."""
    m = _QTY.match(str(q).strip())
    if not m:
        return None
    return float(m.group(1)) * _FACTOR[m.group(2)]


def _is_json6902_ops(node: Any) -> bool:
    """True for a JSON6902 op-list (`- op: add, path: ..., value: ...`),
    which the walker must leave opaque rather than guess at (see module
    docstring "Coverage, precisely")."""
    return isinstance(node, list) and bool(node) and all(
        isinstance(item, dict) and "op" in item and "path" in item for item in node
    )


def _parse_embedded_patch(text: str) -> Any | None:
    """Parse a Kustomize `patch: |` block scalar's body as embedded YAML.

    Returns None (skip, do not fail the gate) for a JSON6902 op-list, for
    text that is not valid YAML, or for anything that does not parse to a
    dict/list - a strategic-merge patch body is always a YAML object."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if _is_json6902_ops(parsed):
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def walk(node: Any, path: str, out: list[tuple[str, dict]]) -> None:
    if isinstance(node, dict):
        res = node.get("resources")
        if isinstance(res, dict):
            out.append((path + "/resources", res))
        for k, v in node.items():
            if k == "patch" and isinstance(v, str):
                embedded = _parse_embedded_patch(v)
                if embedded is not None:
                    walk(embedded, f"{path}/patch<embedded>", out)
                continue
            walk(v, f"{path}/{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", out)


def collect() -> list[tuple[str, str, dict]]:
    found: list[tuple[str, str, dict]] = []
    for f in sorted(KUBE.rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(f.read_text()))
        except yaml.YAMLError:
            continue  # templated/non-YAML fragments are other gates' problem
        rel = str(f.relative_to(REPO))
        for n, d in enumerate(docs):
            blocks: list[tuple[str, dict]] = []
            walk(d, f"doc{n}", blocks)
            for path, res in blocks:
                found.append((rel, path, res))
    return found


def test_every_memory_limit_has_a_declared_request() -> None:
    blocks = collect()
    record(
        "scan_found_resource_blocks",
        len(blocks) >= 50,
        f"only {len(blocks)} resources blocks parsed - the scan is probably broken",
    )

    offenders: list[str] = []
    for rel, path, res in blocks:
        lim = res.get("limits") or {}
        req = res.get("requests") or {}
        if not isinstance(lim, dict) or not isinstance(req, dict):
            continue
        if "memory" in lim and "memory" not in req:
            if rel in EXEMPT:
                continue
            offenders.append(f"{rel} at {path} (limits.memory={lim['memory']})")

    record(
        "no_memory_limit_without_request",
        not offenders,
        "these reserve their whole limit because Kubernetes defaults an absent "
        "requests.memory to limits.memory - declare requests.memory explicitly, "
        "equal to the limit if there is no headroom to reclaim: "
        + "; ".join(offenders),
    )


def test_requests_never_exceed_limits() -> None:
    bad: list[str] = []
    for rel, path, res in collect():
        lim = (res.get("limits") or {})
        req = (res.get("requests") or {})
        if not isinstance(lim, dict) or not isinstance(req, dict):
            continue
        lb, rb = to_bytes(lim.get("memory")), to_bytes(req.get("memory"))
        if lb is not None and rb is not None and rb > lb:
            bad.append(f"{rel} at {path}: requests {req['memory']} > limits {lim['memory']}")
    record("memory_request_never_exceeds_limit", not bad, "; ".join(bad))


def test_exemptions_are_still_real() -> None:
    """An exemption that no longer matches anything is stale and must be
    deleted, or it silently widens the gate for whatever lands at that path."""
    live = {
        rel
        for rel, _path, res in collect()
        if isinstance(res.get("limits"), dict)
        and "memory" in res["limits"]
        and "memory" not in (res.get("requests") or {})
    }
    stale = sorted(set(EXEMPT) - live)
    record(
        "no_stale_exemptions",
        not stale,
        "exemption no longer matches a real site, delete it: " + ", ".join(stale),
    )


def _offenders_via_walk(doc: dict) -> list[str]:
    blocks: list[tuple[str, dict]] = []
    walk(doc, "synthetic", blocks)
    out = []
    for path, res in blocks:
        lim = res.get("limits") or {}
        req = res.get("requests") or {}
        if isinstance(lim, dict) and isinstance(req, dict) and "memory" in lim and "memory" not in req:
            out.append(path)
    return out


def test_patch_block_coverage() -> None:
    """Proves the `patch: |` embedding fix (walk()'s "k == 'patch'" branch)
    actually detects the shape it exists for, rather than merely not
    crashing on it. A limits-only SMP patch body must be flagged (mutation
    goes red); a fully-declared one must not (control passes); a JSON6902
    op-list and invalid YAML must both be skipped without error."""
    control_patch = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: manager\n"
        "          resources:\n"
        "            limits:\n"
        "              memory: 2Gi\n"
        "            requests:\n"
        "              memory: 64Mi\n"
    )
    mutated_patch = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: manager\n"
        "          resources:\n"
        "            limits:\n"
        "              memory: 2Gi\n"
    )
    json6902_patch = (
        "- op: add\n"
        "  path: /spec/template/spec/containers/0/args/-\n"
        "  value: --concurrent=10\n"
    )
    invalid_patch = "not: [valid, yaml"

    record(
        "patch_block_control_fully_declared_not_flagged",
        _offenders_via_walk({"patch": control_patch}) == [],
        "control patch (limits+requests both declared) was unexpectedly "
        f"flagged: {_offenders_via_walk({'patch': control_patch})}",
    )
    mutated_offenders = _offenders_via_walk({"patch": mutated_patch})
    record(
        "patch_block_mutation_limits_only_is_flagged",
        len(mutated_offenders) == 1,
        "a limits-only SMP patch body (no requests.memory) was NOT detected "
        "- the patch-block walker regressed to invisible again",
    )
    record(
        "patch_block_json6902_skipped_without_crash",
        _offenders_via_walk({"patch": json6902_patch}) == [],
        "a JSON6902 op-list must be skipped, not walked as a strategic-merge "
        "object (it has no target to evaluate the ops against)",
    )
    record(
        "patch_block_invalid_yaml_skipped_without_crash",
        _offenders_via_walk({"patch": invalid_patch}) == [],
        "invalid YAML inside a patch: block must be skipped, not raise",
    )


def main() -> int:
    print("==> every limits.memory has an explicit requests.memory")
    test_every_memory_limit_has_a_declared_request()
    print("==> requests.memory never exceeds limits.memory")
    test_requests_never_exceed_limits()
    print("==> documented exemptions still describe real sites")
    test_exemptions_are_still_real()
    print("==> patch: block parsing detects a limits-only SMP body and skips ops/invalid YAML")
    test_patch_block_coverage()

    failed = [r for r in RESULTS if not r[1]]
    print()
    print(f"summary: {len(RESULTS) - len(failed)} passed, {len(failed)} failed, {len(RESULTS)} total")
    for name, _ok, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
