#!/usr/bin/env python3
"""Behavioral regression test for the Plex regex-versioning Renovate rule.

media/plex runs docker.io/plexinc/pms-docker:1.43.1.10611-1e34174b1
(kubernetes/apps/base/media/plex/app/helmrelease.yaml) and zero Renovate PRs
have ever opened for it, despite 1.43.2/1.43.3/1.43.4 all being live upstream
since 2026-09-10 - the dependency dashboard (#489) lists the image but has
never shown an "Updates:" line for it.

Root cause (traced directly in the installed renovate@44.52.1 package, not a
source grep): Plex tags are X.Y.Z.BUILD-HASH, where HASH is a per-release git
commit shortref that never repeats. The real lookup pipeline
(dist/workers/repository/process/lookup/index.js) gates every candidate
release through `versioningApi.isCompatible(candidate, currentValue)` in
addition to filterVersions - and the default "docker" versioning scheme's
isCompatible() requires the dash-suffix to match the currently-installed
tag's suffix EXACTLY (dist/modules/versioning/docker/index.js). Since the
hash differs on every release, isCompatible is false for every real update
and true only for the tag already installed, so the lookup silently produces
zero candidates forever.

The fix (.renovate/overrides.json5) adds a packageRule pinning
`versioning: "regex:^(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)\\.(?<build>\\d+)-[a-f0-9]+$"`
for docker.io/plexinc/pms-docker. Renovate's regex versioning
(dist/modules/versioning/regex/index.js) only treats a named `compatibility`
capture group as a compatibility qualifier; leaving the hash uncaptured means
every tag is "compatible" with every other tag, which is how these images
actually relate on Docker Hub.

This test proves the fix behaviorally against Renovate's own compiled code,
not a source grep:

  1. applyPackageRules resolves the regex versioning scheme for
     docker.io/plexinc/pms-docker specifically, and leaves an unrelated
     docker package on the default scheme (negative control).
  2. Under the resolved regex scheme, isCompatible(newer tag, current tag) is
     true and isGreaterThan orders them correctly, for the installed tag and
     a later, format-correct release.
  3. Reproduces the bug: under the default "docker" scheme, the same tag pair
     is NOT compatible - confirming the mechanism this rule fixes.
  4. Replicates the real lookup gate (filterVersions + isCompatible, the
     exact composition used in lookup/index.js) over a realistic release
     series: zero survivors under "docker" (matching "no PR has ever
     opened"), all newer releases survive under the regex scheme.
  5. getUpdateType classifies the jump to that later release as an ordinary
     "patch" update, matching the packageRule's comment and the repo-wide
     patch-automerge rule it relies on (no automerge rule is added or
     changed by this fix).
  6. Adversarial: the regex scheme rejects tags that don't fit the
     X.Y.Z.BUILD-HASH shape (bare "latest", a missing hash, a non-hex hash,
     only three numeric components) - the fix is not simply "accept
     everything".

CURRENT_TAG tracks the live helmrelease.yaml tag and is a deliberate canary
(see test_live_tag_matches_investigation): a Renovate-driven bump moves it,
and this test's own failure message says to update it here. It last moved on
2026-09-27 when a Renovate PR (docker.io/plexinc/pms-docker 1.43.1.10611-
1e34174b1 -> 1.43.4.10903-e5521bd8c) landed - proof the regex-versioning fix
is working, so the rule is still needed for the next such bump.

Requires a local `renovate` install (RENOVATE_NODE_PATH) or network access to
`npm install renovate@44.52.1` - same harness as talos-renovate-pin-test.py /
kubernetes-renovate-group-test.py.
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
PLEX_HELMRELEASE = (
    ROOT / "kubernetes" / "apps" / "base" / "media" / "plex" / "app" / "helmrelease.yaml"
)

PACKAGE = "docker.io/plexinc/pms-docker"
# Adversarial control: a real docker-datasource package with its own override
# rule in the same file, which must NOT pick up the Plex regex scheme.
UNRELATED_PACKAGE = "quay.io/ceph/ceph"

# The tag actually deployed today (kubernetes/apps/base/media/plex/app/helmrelease.yaml).
CURRENT_TAG = "1.43.4.10903-e5521bd8c"
# A later, format-correct release used to prove the fix against (hash value
# is illustrative - only the shape and distinctness of the hash matters).
TARGET_TAG = "1.43.7.11200-e5521bd9c"
# Format-correct intermediate releases (hash values are illustrative - only
# the shape and distinctness of the hash matters for this rule).
CANDIDATE_TAGS = [
    "1.43.5.11000-aaaaaaaaa",
    "1.43.6.11100-bbbbbbbbb",
    TARGET_TAG,
]

MALFORMED_TAGS = [
    "latest",
    "1.43.1.10611",  # missing build-hash suffix entirely
    "1.43.1.10611-XYZ123",  # non-hex hash
    "1.43.1-1e34174b1",  # only three numeric components, no build
]

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
const { getUpdateType } = await load('dist/workers/repository/process/lookup/update-type.js');
const { filterVersions } = await load('dist/workers/repository/process/lookup/filter.js');
const versioningIndex = await load('dist/modules/versioning/index.js');
const docker = (await load('dist/modules/versioning/docker/index.js')).default;

const packageRules = input.packageRules;
const pkg = input.package;
const unrelated = input.unrelatedPackage;
const currentTag = input.currentTag;
const targetTag = input.targetTag;
const candidateTags = input.candidateTags;
const malformedTags = input.malformedTags;

const results = { renovateVersion: require(path.join(root, 'package.json')).version, checks: {} };

// 1. applyPackageRules: the regex versioning attaches only to the Plex package.
const plexCfg = await applyPackageRules({
  depName: pkg,
  packageName: pkg,
  datasource: 'docker',
  currentValue: currentTag,
  packageRules,
});
const unrelatedCfg = await applyPackageRules({
  depName: unrelated,
  packageName: unrelated,
  datasource: 'docker',
  currentValue: '19.2.3',
  packageRules,
});
results.checks.resolvedVersioning = {
  plex: plexCfg.versioning ?? null,
  unrelated: unrelatedCfg.versioning ?? null,
};

// Resolve the ACTUAL versioning api the way Renovate's own lookup does -
// via modules/versioning/index.js's get(), fed the resolved config string.
const regexApi = versioningIndex.get(plexCfg.versioning);

// 2 + 6. isValid on real/candidate tags and on deliberately malformed ones.
const validity = {};
for (const t of [currentTag, ...candidateTags]) {
  validity[t] = regexApi.isValid(t);
}
const malformed = {};
for (const t of malformedTags) {
  malformed[t] = regexApi.isValid(t);
}
results.checks.validity = validity;
results.checks.malformed = malformed;

// 2 + 3. isCompatible/isGreaterThan under the fix vs. under bare "docker".
const compatFixed = {};
const compatBug = {};
const orderFixed = {};
for (const t of candidateTags) {
  compatFixed[t] = regexApi.isCompatible(t, currentTag);
  compatBug[t] = docker.isCompatible(t, currentTag);
  orderFixed[t] = regexApi.isGreaterThan(t, currentTag);
}
results.checks.compatFixed = compatFixed;
results.checks.compatBug = compatBug;
results.checks.orderFixed = orderFixed;

// 4. Replicate the exact real-lookup composition: filterVersions(...) then
// .filter(v => isCompatible(v, compareValue)) - dist/workers/repository/
// process/lookup/index.js's actual line, not a re-derivation of it.
const releaseSeries = [currentTag, ...candidateTags].map((v) => ({ version: v }));
const baseCfg = { depName: pkg, ignoreUnstable: true, ignoreDeprecated: true, respectLatest: false };
function surviving(versioningApi) {
  const filtered = filterVersions(baseCfg, currentTag, undefined, releaseSeries, versioningApi);
  return filtered.filter((v) => versioningApi.isCompatible(v.version, currentTag)).map((v) => v.version);
}
results.checks.survivingUnderDocker = surviving(docker);
results.checks.survivingUnderRegex = surviving(regexApi);

// 5. getUpdateType for the real reported jump.
results.checks.updateType = getUpdateType({}, regexApi, currentTag, targetTag);

process.stdout.write(JSON.stringify(results, null, 2));
"""


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_subset(text: str) -> Any:
    """Minimal JSON5 subset parser matching the sibling renovate tests."""

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


def load_overrides_package_rules() -> list[dict[str, Any]]:
    data = parse_json5_subset(OVERRIDES.read_text())
    rules = data.get("packageRules")
    require(isinstance(rules, list) and rules, "overrides.json5 has no packageRules")
    return rules


def test_plex_rule_shape(package_rules: list[dict[str, Any]]) -> None:
    matches = [
        r
        for r in package_rules
        if isinstance(r, dict) and r.get("matchPackageNames") == [PACKAGE]
    ]
    require(len(matches) == 1, f"expected exactly one Plex packageRule, got {len(matches)}")
    rule = matches[0]
    require(
        rule.get("matchDatasources") == ["docker"],
        f"Plex rule must scope matchDatasources to docker, got {rule.get('matchDatasources')!r}",
    )
    versioning = rule.get("versioning")
    require(isinstance(versioning, str), f"Plex rule missing a versioning string, got {versioning!r}")
    require(versioning.startswith("regex:"), f"Plex rule versioning must be a regex scheme, got {versioning!r}")
    require(
        "compatibility" not in versioning,
        "Plex rule must NOT name a 'compatibility' capture group - that reproduces the "
        f"exact bug it fixes (renovate's regex isCompatible requires an exact match on it), got {versioning!r}",
    )
    require(
        "automerge" not in rule,
        "Plex rule must not add/change an automerge setting - the fix relies entirely on "
        f"the existing repo-wide patch automerge rule, got keys {sorted(rule.keys())!r}",
    )


def test_live_tag_matches_investigation() -> None:
    """The tag this test proves the fix against must be the one actually deployed,
    not a stale figure from the investigation report."""
    text = PLEX_HELMRELEASE.read_text()
    require(f"repository: {PACKAGE}" in text, f"{PLEX_HELMRELEASE} no longer references {PACKAGE}")
    require(
        f"tag: {CURRENT_TAG}" in text,
        f"{PLEX_HELMRELEASE} tag no longer matches investigated {CURRENT_TAG!r} - "
        "update CURRENT_TAG (and re-check whether the rule is still needed) before trusting this test",
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
    tmp = Path(tempfile.mkdtemp(prefix="renovate-plex-test-"))
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


def run_renovate_probe(package_rules: list[dict[str, Any]]) -> dict:
    renovate_root = ensure_renovate()
    payload = {
        "packageRules": package_rules,
        "package": PACKAGE,
        "unrelatedPackage": UNRELATED_PACKAGE,
        "currentTag": CURRENT_TAG,
        "targetTag": TARGET_TAG,
        "candidateTags": CANDIDATE_TAGS,
        "malformedTags": MALFORMED_TAGS,
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
    rv = probe["checks"]["resolvedVersioning"]
    require(
        isinstance(rv.get("plex"), str) and rv["plex"].startswith("regex:"),
        f"applyPackageRules must resolve a regex versioning scheme for {PACKAGE}, got {rv.get('plex')!r}",
    )
    require(
        rv.get("unrelated") is None,
        f"{UNRELATED_PACKAGE} must NOT pick up the Plex regex scheme (packageRules must be "
        f"scoped by matchPackageNames), got {rv.get('unrelated')!r}",
    )

    validity = probe["checks"]["validity"]
    for t in [CURRENT_TAG, *CANDIDATE_TAGS]:
        require(validity.get(t) is True, f"regex versioning must accept real tag {t!r}, got {validity.get(t)!r}")

    malformed = probe["checks"]["malformed"]
    for t in MALFORMED_TAGS:
        require(
            malformed.get(t) is False,
            f"regex versioning must reject malformed tag {t!r} (it must not accept everything), got {malformed.get(t)!r}",
        )

    compat_fixed = probe["checks"]["compatFixed"]
    compat_bug = probe["checks"]["compatBug"]
    order_fixed = probe["checks"]["orderFixed"]
    for t in CANDIDATE_TAGS:
        require(
            compat_fixed.get(t) is True,
            f"under the regex scheme, {t!r} must be compatible with the installed {CURRENT_TAG!r}, "
            f"got {compat_fixed.get(t)!r}",
        )
        require(
            compat_bug.get(t) is False,
            f"reproduction check: under bare 'docker' versioning {t!r} must NOT be compatible with "
            f"{CURRENT_TAG!r} (different per-release hash) - if this is no longer false, the bug this "
            f"rule fixes may already be gone upstream and the rule should be re-evaluated, got {compat_bug.get(t)!r}",
        )
        require(
            order_fixed.get(t) is True,
            f"under the regex scheme, {t!r} must sort as greater than the installed {CURRENT_TAG!r}, "
            f"got {order_fixed.get(t)!r}",
        )

    surviving_docker = probe["checks"]["survivingUnderDocker"]
    surviving_regex = probe["checks"]["survivingUnderRegex"]
    require(
        surviving_docker == [],
        "reproduction check: the real lookup gate (filterVersions + isCompatible) must leave ZERO "
        f"survivors under bare 'docker' versioning (matching '0 Renovate PRs ever opened'), got {surviving_docker!r}",
    )
    require(
        set(surviving_regex) == set(CANDIDATE_TAGS),
        f"the same lookup gate must let every newer release through under the regex scheme, "
        f"expected {sorted(CANDIDATE_TAGS)!r}, got {sorted(surviving_regex)!r}",
    )

    require(
        probe["checks"]["updateType"] == "patch",
        f"the jump ({CURRENT_TAG} -> {TARGET_TAG}) must classify as an ordinary 'patch' update "
        f"(major/minor unchanged) so it flows through the existing patch-automerge rule, got {probe['checks']['updateType']!r}",
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

    package_rules = load_overrides_package_rules()

    run("Plex packageRule shape (docker datasource, regex versioning, no compatibility group, no automerge)", lambda: test_plex_rule_shape(package_rules))
    run("live helmrelease tag matches the tag this test proves the fix against", test_live_tag_matches_investigation)

    probe_holder: dict[str, Any] = {}

    def _probe() -> None:
        probe_holder["data"] = run_renovate_probe(package_rules)
        test_renovate_behavior(probe_holder["data"])

    run(
        "renovate's own compiled lookup: regex scheme resolves to Plex only, "
        "real tags validate, incompatibility bug reproduces under 'docker', fix restores "
        "all real candidates through the actual filterVersions+isCompatible gate, update "
        "classifies as patch",
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
