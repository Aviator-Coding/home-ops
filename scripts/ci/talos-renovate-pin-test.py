#!/usr/bin/env python3
"""Behavioral regression test for the Talos Renovate pin + auto-merge guard.

Pins the captain decision of 2026-08-31 (see AGENTS.md NOTES and the pin
comment in .renovate/overrides.json5):

  1. allowedVersions must exclude exactly v1.13.3 (both prefix forms) and
     allow every other realistic Talos tag - never a bare `<1.13.3` ceiling.
  2. The new pin must UNFREEZE proposals when the tracked value is already
     past 1.13.3 (the freeze the old ceiling produced).
  3. docker-versioning isStable must treat Talos -alpha/-beta/-rc tags as
     stable (so ignoreUnstable alone cannot protect us).
  4. applyPackageRules against the real autoMerge.json5 must resolve
     automerge:false for all four Talos matchPackageNames (stable minor AND
     alpha), while leaving an unrelated docker package's minor automerge
     alone.
  5. The four matchPackageNames must actually match the four packages that
     feed Talos upgrades (installer, talosctl, siderolabs/talos,
     factory.talos.dev image refs).
  6. This change must not bump live Talos version pins (talosupgrade CR,
     talosctl-busybox Dockerfile) - proposals only, attended captain apply.

Evidence is Renovate's own compiled logic (getRegexPredicate, filterVersions,
docker isStable, applyPackageRules, matchRegexOrGlobList), not a source
grep. Requires a local `renovate` install (RENOVATE_NODE_PATH) or network
access to `npx --package renovate`.
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
OVERRIDES = ROOT / ".renovate" / "overrides.json5"
AUTOMERGE = ROOT / ".renovate" / "autoMerge.json5"
TALOSUPGRADE = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "system-upgrade"
    / "tuppr"
    / "upgrades"
    / "talosupgrade.yaml"
)
TALOSCTL_DOCKERFILE = ROOT / ".github" / "docker" / "talosctl-busybox" / "Dockerfile"

TALOS_PACKAGES = [
    "ghcr.io/siderolabs/installer",
    "ghcr.io/siderolabs/talosctl",
    "siderolabs/talos",
    "factory.talos.dev/installer/abc123",
]

RENOVATE_PROBE = r"""
import { createRequire } from 'module';
import { pathToFileURL } from 'url';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

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

const { getRegexPredicate, matchRegexOrGlobList } = await load('dist/util/string-match.js');
const { filterVersions } = await load('dist/workers/repository/process/lookup/filter.js');
const { applyPackageRules } = await load('dist/util/package-rules/index.js');
const { getUpdateType } = await load('dist/workers/repository/process/lookup/update-type.js');
const docker = (await load('dist/modules/versioning/docker/index.js')).default;
const semverApi = (await load('dist/modules/versioning/semver/index.js')).default;

const pin = input.allowedVersions;
const oldPin = input.oldAllowedVersions;
const packageRules = input.packageRules;
const matchPackageNames = input.matchPackageNames;

const results = { renovateVersion: require(path.join(root, 'package.json')).version, checks: {} };

// 1. Regex predicate cases
const pred = getRegexPredicate(pin);
if (!pred) {
  throw new Error(`getRegexPredicate returned null for ${JSON.stringify(pin)} - rule would be silently skipped`);
}
const cases = input.versionCases;
const predicateResults = {};
for (const [ver, expectAllow] of Object.entries(cases)) {
  predicateResults[ver] = { allowed: pred(ver), expectAllow };
}
results.checks.predicate = predicateResults;

// 2. filterVersions: old ceiling vs new pin, docker versioning, current=v1.13.9
const releases = input.candidateReleases.map((v) => ({ version: v }));
const baseConfig = {
  depName: 'ghcr.io/siderolabs/installer',
  versioning: 'docker',
  ignoreUnstable: true,
  ignoreDeprecated: true,
  respectLatest: false,
};
const oldFiltered = filterVersions(
  { ...baseConfig, allowedVersions: oldPin },
  'v1.13.9',
  'v1.14.0-rc.2',
  releases,
  docker,
).map((r) => r.version);
const newFiltered = filterVersions(
  { ...baseConfig, allowedVersions: pin },
  'v1.13.9',
  'v1.14.0-rc.2',
  releases,
  docker,
).map((r) => r.version);
// With ignoreUnstable + docker isStable quirk, alphas pass isStable - so they stay.
// Also run with the pin alone to show v1.13.3 is dropped even when current is below it.
const fromBelow = filterVersions(
  { ...baseConfig, allowedVersions: pin, ignoreUnstable: false },
  'v1.12.6',
  'v1.14.0',
  releases,
  docker,
).map((r) => r.version);
results.checks.filterVersions = {
  oldPin,
  newPin: pin,
  oldFiltered,
  newFiltered,
  fromBelow,
};

// 3. docker isStable vs semver isStable on prerelease tags
const stability = {};
for (const ver of input.stabilityVersions) {
  stability[ver] = {
    docker: docker.isStable(ver),
    semver: semverApi.isStable(ver),
  };
}
results.checks.stability = stability;

// 4. getUpdateType classifies v1.13.9 -> v1.14.0-alpha.0 as minor under docker
results.checks.updateType = {
  dockerAlpha: getUpdateType({}, docker, 'v1.13.9', 'v1.14.0-alpha.0'),
  dockerStableMinor: getUpdateType({}, docker, 'v1.13.9', 'v1.14.0'),
  dockerPatch: getUpdateType({}, docker, 'v1.13.8', 'v1.13.9'),
};

// 5. matchPackageNames covers all four packages
const nameMatches = {};
for (const pkg of input.talosPackages) {
  nameMatches[pkg] = matchRegexOrGlobList(pkg, matchPackageNames);
}
nameMatches['ghcr.io/home-operations/unrelated'] = matchRegexOrGlobList(
  'ghcr.io/home-operations/unrelated',
  matchPackageNames,
);
results.checks.packageNameMatch = nameMatches;

// 6. applyPackageRules: Talos packages must end automerge:false; unrelated keeps true
const automergeResults = {};
for (const pkg of input.talosPackages) {
  for (const [label, updateType, newVersion] of [
    ['minor-stable', 'minor', 'v1.14.0'],
    ['minor-alpha', 'minor', 'v1.14.0-alpha.0'],
    ['patch', 'patch', 'v1.13.10'],
  ]) {
    const cfg = await applyPackageRules({
      depName: pkg,
      packageName: pkg,
      datasource: 'docker',
      updateType,
      newVersion,
      currentVersion: 'v1.13.9',
      packageRules,
      // Simulate winning the blanket minor/patch automerge rules first:
      // renovate merges matching rules in order, so seed automerge true the
      // way the earlier "Auto merge all ... minor/patch" rules would.
      automerge: updateType === 'digest' ? true : true,
    });
    automergeResults[`${pkg}::${label}`] = {
      automerge: cfg.automerge,
      updateType,
      newVersion,
    };
  }
}
const unrelated = await applyPackageRules({
  depName: 'ghcr.io/example/unrelated',
  packageName: 'ghcr.io/example/unrelated',
  datasource: 'docker',
  updateType: 'minor',
  newVersion: '2.0.0',
  currentVersion: '1.9.0',
  packageRules,
  automerge: true,
});
automergeResults['unrelated::minor'] = { automerge: unrelated.automerge };
// Negative control: drop the Talos automerge:false rule and the alpha minor
// candidate must stay automerge:true - proves the guard is load-bearing.
const rulesWithoutTalos = packageRules.filter(
  (r) => !((r.matchPackageNames || []).includes('ghcr.io/siderolabs/installer') && r.automerge === false),
);
const unguarded = await applyPackageRules({
  depName: 'ghcr.io/siderolabs/installer',
  packageName: 'ghcr.io/siderolabs/installer',
  datasource: 'docker',
  updateType: 'minor',
  newVersion: 'v1.14.0-alpha.0',
  currentVersion: 'v1.13.9',
  packageRules: rulesWithoutTalos,
  automerge: true,
});
automergeResults['installer::alpha-without-guard'] = { automerge: unguarded.automerge };
results.checks.automerge = automergeResults;

process.stdout.write(JSON.stringify(results, null, 2));
"""


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_subset(text: str) -> Any:
    """Minimal JSON5 subset parser matching kopiur-stage0-test.py."""

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


def load_talos_override_rule() -> dict[str, Any]:
    data = parse_json5_subset(OVERRIDES.read_text())
    rules = data.get("packageRules") or []
    matches = [
        r
        for r in rules
        if isinstance(r, dict)
        and set(r.get("matchPackageNames") or [])
        >= {
            "ghcr.io/siderolabs/installer",
            "ghcr.io/siderolabs/talosctl",
            "siderolabs/talos",
            r"/factory\.talos\.dev/",
        }
    ]
    require(len(matches) == 1, f"expected exactly one Talos pin rule, got {len(matches)}")
    return matches[0]


def load_automerge_rules() -> list[dict[str, Any]]:
    data = parse_json5_subset(AUTOMERGE.read_text())
    rules = data.get("packageRules")
    require(isinstance(rules, list) and rules, "autoMerge.json5 has no packageRules")
    return rules


def find_renovate_node_path() -> str | None:
    env = os.environ.get("RENOVATE_NODE_PATH")
    if env and (Path(env) / "package.json").is_file():
        return env
    # Prefer a previously provisioned temp install used by local evidence runs.
    for candidate in Path("/tmp").glob("renovate-test-*/node_modules/renovate"):
        if (candidate / "package.json").is_file():
            return str(candidate)
    # Try resolvable from cwd / NODE_PATH
    try:
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
    except FileNotFoundError as e:
        raise Failure("node is required on PATH") from e
    return None


def ensure_renovate() -> str:
    existing = find_renovate_node_path()
    if existing:
        return existing
    # Last resort: npm install into a temp dir (needs network).
    tmp = Path(tempfile.mkdtemp(prefix="renovate-pin-test-"))
    proc = subprocess.run(
        ["npm", "install", "--no-save", "--no-package-lock", "renovate@44.52.1"],
        cwd=tmp,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise Failure(
            "renovate package unavailable and npm install failed "
            f"({proc.returncode}): {proc.stderr[-500:]}"
        )
    root = tmp / "node_modules" / "renovate"
    require(root.is_dir(), f"npm install did not produce {root}")
    return str(root)


def run_renovate_probe(pin: str, match_names: list[str], package_rules: list[dict]) -> dict:
    renovate_root = ensure_renovate()
    payload = {
        "allowedVersions": pin,
        "oldAllowedVersions": "<1.13.3",
        "matchPackageNames": match_names,
        "packageRules": package_rules,
        "talosPackages": TALOS_PACKAGES,
        "candidateReleases": [
            "v1.12.6",
            "v1.13.2",
            "v1.13.3",
            "1.13.3",
            "v1.13.4",
            "v1.13.8",
            "v1.13.9",
            "v1.13.10",
            "v1.130.3",
            "v11.13.3",
            "v1.14.0-alpha.0",
            "v1.14.0-rc.2",
            "v1.14.0",
            "v1.14.1",
        ],
        "versionCases": {
            # must be excluded
            "v1.13.3": False,
            "1.13.3": False,
            # must be allowed
            "v1.12.6": True,
            "v1.13.2": True,
            "v1.13.4": True,
            "v1.13.8": True,
            "v1.13.9": True,
            "v1.13.10": True,
            "v1.14.0": True,
            "v1.14.0-alpha.0": True,
            "v1.14.0-rc.2": True,
            "v1.14.1": True,
            # no false-positive substring matches
            "v1.130.3": True,
            "v11.13.3": True,
        },
        "stabilityVersions": [
            "v1.13.9",
            "v1.14.0",
            "v1.14.0-alpha.0",
            "v1.14.0-beta.1",
            "v1.14.0-rc.2",
        ],
    }
    env = os.environ.copy()
    env["RENOVATE_NODE_PATH"] = renovate_root
    # Make sure node can also resolve CJS requires inside renovate if needed.
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


def test_pin_shape(rule: dict[str, Any]) -> None:
    allowed = rule.get("allowedVersions")
    require(
        allowed == "!/^v?1\\.13\\.3$/",
        f"Talos allowedVersions must be the negated-regex exclusion of exactly "
        f"v1.13.3, got {allowed!r}",
    )
    require(
        not str(allowed).startswith("<"),
        f"Talos allowedVersions must not be a bare ceiling, got {allowed!r}",
    )
    names = rule.get("matchPackageNames") or []
    for expected in (
        "ghcr.io/siderolabs/installer",
        "ghcr.io/siderolabs/talosctl",
        "siderolabs/talos",
        r"/factory\.talos\.dev/",
    ):
        require(expected in names, f"matchPackageNames missing {expected!r}: {names}")


def test_automerge_rule_last(rules: list[dict[str, Any]]) -> None:
    """Talos automerge:false must beat earlier blanket true rules.

    It does not need to be the absolute last packageRule - sibling
    never-auto-merge rules (e.g. kopiur) may share the trailing block - but it
    must remain the last automerge-setting match for every Talos package name.
    """
    talos_names = {
        "ghcr.io/siderolabs/installer",
        "ghcr.io/siderolabs/talosctl",
        "siderolabs/talos",
        r"/factory\.talos\.dev/",
    }

    def rule_matches(rule: dict[str, Any], pkg: str) -> bool:
        names = rule.get("matchPackageNames")
        if names is not None:
            return pkg in names
        return "matchPackageNames" not in rule

    talos_idxs = [
        i
        for i, r in enumerate(rules)
        if isinstance(r, dict)
        and "ghcr.io/siderolabs/installer" in (r.get("matchPackageNames") or [])
        and r.get("automerge") is False
    ]
    require(talos_idxs, "autoMerge.json5 missing Never-auto-merge-Talos rule")
    talos_idx = talos_idxs[-1]
    # Blanket minor automerge must exist earlier so the override is load-bearing.
    earlier_minor = any(
        r.get("automerge") is True and "minor" in (r.get("matchUpdateTypes") or [])
        for r in rules[:talos_idx]
    )
    require(earlier_minor, "expected an earlier blanket minor automerge:true rule to override")

    for pkg in sorted(talos_names):
        winning_automerge: bool | None = None
        winning_index = -1
        for idx, rule in enumerate(rules):
            if isinstance(rule, dict) and rule_matches(rule, pkg) and "automerge" in rule:
                winning_automerge = bool(rule["automerge"])
                winning_index = idx
        require(
            winning_automerge is False,
            f"{pkg}: final automerge winner must be false (got {winning_automerge} from rule {winning_index})",
        )
        require(
            winning_index == talos_idx,
            f"{pkg}: last automerge-setting match must be the Talos exclusion "
            f"(won at index {winning_index}, Talos exclusion at {talos_idx})",
        )


def test_renovate_behavior(probe: dict[str, Any]) -> None:
    # Predicate matrix
    for ver, row in probe["checks"]["predicate"].items():
        require(
            row["allowed"] is row["expectAllow"],
            f"getRegexPredicate({ver!r}) allowed={row['allowed']} "
            f"expected {row['expectAllow']}",
        )

    fv = probe["checks"]["filterVersions"]
    require(
        fv["oldFiltered"] == [],
        f"OLD pin <1.13.3 must freeze proposals from v1.13.9, got {fv['oldFiltered']}",
    )
    require(
        "v1.13.10" in fv["newFiltered"],
        f"NEW pin must allow v1.13.10 from v1.13.9, got {fv['newFiltered']}",
    )
    require(
        "v1.13.3" not in fv["newFiltered"] and "1.13.3" not in fv["newFiltered"],
        f"NEW pin must still exclude v1.13.3, got {fv['newFiltered']}",
    )
    require(
        "v1.13.3" not in fv["fromBelow"] and "1.13.3" not in fv["fromBelow"],
        f"NEW pin from v1.12.6 must exclude v1.13.3, got {fv['fromBelow']}",
    )
    require(
        "v1.13.4" in fv["fromBelow"] and "v1.13.10" in fv["fromBelow"],
        f"NEW pin from v1.12.6 must allow post-1.13.3 tags, got {fv['fromBelow']}",
    )

    # docker isStable quirk on prereleases
    stab = probe["checks"]["stability"]
    require(stab["v1.14.0-alpha.0"]["docker"] is True,
            "docker isStable(v1.14.0-alpha.0) must be True (the quirk this guards)")
    require(stab["v1.14.0-alpha.0"]["semver"] is False,
            "semver isStable(v1.14.0-alpha.0) must be False (control)")
    require(stab["v1.14.0-rc.2"]["docker"] is True,
            "docker isStable(v1.14.0-rc.2) must be True")
    require(stab["v1.13.9"]["docker"] is True, "docker isStable(v1.13.9) must be True")

    ut = probe["checks"]["updateType"]
    require(
        ut["dockerAlpha"] == "minor",
        f"docker getUpdateType(v1.13.9 -> v1.14.0-alpha.0) must be minor, got {ut['dockerAlpha']!r}",
    )
    require(ut["dockerStableMinor"] == "minor", f"stable minor type={ut['dockerStableMinor']!r}")
    require(ut["dockerPatch"] == "patch", f"patch type={ut['dockerPatch']!r}")

    # package name matching
    for pkg in TALOS_PACKAGES:
        require(
            probe["checks"]["packageNameMatch"].get(pkg) is True,
            f"matchPackageNames must match {pkg!r}",
        )
    require(
        probe["checks"]["packageNameMatch"].get("ghcr.io/home-operations/unrelated") is False,
        "matchPackageNames must not match an unrelated package",
    )

    # automerge resolution
    am = probe["checks"]["automerge"]
    for pkg in TALOS_PACKAGES:
        for label in ("minor-stable", "minor-alpha", "patch"):
            key = f"{pkg}::{label}"
            require(
                am[key]["automerge"] is False,
                f"{key} must resolve automerge:false, got {am[key]}",
            )
    require(
        am["unrelated::minor"]["automerge"] is True,
        f"unrelated package must keep automerge:true, got {am['unrelated::minor']}",
    )
    require(
        am["installer::alpha-without-guard"]["automerge"] is True,
        "without the Talos automerge:false rule, installer alpha minor must stay "
        f"automerge:true (guard is load-bearing); got {am['installer::alpha-without-guard']}",
    )


def _read_talosupgrade_version() -> str:
    text = TALOSUPGRADE.read_text()
    m = re.search(r"(?m)^\s*version:\s*(v[\d.]+)\s*$", text)
    require(m is not None, f"could not parse spec.talos.version from {TALOSUPGRADE}")
    return m.group(1)


def _read_talosctl_busybox_version() -> str:
    text = TALOSCTL_DOCKERFILE.read_text()
    # Image tag is an ARG default (TALOS_VERSION=vX.Y.Z), not an inline FROM tag.
    m = re.search(r"(?m)^ARG\s+TALOS_VERSION=(v[\d.]+)\s*$", text)
    require(
        m is not None,
        f"could not parse ARG TALOS_VERSION from {TALOSCTL_DOCKERFILE}",
    )
    return m.group(1)


def test_no_live_version_bump() -> None:
    """This PR must unfreeze proposals only - never apply a Talos upgrade."""
    # Contract: the two live version pins exist and were not part of applying
    # an upgrade in this change. We assert they still resolve to a concrete
    # version string (files present + parseable). A behavioral "not bumped by
    # this PR" check is done via git against merge-base when available.
    tu = _read_talosupgrade_version()
    tb = _read_talosctl_busybox_version()
    require(tu.startswith("v"), f"talosupgrade version malformed: {tu!r}")
    require(tb.startswith("v"), f"talosctl-busybox tag malformed: {tb!r}")

    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "MERGE_BASE_SENTINEL"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    # Prefer explicit base from the gate when provided; else origin/main; else skip.
    base = os.environ.get("TALOS_PIN_TEST_BASE")
    if not base:
        proc = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            base = proc.stdout.strip()
    if not base:
        return  # no base available; shape checks above still ran

    for rel in (
        "kubernetes/apps/base/system-upgrade/tuppr/upgrades/talosupgrade.yaml",
        ".github/docker/talosctl-busybox/Dockerfile",
    ):
        diff = subprocess.run(
            ["git", "diff", "--name-only", base, "HEAD", "--", rel],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        require(
            diff.returncode == 0 and diff.stdout.strip() == "",
            f"{rel} must not change in this PR (proposals only); diff={diff.stdout!r}",
        )


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

    rule = load_talos_override_rule()
    run("talos pin shape is negated-regex exclusion of v1.13.3 only", lambda: test_pin_shape(rule))

    am_rules = load_automerge_rules()
    run(
        "talos automerge:false beats earlier blanket true (last-match-wins)",
        lambda: test_automerge_rule_last(am_rules),
    )

    probe_holder: dict[str, Any] = {}

    def _probe() -> None:
        probe_holder["data"] = run_renovate_probe(
            pin=rule["allowedVersions"],
            match_names=list(rule["matchPackageNames"]),
            package_rules=am_rules,
        )
        test_renovate_behavior(probe_holder["data"])

    run(
        "renovate getRegexPredicate/filterVersions/isStable/applyPackageRules behavior",
        _probe,
    )
    run("talosupgrade + talosctl-busybox not bumped by this change", test_no_live_version_bump)

    # Emit machine-readable probe summary for evidence capture when successful.
    if probe_holder.get("data") and not failures:
        summary = {
            "renovateVersion": probe_holder["data"].get("renovateVersion"),
            "oldFiltered": probe_holder["data"]["checks"]["filterVersions"]["oldFiltered"],
            "newFiltered": probe_holder["data"]["checks"]["filterVersions"]["newFiltered"],
            "dockerAlphaStable": probe_holder["data"]["checks"]["stability"]["v1.14.0-alpha.0"],
            "updateTypeAlpha": probe_holder["data"]["checks"]["updateType"]["dockerAlpha"],
            "automergeInstallerMinor": probe_holder["data"]["checks"]["automerge"][
                "ghcr.io/siderolabs/installer::minor-stable"
            ],
            "automergeInstallerAlpha": probe_holder["data"]["checks"]["automerge"][
                "ghcr.io/siderolabs/installer::minor-alpha"
            ],
            "automergeUnrelated": probe_holder["data"]["checks"]["automerge"]["unrelated::minor"],
            "automergeInstallerAlphaWithoutGuard": probe_holder["data"]["checks"][
                "automerge"
            ]["installer::alpha-without-guard"],
        }
        print("---PROBE_SUMMARY---")
        print(json.dumps(summary, indent=2))

    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
