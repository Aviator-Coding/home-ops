#!/usr/bin/env python3
"""Behavioral regression test for the Kubernetes Renovate group + auto-merge guard.

Kubernetes v1.36.5 arrived as five single-component Renovate PRs (1760-1763
kube-apiserver/kube-controller-manager/kube-proxy/kube-scheduler; 1769
kubelet), and each failed scripts/ci/version-consistency.sh (the required
`versions` check), which needs all five images in talos/machineconfig.yaml.j2
to match the same KubernetesUpgrade CR version. They had to be consolidated by
hand into PR 1780.

The fix has two parts, and either one missing reopens a real risk:

  1. .renovate/groups.json5 groups all five packages into one "Kubernetes" PR
     so version-consistency.sh sees a single, self-consistent change.
  2. .renovate/autoMerge.json5 adds an explicit automerge:false for the same
     five package names, because grouping them does NOT make an unattended
     merge safe - kubelet's version pin feeds tuppr's live KubernetesUpgrade
     CR directly (kubernetes/apps/base/system-upgrade/tuppr/upgrades/
     kubernetesupgrade.yaml), which tuppr applies across all 3 Talos nodes
     with no human apply-node step, gated only by two healthChecks. Before
     this fix, the five packages individually failing version-consistency.sh
     was the only thing standing between them and an unattended merge.

Evidence is Renovate's own compiled logic (applyPackageRules), not a source
grep - same approach as talos-renovate-pin-test.py for the sibling Talos/tuppr
guard. Requires a local `renovate` install (RENOVATE_NODE_PATH) or network
access to `npm install renovate@44.52.1`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ROOT / ".renovate" / "groups.json5"
AUTOMERGE = ROOT / ".renovate" / "autoMerge.json5"
MACHINECONFIG = ROOT / "talos" / "machineconfig.yaml.j2"
KUBERNETESUPGRADE = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "system-upgrade"
    / "tuppr"
    / "upgrades"
    / "kubernetesupgrade.yaml"
)

K8S_PACKAGES = [
    "ghcr.io/siderolabs/kubelet",
    "registry.k8s.io/kube-apiserver",
    "registry.k8s.io/kube-controller-manager",
    "registry.k8s.io/kube-proxy",
    "registry.k8s.io/kube-scheduler",
]

# Adversarial control: a real, differently-versioned registry.k8s.io image
# that shares the "kube-" prefix but must NOT be swept into the group or the
# automerge exclusion by an over-broad match.
UNRELATED_PACKAGE = "registry.k8s.io/kube-state-metrics"

RENOVATE_PROBE = r"""
import { createRequire } from 'module';
import { pathToFileURL } from 'url';
import fs from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

function resolveRenovateRoot() {
  if (process.env.RENOVATE_NODE_PATH) {
    return process.env.RENOVATE_NODE_PATH;
  }
  try {
    return path.dirname(require.resolve('renovate/package.json'));
  } catch {
    throw new Error(
      'renovate package not resolvable; set RENOVATE_NODE_PATH to node_modules/renovate'
    );
  }
}

const root = resolveRenovateRoot();
const load = async (rel) => import(pathToFileURL(path.join(root, rel)).href);
const { applyPackageRules } = await load('dist/util/package-rules/index.js');

const groupRules = input.groupRules;
const automergeRules = input.automergeRules;
const combinedRules = [...automergeRules, ...groupRules]; // extends order: autoMerge.json5 then groups.json5
const packages = input.packages;
const unrelated = input.unrelated;

const results = {
  renovateVersion: require(path.join(root, 'package.json')).version,
  checks: {},
};

// 1. groups.json5 alone: each Kubernetes package resolves groupName "Kubernetes".
const groupNameResults = {};
for (const pkg of packages) {
  const cfg = await applyPackageRules({
    depName: pkg,
    packageName: pkg,
    datasource: 'docker',
    updateType: 'minor',
    newVersion: 'v1.36.6',
    currentVersion: 'v1.36.5',
    packageRules: groupRules,
  });
  groupNameResults[pkg] = cfg.groupName ?? null;
}
const unrelatedGroup = await applyPackageRules({
  depName: unrelated,
  packageName: unrelated,
  datasource: 'docker',
  updateType: 'minor',
  newVersion: '2.0.0',
  currentVersion: '1.0.0',
  packageRules: groupRules,
});
groupNameResults[unrelated] = unrelatedGroup.groupName ?? null;
results.checks.groupName = groupNameResults;

// 2. Combined (autoMerge + groups, real extends order) for each update type,
// seeding automerge:true the way the blanket digest/patch/minor rules would.
const automergeResults = {};
for (const pkg of packages) {
  for (const [label, updateType, newVersion] of [
    ['digest', 'digest', 'v1.36.5'],
    ['patch', 'patch', 'v1.36.6'],
    ['minor', 'minor', 'v1.37.0'],
  ]) {
    const cfg = await applyPackageRules({
      depName: pkg,
      packageName: pkg,
      datasource: 'docker',
      updateType,
      newVersion,
      currentVersion: 'v1.36.5',
      packageRules: combinedRules,
      automerge: true,
    });
    automergeResults[`${pkg}::${label}`] = {
      automerge: cfg.automerge,
      groupName: cfg.groupName ?? null,
    };
  }
}
const unrelatedCombined = await applyPackageRules({
  depName: unrelated,
  packageName: unrelated,
  datasource: 'docker',
  updateType: 'minor',
  newVersion: '2.0.0',
  currentVersion: '1.0.0',
  packageRules: combinedRules,
  automerge: true,
});
automergeResults[`${unrelated}::minor`] = {
  automerge: unrelatedCombined.automerge,
  groupName: unrelatedCombined.groupName ?? null,
};
results.checks.automerge = automergeResults;

// 3. Negative control: drop the K8s automerge:false rule and prove the
// grouped minor bump would otherwise have automerged - the guard is
// load-bearing, grouping alone does not make it safe.
const rulesWithoutGuard = automergeRules.filter(
  (r) => !(
    (r.matchPackageNames || []).includes('ghcr.io/siderolabs/kubelet') &&
    r.automerge === false
  ),
);
const combinedWithoutGuard = [...rulesWithoutGuard, ...groupRules];
const unguarded = await applyPackageRules({
  depName: 'ghcr.io/siderolabs/kubelet',
  packageName: 'ghcr.io/siderolabs/kubelet',
  datasource: 'docker',
  updateType: 'minor',
  newVersion: 'v1.37.0',
  currentVersion: 'v1.36.5',
  packageRules: combinedWithoutGuard,
  automerge: true,
});
results.checks.unguarded = { automerge: unguarded.automerge, groupName: unguarded.groupName ?? null };

process.stdout.write(JSON.stringify(results, null, 2));
"""


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_subset(text: str) -> Any:
    """Minimal JSON5 subset parser matching kopiur-stage0-test.py / talos-renovate-pin-test.py."""

    def strip_line_comments_outside_strings(src: str) -> str:
        out: list[str] = []
        i = 0
        n = len(src)
        in_str = False
        while i < n:
            ch = src[i]
            if in_str:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
                continue
            if ch == "/" and i + 1 < n and src[i + 1] == "/":
                while i < n and src[i] not in "\n\r":
                    i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    no_line = strip_line_comments_outside_strings(text)
    quoted = re.sub(
        r"([{\[,]\s*)([A-Za-z_$][\w$]*)\s*:",
        r'\1"\2":',
        no_line,
    )
    no_trail = re.sub(r",(\s*[}\]])", r"\1", quoted)
    try:
        return json.loads(no_trail)
    except json.JSONDecodeError as e:
        raise Failure(f"failed to parse JSON5 subset as JSON: {e}") from e


def load_group_rules() -> list[dict[str, Any]]:
    data = parse_json5_subset(GROUPS.read_text())
    rules = data.get("packageRules") or []
    require(isinstance(rules, list) and rules, "groups.json5 has no packageRules")
    return rules


def load_automerge_rules() -> list[dict[str, Any]]:
    data = parse_json5_subset(AUTOMERGE.read_text())
    rules = data.get("packageRules") or []
    require(isinstance(rules, list) and rules, "autoMerge.json5 has no packageRules")
    return rules


def test_group_rule_shape(group_rules: list[dict[str, Any]]) -> None:
    matches = [
        r
        for r in group_rules
        if isinstance(r, dict) and r.get("groupName") == "Kubernetes"
    ]
    require(len(matches) == 1, f"expected exactly one 'Kubernetes' group rule, got {len(matches)}")
    rule = matches[0]
    require(
        set(rule.get("matchPackageNames") or []) == set(K8S_PACKAGES),
        f"Kubernetes group matchPackageNames must be exactly {K8S_PACKAGES}, "
        f"got {rule.get('matchPackageNames')!r}",
    )
    require(
        rule.get("matchDatasources") == ["docker"],
        f"Kubernetes group must scope matchDatasources to docker, got {rule.get('matchDatasources')!r}",
    )


def test_automerge_rule_shape_and_order(automerge_rules: list[dict[str, Any]]) -> None:
    """The K8s automerge:false rule must beat the earlier blanket true rules
    (last-match-wins) for every one of the 5 package names, same reasoning as
    the sibling Talos/kopiur exclusions."""
    matches = [
        r
        for r in automerge_rules
        if isinstance(r, dict)
        and r.get("automerge") is False
        and set(r.get("matchPackageNames") or []) == set(K8S_PACKAGES)
    ]
    require(matches, "autoMerge.json5 missing the Kubernetes never-auto-merge rule")
    require(
        len(matches) == 1,
        f"expected exactly one Kubernetes never-auto-merge rule, got {len(matches)}",
    )
    guard_idx = automerge_rules.index(matches[0])

    earlier_minor_true = any(
        isinstance(r, dict)
        and r.get("automerge") is True
        and "minor" in (r.get("matchUpdateTypes") or [])
        for r in automerge_rules[:guard_idx]
    )
    require(
        earlier_minor_true,
        "expected an earlier blanket minor automerge:true rule for the K8s guard to override",
    )

    for pkg in K8S_PACKAGES:
        winning_automerge: bool | None = None
        winning_index = -1
        for idx, rule in enumerate(automerge_rules):
            names = rule.get("matchPackageNames")
            matched = names is not None and pkg in names
            if matched and "automerge" in rule:
                winning_automerge = bool(rule["automerge"])
                winning_index = idx
        require(
            winning_automerge is False,
            f"{pkg}: final automerge winner in autoMerge.json5 must be false "
            f"(got {winning_automerge} from rule {winning_index})",
        )
        require(
            winning_index == guard_idx,
            f"{pkg}: last automerge-setting match must be the Kubernetes guard "
            f"(won at index {winning_index}, guard at {guard_idx})",
        )


def _node_on_path() -> bool:
    try:
        proc = subprocess.run(
            ["node", "-e", "process.exit(0)"],
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def find_renovate_node_path() -> str | None:
    env = os.environ.get("RENOVATE_NODE_PATH")
    if env and (Path(env) / "package.json").is_file():
        return env
    for pattern in ("renovate-ci/node_modules/renovate", "renovate-test-*/node_modules/renovate"):
        for candidate in Path("/tmp").glob(pattern):
            if (candidate / "package.json").is_file():
                return str(candidate)
    if not _node_on_path():
        return None
    proc = subprocess.run(
        [
            "node",
            "-e",
            "console.log(require('path').dirname(require.resolve('renovate/package.json')))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def ensure_renovate() -> str:
    existing = find_renovate_node_path()
    if existing:
        return existing
    if not _node_on_path():
        raise Failure(
            "node is required on PATH (python-tests installs it via actions/setup-node; "
            "locally: provide node + RENOVATE_NODE_PATH or npm install renovate@44.52.1)"
        )
    tmp = Path(tempfile.mkdtemp(prefix="renovate-k8s-group-test-"))
    try:
        proc = subprocess.run(
            ["npm", "install", "--no-save", "--no-package-lock", "renovate@44.52.1"],
            cwd=tmp,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise Failure(
            "npm is required on PATH to provision renovate@44.52.1 when "
            "RENOVATE_NODE_PATH is unset"
        ) from e
    if proc.returncode != 0:
        raise Failure(
            f"renovate package unavailable and npm install failed ({proc.returncode}): "
            f"{proc.stderr[-500:]}"
        )
    root = tmp / "node_modules" / "renovate"
    require(root.is_dir(), f"npm install did not produce {root}")
    return str(root)


def run_renovate_probe(group_rules: list[dict], automerge_rules: list[dict]) -> dict:
    renovate_root = ensure_renovate()
    payload = {
        "groupRules": group_rules,
        "automergeRules": automerge_rules,
        "packages": K8S_PACKAGES,
        "unrelated": UNRELATED_PACKAGE,
    }
    env = os.environ.copy()
    env["RENOVATE_NODE_PATH"] = renovate_root
    node_modules = str(Path(renovate_root).parent)
    env["NODE_PATH"] = node_modules + (
        os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else ""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(RENOVATE_PROBE)
        probe_path = fh.name
    try:
        proc = subprocess.run(
            ["node", probe_path],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass
    if proc.returncode != 0:
        raise Failure(
            f"renovate probe failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise Failure(f"probe returned non-JSON: {proc.stdout[:500]}") from e


def test_renovate_behavior(probe: dict[str, Any]) -> None:
    gn = probe["checks"]["groupName"]
    for pkg in K8S_PACKAGES:
        require(
            gn.get(pkg) == "Kubernetes",
            f"applyPackageRules(groups.json5) must resolve groupName 'Kubernetes' for {pkg}, got {gn.get(pkg)!r}",
        )
    require(
        gn.get(UNRELATED_PACKAGE) is None,
        f"{UNRELATED_PACKAGE} must NOT be swept into the Kubernetes group, got {gn.get(UNRELATED_PACKAGE)!r}",
    )

    am = probe["checks"]["automerge"]
    for pkg in K8S_PACKAGES:
        for label in ("digest", "patch", "minor"):
            key = f"{pkg}::{label}"
            row = am[key]
            require(
                row["automerge"] is False,
                f"{key} must resolve automerge:false (grouping must not make this safe to auto-merge), got {row}",
            )
            require(
                row["groupName"] == "Kubernetes",
                f"{key} must still carry groupName 'Kubernetes' alongside automerge:false, got {row}",
            )
    unrelated_key = f"{UNRELATED_PACKAGE}::minor"
    require(
        am[unrelated_key]["automerge"] is True,
        f"unrelated package must keep automerge:true, got {am[unrelated_key]}",
    )
    require(
        am[unrelated_key]["groupName"] is None,
        f"unrelated package must not be grouped, got {am[unrelated_key]}",
    )

    require(
        probe["checks"]["unguarded"]["automerge"] is True,
        "without the Kubernetes automerge:false rule, a grouped minor bump would "
        f"automerge (guard is load-bearing); got {probe['checks']['unguarded']}",
    )


def test_version_consistency_tracks_same_packages() -> None:
    """The 5 grouped/guarded packages must be exactly the ones
    version-consistency.sh (and the KubernetesUpgrade CR) actually checks -
    otherwise the group could drift from the check it exists to satisfy."""
    text = MACHINECONFIG.read_text()
    found = set(re.findall(r"depName=(\S+)", text))
    k8s_found = {p for p in found if p in K8S_PACKAGES or p.startswith("registry.k8s.io/kube-")}
    require(
        k8s_found == set(K8S_PACKAGES),
        f"machineconfig.yaml.j2 renovate depName pins for Kubernetes images must be exactly "
        f"{sorted(K8S_PACKAGES)}, found {sorted(k8s_found)}",
    )
    require(KUBERNETESUPGRADE.is_file(), f"missing {KUBERNETESUPGRADE}")


def main() -> int:
    failures: list[str] = []
    passed = 0

    def run(name: str, fn) -> None:
        nonlocal passed
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Failure as e:
            print(f"[FAIL] {name}: {e}")
            failures.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001 - surface unexpected probe errors
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")
            failures.append(f"{name}: {e}")

    group_rules = load_group_rules()
    automerge_rules = load_automerge_rules()

    run("Kubernetes group rule shape (5 packages, docker datasource only)", lambda: test_group_rule_shape(group_rules))
    run(
        "Kubernetes automerge:false beats earlier blanket true (last-match-wins)",
        lambda: test_automerge_rule_shape_and_order(automerge_rules),
    )
    run("machineconfig.yaml.j2 depName pins match the grouped/guarded package set", test_version_consistency_tracks_same_packages)

    probe_holder: dict[str, Any] = {}

    def _probe() -> None:
        probe_holder["data"] = run_renovate_probe(group_rules, automerge_rules)
        test_renovate_behavior(probe_holder["data"])

    run(
        "renovate applyPackageRules: all 5 packages group AND stay automerge:false; unrelated package untouched; guard is load-bearing",
        _probe,
    )

    if probe_holder.get("data") and not failures:
        print("---PROBE_SUMMARY---")
        print(json.dumps(probe_holder["data"], indent=2))

    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
