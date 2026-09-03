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
  2. Parses every dual-engine (kopiur + VolSync) claim's live substitute maps
     into structured cron hours, converts those hours to UTC under both DST
     seasons for the pre-fix model (kopiur=UTC, VolSync=local) and the
     post-fix model (both=local), and asserts:
       - pre-fix EST collides on every dual-engine ceph claim (the bug)
       - post-fix collides on zero claims in either season for ceph and r2
  3. Separately asserts the timezone pin over the WHOLE kopiur fleet, not just
     the dual-engine intersection. Stage 5 has retired VolSync from eight claims
     (2026-09-01 and 2026-09-02), which drops them out of that intersection -
     and a claim with one engine left has nothing to collide with, so losing its
     timezone pin would be invisible to every assertion in (2). That gap widens
     with every retirement, which is why (3) exists separately.

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

# Floor on the dual-engine population, so a discovery bug that finds nothing
# fails loudly instead of vacuously passing every collision assertion.
#
# Was 29 (30 claims, all dual-engine). Stage 5 has retired VolSync from eight of
# them in two waves: 2026-09-01 - repo-wiki, recyclarr-config, sabnzbd-config,
# seerr; 2026-09-02 - prowlarr-config, ntfy, autobrr, obsidian-livesync. That
# leaves 22. Lower this ONLY alongside a real retirement; the authoritative
# retired set is RETIRED_CLAIMS in kopiur-stage3-test.py. Note the timezone
# contract itself is checked over ALL kopiur claims (see kopiur_claims), not
# just these.
MIN_DUAL_ENGINE_CLAIMS = 22

# The whole kopiur fleet, dual-engine or retired. Unlike the floor above this
# does NOT drop when a volume retires - retiring VolSync does not remove the
# claim from kopiur, and lowering this would be the bug, not the fix.
MIN_KOPIUR_CLAIMS = 30

DEFAULT_TZ = "America/New_York"
DEFAULT_KOPIUR_CEPH = "H 1-23/4 * * *"
DEFAULT_KOPIUR_R2 = "H 4 * * *"
DEFAULT_VOLSYNC_CEPH = "0 */4 * * *"
DEFAULT_VOLSYNC_R2 = "0 2 * * *"

# America/New_York offsets from UTC. Positive = local is behind UTC, so
# utc_hour = (local_hour + offset) % 24.
EDT_OFFSET = 4  # summer
EST_OFFSET = 5  # winter after 2026-11-01


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


def cron_hours(expr: str) -> set[int]:
    """Parse the hour field of a 5-field cron, including kopiur's bare-H form.

    `H` is Jenkins-style hashing of the minute only - the hour field is still
    a concrete hour or range/step, so `H 1-23/4 * * *` expands to the same
    hour set as `0 1-23/4 * * *`.
    """
    parts = expr.split()
    require(len(parts) == 5, f"expected 5-field cron, got {expr!r}")
    hour = parts[1]
    # Strip a leading bare H used as the minute placeholder on the hour token
    # is never valid; H only appears in the minute field (parts[0]).
    out: set[int] = set()
    if hour == "*":
        return set(range(24))
    for piece in hour.split(","):
        step = 1
        body = piece
        if "/" in piece:
            body, step_s = piece.split("/", 1)
            step = int(step_s)
        if body == "*":
            start, end = 0, 23
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(body)
        out.update(range(start, end + 1, step))
    return out


def to_utc(local_hours: set[int], offset: int) -> set[int]:
    return {(h + offset) % 24 for h in local_hours}


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


def dual_engine_claims() -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
    kopiur_raw = kopiur_claims()
    volsync_raw = _engine_schedules(
        VOLSYNC_COMPONENT_SUFFIX,
        VOLSYNC_BACKUP_PATH,
        "VOLSYNC_CLAIM",
        {
            "ceph": ("VOLSYNC_SCHEDULE_CEPH", DEFAULT_VOLSYNC_CEPH),
            "r2": ("VOLSYNC_SCHEDULE_R2", DEFAULT_VOLSYNC_R2),
        },
    )
    dual = []
    for key in sorted(set(kopiur_raw) & set(volsync_raw)):
        dual.append((key[0], key[1], kopiur_raw[key], volsync_raw[key]))
    return dual


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

    The collision tests below iterate the dual-engine intersection, because a
    collision needs two engines. That is the right population for them and the
    wrong one for this: after Stage 5 retired VolSync from four claims, those
    four leave the intersection entirely, and dropping
    KOPIUR_SCHEDULE_TIMEZONE on one of them would sail through every other
    assertion in this file while the operator silently went back to evaluating
    its cron as literal UTC - a 4h/5h shift with nothing left to collide with
    to reveal it.
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


def test_pre_fix_est_ceph_collision_is_total() -> None:
    """Reproduce the bug: with kopiur on UTC and VolSync on local, EST collides everywhere."""
    dual = dual_engine_claims()
    require(
        len(dual) >= MIN_DUAL_ENGINE_CLAIMS,
        f"expected >={MIN_DUAL_ENGINE_CLAIMS} dual-engine claims, got {len(dual)}",
    )
    collisions: list[str] = []
    for ns, claim, ksub, vsub in dual:
        k_hours = cron_hours(ksub["ceph"])  # literal UTC under the pre-fix model
        v_local = cron_hours(vsub["ceph"])
        v_utc_est = to_utc(v_local, EST_OFFSET)
        if k_hours & v_utc_est:
            collisions.append(f"{ns}/{claim}")
    require(
        len(collisions) == len(dual),
        f"pre-fix EST should collide on EVERY dual-engine ceph claim "
        f"({len(dual)}), collided on {len(collisions)}: missing="
        f"{sorted({f'{n}/{c}' for n, c, _, _ in dual} - set(collisions))}",
    )
    # And the same model must stay clean under EDT (the summer coincidence).
    edt_collisions = []
    for ns, claim, ksub, vsub in dual:
        k_hours = cron_hours(ksub["ceph"])
        v_utc_edt = to_utc(cron_hours(vsub["ceph"]), EDT_OFFSET)
        if k_hours & v_utc_edt:
            edt_collisions.append(f"{ns}/{claim}")
    require(
        not edt_collisions,
        f"pre-fix EDT must stay clean (the 4-mod-4 coincidence); collided: {edt_collisions}",
    )


def test_post_fix_no_collision_either_season() -> None:
    """After aligning zones, no dual-engine claim collides on ceph or r2 in either season."""
    dual = dual_engine_claims()
    require(
        len(dual) >= MIN_DUAL_ENGINE_CLAIMS,
        f"expected >={MIN_DUAL_ENGINE_CLAIMS} dual-engine claims, got {len(dual)}",
    )

    # Every onboarded claim must evaluate cron in America/New_York (default or set).
    bad_tz = [
        f"{ns}/{claim}={ksub['tz']}"
        for ns, claim, ksub, _ in dual
        if ksub["tz"] != DEFAULT_TZ
    ]
    require(
        not bad_tz,
        f"every dual-engine claim must keep KOPIUR_SCHEDULE_TIMEZONE at "
        f"{DEFAULT_TZ!r} (or omit it for the component default); drifted: {bad_tz}",
    )

    report_lines = [
        "claim | dest | VS local | K local | VS UTC EDT | K UTC EDT | VS UTC EST | K UTC EST | collide?",
    ]
    failures: list[str] = []
    for ns, claim, ksub, vsub in dual:
        for dest in ("ceph", "r2"):
            k_local = cron_hours(ksub[dest])
            v_local = cron_hours(vsub[dest])
            # Post-fix: both engines evaluate the written hour as America/New_York.
            k_edt, v_edt = to_utc(k_local, EDT_OFFSET), to_utc(v_local, EDT_OFFSET)
            k_est, v_est = to_utc(k_local, EST_OFFSET), to_utc(v_local, EST_OFFSET)
            hit_edt = sorted(k_edt & v_edt)
            hit_est = sorted(k_est & v_est)
            collide = bool(hit_edt or hit_est)
            report_lines.append(
                f"{ns}/{claim} | {dest} | {sorted(v_local)} | {sorted(k_local)} | "
                f"{sorted(v_edt)} | {sorted(k_edt)} | {sorted(v_est)} | {sorted(k_est)} | "
                f"{'YES edt=' + str(hit_edt) + ' est=' + str(hit_est) if collide else 'no'}"
            )
            if collide:
                failures.append(
                    f"{ns}/{claim} {dest}: EDT hit {hit_edt}, EST hit {hit_est}"
                )

    # Emit the full hour table so a reviewer (and the evidence artifact) can see
    # every claim x destination x season without re-deriving it.
    print("--- post-fix hour table (local = America/New_York) ---")
    for line in report_lines:
        print(line)
    print("--- end hour table ---")

    require(
        not failures,
        "post-fix same-hour collisions (engines must stay staggered in both seasons): "
        + "; ".join(failures),
    )


def test_ceph_stagger_is_exactly_one_hour() -> None:
    """The structural ODD/EVEN ceph offset must remain a constant 1h in both seasons."""
    dual = dual_engine_claims()
    for ns, claim, ksub, vsub in dual:
        k_local = sorted(cron_hours(ksub["ceph"]))
        v_local = sorted(cron_hours(vsub["ceph"]))
        require(
            len(k_local) == 6 and len(v_local) == 6,
            f"{ns}/{claim}: expected 6 ceph hours each, got k={k_local} v={v_local}",
        )
        # Local-hour difference must be 1 for every paired slot (kopiur is the
        # odd set 1-23/4, VolSync is the even set */4 - or a per-app minute
        # variant of the same hour set).
        for kh in k_local:
            # The matching VolSync hour is (kh - 1) mod 24 for the standard stagger.
            expect_v = (kh - 1) % 24
            require(
                expect_v in v_local,
                f"{ns}/{claim}: kopiur local hour {kh} has no VolSync partner at "
                f"{expect_v} (got VolSync hours {v_local}) - the 1-hour stagger broke",
            )
        for offset, season in ((EDT_OFFSET, "EDT"), (EST_OFFSET, "EST")):
            k_utc = sorted(to_utc(set(k_local), offset))
            v_utc = sorted(to_utc(set(v_local), offset))
            # Lengths already equal (both 6); plain zip is enough on py3.9 CI.
            require(
                len(k_utc) == len(v_utc),
                f"{ns}/{claim} {season}: utc hour set length mismatch "
                f"k={k_utc} v={v_utc}",
            )
            for kh, vh in zip(k_utc, v_utc):
                # After a uniform shift both sets move together, so the sorted
                # pairing still differs by 1 mod 24.
                delta = (kh - vh) % 24
                require(
                    delta == 1,
                    f"{ns}/{claim} {season}: expected 1h stagger, got k_utc={k_utc} "
                    f"v_utc={v_utc} (pair {vh}->{kh} delta={delta})",
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
    run("pre_fix_est_ceph_collision_is_total", test_pre_fix_est_ceph_collision_is_total)
    run("post_fix_no_collision_either_season", test_post_fix_no_collision_either_season)
    run("ceph_stagger_is_exactly_one_hour", test_ceph_stagger_is_exactly_one_hour)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
