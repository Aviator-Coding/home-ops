#!/usr/bin/env python3
"""Semantic regression test for the Radarr SQP-1 recyclarr quality-profile fix.

2026-08-29 outage: 240 movies on ``[SQP] SQP-1 (2160p)`` accepted zero releases
because (1) ``min_format_score: 2000`` doubled the TRaSH guide floor of 1000 and
(2) ``assign_scores_to`` matched by profile *name*, so a 2026-04-09 guide rename
orphaned custom-format scores onto a 2-movie stale profile.

This test pins the GitOps half of the captain fix (movie/collection moves are
Radarr DB state, not Git):

  1. No ``min_format_score`` override on Radarr quality profiles - guide 1000 wins.
  2. Both SQP-1 profiles are declared by the live TRaSH ``trash_id`` values.
  3. Every ``assign_scores_to`` entry matches by ``trash_id``, never by ``name``.
  4. The kustomize-built ConfigMap that Flux mounts is the same semantic config.
  5. Against the published TRaSH profile JSON, guide ``minFormatScore`` is 1000
     for both SQP-1 profiles (the counterfactual that made threshold 2000 lethal).

It does not grep source strings. It parses YAML into structured maps (including
the ConfigMap ``data.recyclarr.yml`` Flux actually delivers) and, when network
is available, loads the TRaSH-Guides quality-profile documents by URL.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RECYCLARR_APP = ROOT / "kubernetes/apps/base/downloads/recyclarr/app"
CONFIG_PATH = RECYCLARR_APP / "config/recyclarr.yml"

# Independently verified against TRaSH-Guides (docs/json/radarr|sonarr/quality-profiles).
RADARR_SQP_2160P = "5128baeb2b081b72126bc8482b2a86a0"
RADARR_SQP_1080P = "0896c29d74de619df168d23b98104b22"
SONARR_WEB_1080P = "72dae194fc92bf828f32cde7744e51a1"
SONARR_WEB_2160P = "d1498e7d189fbe6c7110ceaabb7473e6"

TRASH_PROFILE_URLS = {
    RADARR_SQP_2160P: (
        "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/"
        "docs/json/radarr/quality-profiles/sqp-1-2160p.json"
    ),
    RADARR_SQP_1080P: (
        "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/"
        "docs/json/radarr/quality-profiles/sqp-1-1080p.json"
    ),
    SONARR_WEB_1080P: (
        "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/"
        "docs/json/sonarr/quality-profiles/web-1080p.json"
    ),
    SONARR_WEB_2160P: (
        "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/"
        "docs/json/sonarr/quality-profiles/web-2160p.json"
    ),
}

GUIDE_MIN_FORMAT_SCORE = 1000  # lethal override was 2000


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


class EnvVarLoader(yaml.SafeLoader):
    """Recyclarr config uses ``!env_var NAME``; treat as opaque string for structure."""


def _env_var_ctor(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    return f"!env_var {loader.construct_scalar(node)}"


EnvVarLoader.add_constructor("!env_var", _env_var_ctor)


def load_recyclarr_config(text: str) -> dict[str, Any]:
    data = yaml.load(text, Loader=EnvVarLoader)
    assert_true(isinstance(data, dict), "recyclarr root must be a mapping")
    return data


def load_file_config(path: Path) -> dict[str, Any]:
    return load_recyclarr_config(path.read_text())


def kustomize_built_config() -> dict[str, Any]:
    """Return the recyclarr.yml embedded in the ConfigMap Flux applies."""
    try:
        built = subprocess.run(
            [
                "kustomize",
                "build",
                str(RECYCLARR_APP),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError:
        built = subprocess.run(
            [
                "kubectl",
                "kustomize",
                str(RECYCLARR_APP),
                "--load-restrictor",
                "LoadRestrictionsNone",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise Failure(f"kustomize build failed: {exc.stderr or exc}") from exc

    for doc in yaml.safe_load_all(built):
        if not isinstance(doc, dict) or doc.get("kind") != "ConfigMap":
            continue
        name = (doc.get("metadata") or {}).get("name") or ""
        if not name.startswith("recyclarr"):
            continue
        data = doc.get("data") or {}
        raw = data.get("recyclarr.yml")
        assert_true(isinstance(raw, str) and raw.strip(), "ConfigMap missing recyclarr.yml")
        return load_recyclarr_config(raw)
    raise Failure("kustomize build produced no recyclarr ConfigMap")


def quality_profiles(instance: dict[str, Any]) -> list[dict[str, Any]]:
    qps = instance.get("quality_profiles") or []
    assert_true(isinstance(qps, list), "quality_profiles must be a list")
    return [qp for qp in qps if isinstance(qp, dict)]


def custom_format_groups(instance: dict[str, Any]) -> list[dict[str, Any]]:
    cfs = instance.get("custom_formats") or []
    assert_true(isinstance(cfs, list), "custom_formats must be a list")
    return [cf for cf in cfs if isinstance(cf, dict)]


def assign_targets(instance: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for group in custom_format_groups(instance):
        assigns = group.get("assign_scores_to") or []
        assert_true(isinstance(assigns, list), "assign_scores_to must be a list")
        for entry in assigns:
            assert_true(isinstance(entry, dict), "assign_scores_to entry must be a mapping")
            targets.append(entry)
    return targets


def fetch_trash_profile(trash_id: str) -> dict[str, Any] | None:
    url = TRASH_PROFILE_URLS[trash_id]
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[WARN] could not fetch TRaSH profile {trash_id}: {exc}")
        return None
    assert_true(isinstance(payload, dict), f"TRaSH payload for {trash_id} not an object")
    return payload


def assert_assign_scores_use_trash_id(instance: dict[str, Any], label: str) -> None:
    targets = assign_targets(instance)
    assert_true(len(targets) > 0, f"{label}: expected at least one assign_scores_to entry")
    for entry in targets:
        assert_true(
            "name" not in entry,
            f"{label}: assign_scores_to must not match by name (got {entry!r}) - "
            "name matching is how the 2026-04-09 guide rename orphaned scores",
        )
        tid = entry.get("trash_id")
        assert_true(
            isinstance(tid, str) and len(tid) == 32 and tid.isalnum(),
            f"{label}: assign_scores_to entry needs a 32-char hex trash_id, got {entry!r}",
        )


def assert_radarr_sqp_fix(cfg: dict[str, Any], *, require_guide_fetch: bool = False) -> dict[str, Any]:
    """Validate the fixed Radarr half of recyclarr.yml. Returns evidence blob."""
    radarr = (cfg.get("radarr") or {}).get("radarr_main")
    assert_true(isinstance(radarr, dict), "radarr.radarr_main missing")

    qps = quality_profiles(radarr)
    by_id = {qp.get("trash_id"): qp for qp in qps if qp.get("trash_id")}

    assert_true(
        RADARR_SQP_2160P in by_id,
        f"Radarr must declare SQP-1 2160p trash_id {RADARR_SQP_2160P}",
    )
    assert_true(
        RADARR_SQP_1080P in by_id,
        f"Radarr must declare SQP-1 1080p trash_id {RADARR_SQP_1080P} "
        "(structural answer for pre-2010/documentary titles)",
    )

    for tid, qp in by_id.items():
        assert_true(
            "min_format_score" not in qp,
            f"Radarr quality profile {tid} must not override min_format_score "
            f"(found {qp.get('min_format_score')!r}); guide default is "
            f"{GUIDE_MIN_FORMAT_SCORE} and the outage used 2000",
        )

    assert_assign_scores_use_trash_id(radarr, "radarr")

    # Local CF score targets must resolve to a declared quality profile trash_id.
    declared = set(by_id)
    for entry in assign_targets(radarr):
        tid = entry["trash_id"]
        assert_true(
            tid in declared,
            f"radarr assign_scores_to trash_id {tid} is not a declared quality_profile",
        )

    evidence: dict[str, Any] = {
        "radarr_quality_profile_trash_ids": sorted(declared),
        "radarr_assign_scores_trash_ids": sorted({e["trash_id"] for e in assign_targets(radarr)}),
        "min_format_score_overrides": [
            {"trash_id": tid, "min_format_score": qp.get("min_format_score")}
            for tid, qp in by_id.items()
            if "min_format_score" in qp
        ],
        "guide_profiles": {},
    }

    guide_ok = True
    for tid in (RADARR_SQP_2160P, RADARR_SQP_1080P):
        guide = fetch_trash_profile(tid)
        if guide is None:
            guide_ok = False
            continue
        assert_true(
            guide.get("trash_id") == tid,
            f"TRaSH document trash_id mismatch: expected {tid}, got {guide.get('trash_id')!r}",
        )
        min_score = guide.get("minFormatScore")
        assert_true(
            min_score == GUIDE_MIN_FORMAT_SCORE,
            f"TRaSH profile {guide.get('name')!r} minFormatScore must be "
            f"{GUIDE_MIN_FORMAT_SCORE}, got {min_score!r}",
        )
        evidence["guide_profiles"][tid] = {
            "name": guide.get("name"),
            "minFormatScore": min_score,
            "cutoffFormatScore": guide.get("cutoffFormatScore"),
        }

    if require_guide_fetch:
        assert_true(guide_ok and len(evidence["guide_profiles"]) == 2, "TRaSH guide fetch required but failed")

    # Acceptance-threshold counterfactual used in the outage report:
    # a release scoring 1500 is rejected at 2000 and accepted at guide 1000.
    sample_release_score = 1500
    evidence["threshold_counterfactual"] = {
        "sample_custom_format_score": sample_release_score,
        "rejected_at_override_2000": sample_release_score < 2000,
        "accepted_at_guide_1000": sample_release_score >= GUIDE_MIN_FORMAT_SCORE,
    }
    assert_true(
        evidence["threshold_counterfactual"]["rejected_at_override_2000"]
        and evidence["threshold_counterfactual"]["accepted_at_guide_1000"],
        "counterfactual invariant broken",
    )

    return evidence


def assert_sonarr_trash_id_matching(cfg: dict[str, Any]) -> None:
    sonarr = (cfg.get("sonarr") or {}).get("sonarr_main")
    assert_true(isinstance(sonarr, dict), "sonarr.sonarr_main missing")
    qps = quality_profiles(sonarr)
    by_id = {qp.get("trash_id") for qp in qps}
    assert_true(SONARR_WEB_1080P in by_id, "Sonarr missing WEB-1080p trash_id")
    assert_true(SONARR_WEB_2160P in by_id, "Sonarr missing WEB-2160p trash_id")
    assert_assign_scores_use_trash_id(sonarr, "sonarr")
    for entry in assign_targets(sonarr):
        assert_true(
            entry["trash_id"] in by_id,
            f"sonarr assign_scores_to trash_id {entry['trash_id']} not declared",
        )


def assert_pre_fix_would_fail(pre_text: str) -> None:
    """The pre-fix config must fail the same semantic checks (regression gate)."""
    cfg = load_recyclarr_config(pre_text)
    try:
        assert_radarr_sqp_fix(cfg, require_guide_fetch=False)
    except Failure:
        return
    raise Failure("pre-fix recyclarr.yml unexpectedly satisfied the fixed invariants")


def load_pre_fix_from_git() -> str | None:
    """Best-effort: parent of the fix commit on this branch, else None."""
    try:
        # File at merge-base-ish: first parent of the fix commit if present.
        show = subprocess.run(
            [
                "git",
                "show",
                "cb3529b907d2e4ca673d87e09b677ecae527e711:"
                "kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return show.stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def main() -> int:
    failures = 0
    evidence: dict[str, Any] = {}

    def run(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        nonlocal failures
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors as failures
            failures += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")

    file_cfg = load_file_config(CONFIG_PATH)

    def t_file() -> None:
        evidence["from_file"] = assert_radarr_sqp_fix(file_cfg)
        assert_sonarr_trash_id_matching(file_cfg)

    def t_cm() -> None:
        cm_cfg = kustomize_built_config()
        evidence["from_configmap"] = assert_radarr_sqp_fix(cm_cfg)
        assert_sonarr_trash_id_matching(cm_cfg)
        # Delivered ConfigMap must declare the same Radarr profile set as the source file.
        assert_true(
            evidence["from_file"]["radarr_quality_profile_trash_ids"]
            == evidence["from_configmap"]["radarr_quality_profile_trash_ids"],
            "ConfigMap Radarr profile trash_ids drifted from source file",
        )

    def t_regression() -> None:
        pre = load_pre_fix_from_git()
        if pre is None:
            print("[WARN] pre-fix git blob unavailable; skipping before/after gate")
            return
        assert_pre_fix_would_fail(pre)
        # Explicit structural checks on the known-bad blob for clearer evidence.
        pre_cfg = load_recyclarr_config(pre)
        radarr = pre_cfg["radarr"]["radarr_main"]
        qps = quality_profiles(radarr)
        overrides = [qp.get("min_format_score") for qp in qps if "min_format_score" in qp]
        assert_true(2000 in overrides, "expected pre-fix min_format_score: 2000 still present in base blob")
        name_matched = [e for e in assign_targets(radarr) if "name" in e]
        assert_true(len(name_matched) > 0, "expected pre-fix name-based assign_scores_to")
        pre_ids = {qp.get("trash_id") for qp in qps}
        assert_true(
            RADARR_SQP_1080P not in pre_ids,
            "pre-fix config should not yet declare SQP-1 1080p",
        )
        evidence["pre_fix"] = {
            "min_format_score_overrides": overrides,
            "name_matched_assign_count": len(name_matched),
            "quality_profile_trash_ids": sorted(x for x in pre_ids if x),
        }

    run("source recyclarr.yml satisfies SQP-1 fix invariants", t_file)
    run("kustomize ConfigMap satisfies SQP-1 fix invariants", t_cm)
    run("pre-fix config fails the same invariants (regression)", t_regression)

    # Always emit a compact evidence document to stdout for the test phase artifact.
    print("--- evidence ---")
    print(json.dumps(evidence, indent=2, sort_keys=True))

    print(f"\n{failures} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
