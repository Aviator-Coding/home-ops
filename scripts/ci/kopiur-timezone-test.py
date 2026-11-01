#!/usr/bin/env python3
"""Semantic regression for the kopiur/VolSync cron timezone alignment.

Background (measured live 2026-08-31, documented in
kubernetes/components/kopiur/Readme.md "Timezone: kopiur vs VolSync"):

  * The cluster-wide k8tz webhook injects TZ=America/New_York into every pod.
  * VolSync's Go scheduler honours process TZ, so a written cron hour is a
    local America/New_York hour whose UTC instant shifts with DST.
  * kopiur's operator resolves its own timezone and defaults to UTC regardless
    of the injected TZ, unless SnapshotSchedule.spec.schedule.timezone is set.

Before the fix, the deliberate 1-hour ceph stagger only survived because
4 (EDT offset) mod 4 (period) == 0. At the 2026-11-01 EST transition the
offset becomes 5 and every dual-engine 4-hourly claim collides.

This test does NOT grep source text. It:

  1. Renders the real kustomize build of components/kopiur/backup under a
     Flux-shaped envsubst (including ${VAR:-default}) and asserts both
     SnapshotSchedule objects carry schedule.timezone with the substitutable
     default America/New_York, and that an explicit KOPIUR_SCHEDULE_TIMEZONE
     override wins.
  2. Asserts the timezone pin over the WHOLE kopiur fleet: kopiur evaluates its
     own cron, so a claim with one engine still drifts to literal UTC if the
     pin is dropped.
  3. Asserts that no claim runs both engines. The cross-engine collision
     assertions (pre-fix EST collides on every dual-engine ceph claim, post-fix
     collides on none, a constant 1h ceph stagger) needed a dual-engine claim to
     mean anything. The last one, `selfhosted/paperless-ngx`, left VolSync on
     2026-11-01 (issue #2202 PR-4), so they were removed with
     MIN_DUAL_ENGINE_CLAIMS. If a claim ever runs both engines again, restore
     them from git history before merging that change.

Live status.nextSchedule.timezone against the running cluster is a separate
post-merge / operator gate this sandbox cannot reach; the rendered CR field
is the GitOps contract that must hold before merge.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
KOPIUR_BACKUP = REPO / "kubernetes" / "components" / "kopiur" / "backup"
KOPIUR_COMPONENT_SUFFIX = "components/kopiur"
KOPIUR_BACKUP_PATH = "kubernetes/components/kopiur/backup"
VOLSYNC_COMPONENT_SUFFIX = "components/volsync"
VOLSYNC_BACKUP_PATH = "kubernetes/components/volsync/backup"

# The whole kopiur fleet, dual-engine or retired.
#
# Retiring a volume NEVER lowers this: retiring VolSync does not remove the
# claim from kopiur, so a drop after a Stage 5 wave is the bug, not the fix.
#
# Removing an APP is the one thing that legitimately does lower it, because the
# claim itself leaves the fleet along with the overlay that declared it. Was 30;
# `downloads/autobrr` was removed on 2026-09-02 (captain decision - the app was
# unused), which is why this is 29. Its kopia snapshots were deliberately KEPT,
# but a retained snapshot is not a kopiur-onboarded claim and nothing in this
# repo declares it any more. Distinguish the two cases before touching this.
MIN_KOPIUR_CLAIMS = 29

DEFAULT_TZ = "America/New_York"
DEFAULT_KOPIUR_CEPH = "H 1-23/4 * * *"
DEFAULT_KOPIUR_R2 = "H 4 * * *"


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def flux_envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped ${VAR} / ${VAR:-default} substitution, including nesting."""

    def lookup(key: str) -> str | None:
        if key in env and env[key] not in (None, ""):
            return env[key]
        return None

    def expand(s: str) -> str:
        out: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] != "$" or i + 1 >= n or s[i + 1] != "{":
                out.append(s[i])
                i += 1
                continue
            depth = 1
            j = i + 2
            while j < n and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            if depth != 0:
                out.append(s[i])
                i += 1
                continue
            body = s[i + 2 : j - 1]
            if ":-" in body:
                key, default = body.split(":-", 1)
            else:
                key, default = body, None
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
                out.append(s[i:j])
                i = j
                continue
            val = lookup(key)
            if val is not None:
                out.append(val)
            elif default is not None:
                out.append(expand(default))
            else:
                out.append(s[i:j])
            i = j
        return "".join(out)

    return expand(text)


_RAW_BUILD: str | None = None


def kustomize_build(path: Path) -> str:
    global _RAW_BUILD
    if _RAW_BUILD is not None and path == KOPIUR_BACKUP:
        return _RAW_BUILD
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(path)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(
            f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    if path == KOPIUR_BACKUP:
        _RAW_BUILD = proc.stdout
    return proc.stdout


def render_kopiur(env: dict[str, str]) -> list[dict[str, Any]]:
    rendered = flux_envsubst(kustomize_build(KOPIUR_BACKUP), env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(
        not unresolved,
        f"unresolved substitution tokens after envsubst: {unresolved}",
    )
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    require(docs, "substituted kopiur backup build produced no documents")
    return docs


def flux_kustomizations() -> list[tuple[dict[str, Any], Path]]:
    out: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        try:
            docs = load_multi(path)
        except yaml.YAMLError:
            continue
        for d in docs:
            if d.get("kind") != "Kustomization":
                continue
            if str(d.get("apiVersion", "")).startswith("kustomize.config.k8s.io"):
                continue
            out.append((d, path))
    return out


def _engine_schedules(
    component_suffix: str,
    backup_path: str,
    claim_key: str,
    schedule_defaults: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Map (ns, claim) -> schedule values for one backup engine.

    schedule_defaults maps dest-name -> (substitute_key, component_default).
    Overlay omission falls through to the component default, matching what
    Flux envsubst does with ${VAR:-default} on the templates.
    """
    found: dict[tuple[str, str], dict[str, str]] = {}
    for d, _path in flux_kustomizations():
        spec = d.get("spec") or {}
        comps = [c for c in (spec.get("components") or []) if isinstance(c, str)]
        via_component = any(c.rstrip("/").endswith(component_suffix) for c in comps)
        via_path = str(spec.get("path") or "").strip("./") == backup_path
        if not (via_component or via_path):
            continue
        sub = {
            str(k): str(v)
            for k, v in (((spec.get("postBuild") or {}).get("substitute")) or {}).items()
        }
        app = sub.get("APP") or d.get("metadata", {}).get("name", "?")
        claim = sub.get(claim_key, app)
        ns = d.get("metadata", {}).get("namespace") or spec.get("targetNamespace") or "?"
        found[(ns, claim)] = {
            name: sub.get(key, default)
            for name, (key, default) in schedule_defaults.items()
        }
    return found


def kopiur_claims() -> dict[tuple[str, str], dict[str, str]]:
    """Every kopiur-onboarded claim's schedule triple, retired-from-VolSync or not.

    The timezone contract belongs to kopiur alone: a claim VolSync has been
    retired from still evaluates its own cron, and still gets it wrong (literal
    UTC) if KOPIUR_SCHEDULE_TIMEZONE is dropped. Iterating only the dual-engine
    intersection would silently stop checking the Stage 5 retired claims, which
    is how a fixed bug quietly comes back on the volumes with one engine left.
    """
    return _engine_schedules(
        KOPIUR_COMPONENT_SUFFIX,
        KOPIUR_BACKUP_PATH,
        "KOPIUR_CLAIM",
        {
            "ceph": ("KOPIUR_SCHEDULE_CEPH", DEFAULT_KOPIUR_CEPH),
            "r2": ("KOPIUR_SCHEDULE_R2", DEFAULT_KOPIUR_R2),
            "tz": ("KOPIUR_SCHEDULE_TIMEZONE", DEFAULT_TZ),
        },
    )


def volsync_claims() -> set[tuple[str, str]]:
    """Every (ns, claim) a Flux Kustomization still wires VolSync onto."""
    return set(
        _engine_schedules(VOLSYNC_COMPONENT_SUFFIX, VOLSYNC_BACKUP_PATH, "VOLSYNC_CLAIM", {})
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rendered_schedules_default_timezone() -> None:
    """Component default must pin America/New_York on both SnapshotSchedules."""
    env = {
        "APP": "tz-probe",
        "KOPIUR_CLAIM": "tz-probe",
        "KOPIUR_PUID": "1000",
        "KOPIUR_PGID": "1000",
        "KOPIUR_SCHEDULE_R2": "H 11 * * *",
        # deliberately omit KOPIUR_SCHEDULE_TIMEZONE so the :-default fires
    }
    docs = render_kopiur(env)
    schedules = {
        s["metadata"]["name"]: s for s in docs if s.get("kind") == "SnapshotSchedule"
    }
    require(
        set(schedules) == {"tz-probe-ceph", "tz-probe-r2"},
        f"expected ceph+r2 SnapshotSchedules, got {sorted(schedules)}",
    )
    for name, s in schedules.items():
        sched = (s.get("spec") or {}).get("schedule") or {}
        require(
            sched.get("timezone") == DEFAULT_TZ,
            f"{name}: schedule.timezone must default to {DEFAULT_TZ!r}, got {sched.get('timezone')!r}",
        )
        require("cron" in sched, f"{name}: schedule.cron must be present")


def test_rendered_schedules_timezone_override() -> None:
    """An explicit KOPIUR_SCHEDULE_TIMEZONE must win over the default."""
    override = "UTC"
    env = {
        "APP": "tz-override",
        "KOPIUR_CLAIM": "tz-override",
        "KOPIUR_PUID": "1000",
        "KOPIUR_PGID": "1000",
        "KOPIUR_SCHEDULE_R2": "H 14 * * *",
        "KOPIUR_SCHEDULE_TIMEZONE": override,
    }
    docs = render_kopiur(env)
    schedules = [s for s in docs if s.get("kind") == "SnapshotSchedule"]
    require(len(schedules) == 2, f"expected 2 schedules, got {len(schedules)}")
    for s in schedules:
        tz = ((s.get("spec") or {}).get("schedule") or {}).get("timezone")
        require(
            tz == override,
            f"{s['metadata']['name']}: timezone override {override!r} did not apply, got {tz!r}",
        )


def test_every_kopiur_claim_pins_the_timezone() -> None:
    """The timezone fix must cover EVERY kopiur claim, retired-from-VolSync or not.

    The removed collision tests iterated the dual-engine intersection, because a
    collision needs two engines. That was the right population for them and the
    wrong one for this: a claim retired from VolSync leaves the intersection
    entirely (all of them have since 2026-11-01), and dropping
    KOPIUR_SCHEDULE_TIMEZONE on one of them would go unnoticed while the
    operator silently went back to evaluating its cron as literal UTC - a 4h/5h
    shift with nothing left to collide with to reveal it.
    """
    claims = kopiur_claims()
    require(
        len(claims) >= MIN_KOPIUR_CLAIMS,
        f"expected >={MIN_KOPIUR_CLAIMS} kopiur claims, got {len(claims)}",
    )
    bad = sorted(
        f"{ns}/{claim}={sched['tz']}"
        for (ns, claim), sched in claims.items()
        if sched["tz"] != DEFAULT_TZ
    )
    require(
        not bad,
        f"every kopiur claim must evaluate cron in {DEFAULT_TZ} (explicitly or by the "
        f"component default) - kopiur ignores the k8tz-injected process TZ and falls "
        f"back to literal UTC; got {bad}",
    )


def test_no_claim_runs_both_engines() -> None:
    """The dual-engine population is empty, so no cross-engine collision can exist.

    The collision assertions this file used to carry (pre-fix EST collides on
    every dual-engine ceph claim, post-fix on none, a constant 1h ceph stagger)
    were removed when the last dual-engine claim, `selfhosted/paperless-ngx`,
    left VolSync on 2026-11-01 (issue #2202 PR-4). This pins that premise: a
    claim that runs both engines again would have nothing checking its schedule
    against VolSync's.
    """
    dual = sorted(f"{ns}/{claim}" for ns, claim in set(kopiur_claims()) & volsync_claims())
    require(
        not dual,
        f"claims run both kopiur and VolSync again: {dual}. Restore the cross-engine "
        f"collision assertions (and MIN_DUAL_ENGINE_CLAIMS) from git history first",
    )


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("rendered_schedules_default_timezone", test_rendered_schedules_default_timezone)
    run("rendered_schedules_timezone_override", test_rendered_schedules_timezone_override)
    run("every_kopiur_claim_pins_the_timezone", test_every_kopiur_claim_pins_the_timezone)
    run("no_claim_runs_both_engines", test_no_claim_runs_both_engines)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
