#!/usr/bin/env python3
"""Behavioral regression test for the rsshub digest-update throttle.

Pins the 2026-09-27 fix in `.renovate/autoMerge.json5` (see AGENTS.md
"rsshub tracks a rolling `latest` tag..."): ghcr.io/diygod/rsshub is pinned
`latest@sha256:...` and upstream rebuilds `latest` many times a day. With no
schedule, the blanket "Auto merge all container digest updates" rule merged
33 rsshub digest PRs between 2026-09-07 and 2026-09-27 - each one restarting
the pod (deploymentStrategy: Recreate). The fix adds a trailing packageRule
that narrows rsshub's digest-update schedule to once a week without
disabling automerge.

This does not grep the JSON for a `schedule:` string - a schedule key is
meaningless unless Renovate's own scheduler agrees it is valid syntax AND
actually restricts eligibility to the intended window. This test invokes
Renovate's own compiled logic, not a reimplementation:

  1. `applyPackageRules` (the real last-match-wins merge over the complete,
     real, ordered packageRules list from the file) resolves a synthetic
     rsshub digest update to automerge=true AND schedule=["before 6am on
     monday"] - proving the new rule narrows without disabling.
  2. The same function resolves an rsshub MINOR update (matchUpdateTypes is
     digest-only) and an unrelated docker package's digest update with NO
     schedule field - proving the throttle does not leak to other update
     types or other packages.
  3. `hasValidSchedule` (Renovate's own schedule-string validator) accepts
     the exact composed schedule, and rejects a deliberately broken variant
     - proving this test can fail and the syntax is not silently ignored.
  4. `isScheduledNow` (Renovate's own scheduler, with Settings.now overridden
     to fixed instants under TZ=UTC, matching the comment "No repo-wide
     timezone is configured, so this schedule runs in UTC") is true inside
     the Monday-before-6am window, false everywhere else in that week
     including the day after and the day before, and true again exactly one
     week later - proving "once a week", not "once ever" or "still daily".

Requires a local `renovate` install (RENOVATE_NODE_PATH) or network access to
`npx --package renovate`, same as talos-renovate-pin-test.py.
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
AUTOMERGE = ROOT / ".renovate" / "autoMerge.json5"
RSSHUB_HELMRELEASE = (
    ROOT / "kubernetes" / "apps" / "base" / "selfhosted" / "rsshub" / "app" / "helmrelease.yaml"
)

RSSHUB_PACKAGE = "ghcr.io/diygod/rsshub"

# [year, month, day, hour, minute] UTC tuples fed to Renovate's real
# isScheduledNow via a Settings.now override (see RENOVATE_PROBE).
TIME_PROBES: dict[str, list[int]] = {
    "mon_before_cutoff": [2026, 9, 28, 3, 0],  # Monday 03:00 UTC
    "mon_just_before_cutoff": [2026, 9, 28, 5, 59],  # Monday 05:59 UTC
    "mon_after_cutoff": [2026, 9, 28, 7, 0],  # Monday 07:00 UTC
    "sun_before": [2026, 9, 27, 23, 0],  # Sunday 23:00 UTC (day before)
    "tue_after": [2026, 9, 29, 3, 0],  # Tuesday 03:00 UTC (day after)
    "next_mon_before_cutoff": [2026, 10, 5, 3, 0],  # following Monday
}

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
const loadAbs = async (abs) => import(pathToFileURL(abs).href);

const { applyPackageRules } = await load('dist/util/package-rules/index.js');
const { isScheduledNow, hasValidSchedule } = await load(
  'dist/workers/repository/update/branch/schedule.js'
);
// Import the exact same luxon ESM build that schedule.js's own bare `import
// "luxon"` resolves to (package.json exports "." "import" condition), so
// overriding Settings.now here affects the same module-cache singleton
// schedule.js reads. luxon's "node" (CJS) build is a DIFFERENT module
// instance with its own unrelated Settings object - overriding that one
// silently does nothing to schedule.js's real clock (verified directly).
const luxonMjs = path.join(root, '..', 'luxon', 'build', 'es6', 'luxon.mjs');
const { Settings } = await loadAbs(luxonMjs);

const packageRules = input.packageRules;

const results = {
  renovateVersion: require(path.join(root, 'package.json')).version,
  checks: {},
};

async function resolveDep(packageName, updateType) {
  // Full end-to-end simulation: pass the complete, real, ordered
  // packageRules array from .renovate/autoMerge.json5 to Renovate's own
  // applyPackageRules exactly once, the same way Renovate itself accumulates
  // config across the whole list for one dependency update. No manual
  // seeding of automerge - the blanket digest rule earlier in the same list
  // supplies it for real.
  return applyPackageRules({
    depName: packageName,
    packageName,
    datasource: 'docker',
    updateType,
    newVersion: 'sha256:' + '1'.repeat(64),
    currentVersion: 'sha256:' + '0'.repeat(64),
    packageRules,
  });
}

results.checks.rsshubDigest = await resolveDep(input.rsshubPackage, 'digest');
results.checks.rsshubMinor = await resolveDep(input.rsshubPackage, 'minor');
results.checks.unrelatedDigest = await resolveDep('ghcr.io/example/unrelated', 'digest');

const rsshubSchedule = results.checks.rsshubDigest.schedule;
results.checks.scheduleValid = hasValidSchedule(rsshubSchedule);
results.checks.scheduleValidBroken = hasValidSchedule(['before 6am on mondayzzz']);

// Deliberately built with Date.UTC rather than DateTime.fromISO: luxon's
// fromISO falls back to Settings.now() internally for calendar context,
// which recurses infinitely once Settings.now itself calls into luxon.
function setNow([y, mo, d, h, mi]) {
  const ms = Date.UTC(y, mo - 1, d, h, mi, 0);
  Settings.now = () => ms;
}
function restoreNow() {
  Settings.now = () => Date.now();
}

const scheduleWindow = {};
for (const [label, parts] of Object.entries(input.timeProbes)) {
  setNow(parts);
  scheduleWindow[label] = isScheduledNow({ schedule: rsshubSchedule });
}
restoreNow();
results.checks.scheduleWindow = scheduleWindow;

const unrelatedScheduleWindow = {};
for (const [label, parts] of Object.entries(input.timeProbes)) {
  setNow(parts);
  unrelatedScheduleWindow[label] = isScheduledNow({
    schedule: results.checks.unrelatedDigest.schedule,
  });
}
restoreNow();
results.checks.unrelatedScheduleWindow = unrelatedScheduleWindow;

process.stdout.write(JSON.stringify(results, null, 2));
"""


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_subset(text: str) -> Any:
    """Minimal JSON5 subset parser matching talos-renovate-pin-test.py."""

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


def load_automerge_rules() -> list[dict[str, Any]]:
    data = parse_json5_subset(AUTOMERGE.read_text())
    rules = data.get("packageRules")
    require(isinstance(rules, list) and rules, "autoMerge.json5 has no packageRules")
    return rules


def find_rsshub_rule(rules: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    matches = [
        (i, r)
        for i, r in enumerate(rules)
        if isinstance(r, dict) and RSSHUB_PACKAGE in (r.get("matchPackageNames") or [])
    ]
    require(len(matches) == 1, f"expected exactly one rsshub packageRule, got {len(matches)}")
    return matches[0]


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
    tmp = Path(tempfile.mkdtemp(prefix="renovate-rsshub-test-"))
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


def run_renovate_probe(package_rules: list[dict]) -> dict:
    renovate_root = ensure_renovate()
    payload = {
        "packageRules": package_rules,
        "rsshubPackage": RSSHUB_PACKAGE,
        "timeProbes": TIME_PROBES,
    }
    env = os.environ.copy()
    env["RENOVATE_NODE_PATH"] = renovate_root
    node_modules = str(Path(renovate_root).parent)
    env["NODE_PATH"] = node_modules + (
        os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else ""
    )
    # The composed schedule has no explicit timezone field, so Renovate's
    # scheduler falls back to the process's local time (luxon DateTime.local()).
    # Pin the probe process to UTC to match the AGENTS.md/autoMerge.json5
    # comment's stated assumption ("No repo-wide timezone is configured, so
    # this schedule runs in UTC") rather than whatever timezone the sandbox
    # happens to run in.
    env["TZ"] = "UTC"
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


def test_rule_shape(idx: int, rule: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    require(
        rule.get("matchUpdateTypes") == ["digest"],
        f"rsshub rule must scope matchUpdateTypes to digest only, got {rule.get('matchUpdateTypes')!r}",
    )
    require(
        "automerge" not in rule,
        "rsshub rule must not set automerge itself - it should narrow the schedule of "
        "the existing blanket digest automerge rule, not re-declare it",
    )
    schedule = rule.get("schedule")
    require(
        isinstance(schedule, list) and len(schedule) == 1,
        f"rsshub rule must set a single-entry schedule list, got {schedule!r}",
    )

    blanket_digest_idxs = [
        i
        for i, r in enumerate(rules)
        if isinstance(r, dict)
        and r.get("automerge") is True
        and "digest" in (r.get("matchUpdateTypes") or [])
        and not r.get("matchPackageNames")
    ]
    require(blanket_digest_idxs, "expected a blanket digest automerge:true rule")
    require(
        idx > blanket_digest_idxs[-1],
        "rsshub schedule rule must be positioned after the blanket digest automerge rule",
    )


def test_rsshub_pinned_to_latest_digest() -> None:
    text = RSSHUB_HELMRELEASE.read_text()
    require(
        re.search(r"repository:\s*ghcr\.io/diygod/rsshub\s*\n\s*tag:\s*latest@sha256:", text)
        is not None,
        "rsshub helmrelease must still pin repository: ghcr.io/diygod/rsshub / "
        "tag: latest@sha256:... - if this ever changes to a real tag, the "
        "digest-only matchUpdateTypes scoping on the renovate rule should be "
        "revisited",
    )


def test_renovate_behavior(probe: dict[str, Any]) -> None:
    rsshub_digest = probe["checks"]["rsshubDigest"]
    require(
        rsshub_digest["automerge"] is True,
        f"rsshub digest update must resolve automerge:true, got {rsshub_digest.get('automerge')}",
    )
    require(
        rsshub_digest.get("schedule") == ["before 6am on monday"],
        f"rsshub digest update must resolve schedule=['before 6am on monday'], "
        f"got {rsshub_digest.get('schedule')!r}",
    )

    rsshub_minor = probe["checks"]["rsshubMinor"]
    require(
        rsshub_minor["automerge"] is True,
        "rsshub minor update must stay automerge:true (unaffected by the digest-only rule)",
    )
    require(
        "schedule" not in rsshub_minor,
        f"rsshub minor update must NOT inherit the digest-only schedule, "
        f"got {rsshub_minor.get('schedule')!r}",
    )

    unrelated = probe["checks"]["unrelatedDigest"]
    require(
        unrelated["automerge"] is True,
        "an unrelated docker digest package must stay automerge:true",
    )
    require(
        "schedule" not in unrelated,
        f"an unrelated docker digest package must NOT be throttled by the rsshub "
        f"rule, got {unrelated.get('schedule')!r}",
    )

    require(
        probe["checks"]["scheduleValid"][0] is True,
        f"Renovate's own hasValidSchedule must accept the composed rsshub schedule, "
        f"got {probe['checks']['scheduleValid']}",
    )
    require(
        probe["checks"]["scheduleValidBroken"][0] is False,
        "hasValidSchedule must reject a broken schedule string - proves this check "
        "is not vacuously true",
    )

    window = probe["checks"]["scheduleWindow"]
    require(window["mon_before_cutoff"] is True, f"Monday 03:00 UTC must be scheduled: {window}")
    require(
        window["mon_just_before_cutoff"] is True,
        f"Monday 05:59 UTC must still be scheduled: {window}",
    )
    require(
        window["mon_after_cutoff"] is False,
        f"Monday 07:00 UTC must be past the 6am cutoff: {window}",
    )
    require(window["sun_before"] is False, f"Sunday must not be scheduled: {window}")
    require(window["tue_after"] is False, f"Tuesday must not be scheduled: {window}")
    require(
        window["next_mon_before_cutoff"] is True,
        f"the following Monday must be scheduled again - proves weekly recurrence, "
        f"not a one-shot window: {window}",
    )

    unrelated_window = probe["checks"]["unrelatedScheduleWindow"]
    require(
        all(unrelated_window.values()),
        f"an unrelated package with no schedule field must be eligible at any time, "
        f"got {unrelated_window}",
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

    rules = load_automerge_rules()

    found: dict[str, Any] = {}

    def _find_rule() -> None:
        idx, rule = find_rsshub_rule(rules)
        found["idx"] = idx
        found["rule"] = rule
        test_rule_shape(idx, rule, rules)

    run("rsshub rule shape: digest-only, no bare automerge, single schedule entry", _find_rule)
    run("rsshub helmrelease still pins latest@sha256 (digest-only scoping still applies)", test_rsshub_pinned_to_latest_digest)

    probe_holder: dict[str, Any] = {}

    def _probe() -> None:
        probe_holder["data"] = run_renovate_probe(rules)
        test_renovate_behavior(probe_holder["data"])

    run(
        "renovate applyPackageRules/hasValidSchedule/isScheduledNow behavior",
        _probe,
    )

    if probe_holder.get("data") and not failures:
        summary = {
            "renovateVersion": probe_holder["data"].get("renovateVersion"),
            "rsshubDigest": probe_holder["data"]["checks"]["rsshubDigest"].get("automerge"),
            "rsshubDigestSchedule": probe_holder["data"]["checks"]["rsshubDigest"].get("schedule"),
            "scheduleWindow": probe_holder["data"]["checks"]["scheduleWindow"],
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
