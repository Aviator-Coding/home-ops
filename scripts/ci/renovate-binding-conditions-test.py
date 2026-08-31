#!/usr/bin/env python3
"""Semantic pin for the 2026-08-22 keep-in-cluster Renovate binding conditions.

The captain's four conditions were written against mogenius/renovate-operator;
what shipped is the official Helm chart + GitHub-App CronJob. Re-measured
2026-08-31:

  1. Metrics/ServiceMonitor   - substituted with kube-state-metrics CronJob alerts
  2. Alert-on-silence         - RenovateRunMissing / NeverSucceeded / CronJobAbsent
  3. Docker Hub hostRules     - formally retired (anonymous lookups measured safe)
  4. Chart-pin automerge off  - closed via matchFileNames exclusion in autoMerge

This test does not grep source text as its evidence. It:

  1. Parses `.renovate/autoMerge.json5` into structured packageRules.
  2. Simulates Renovate's last-match-wins evaluation against concrete dependency
     updates (file path + datasource + update type + package name), including
     the live OCIRepository path for Renovate's own chart.
  3. Renders `kubernetes/apps/base/renovate/app` via kustomize and asserts the
     live monitoring contract plus the intentional absence of hostRules /
     RENOVATE_HOST_RULES on the shipped CronJob design.

Live Docker Hub rate-limit measurements and the next scheduled CronJob run
after merge remain post-merge operational gates; this pins the GitOps contract
that must hold before merge.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
AUTOMERGE = ROOT / ".renovate/autoMerge.json5"
RENOVATE_APP = ROOT / "kubernetes/apps/base/renovate/app"

# Live chart pin path Renovate actually tracks (OCIRepository manager).
RENOVATE_CHART_FILE = "kubernetes/apps/base/renovate/app/ocirepository.yaml"
RENOVATE_CHART_PACKAGE = "ghcr.io/renovatebot/charts/renovate"

KOPIUR_PACKAGE_NAMES = frozenset(
    {
        "ghcr.io/home-operations/charts/kopiur",
        "ghcr.io/home-operations/kopiur-controller",
        "ghcr.io/home-operations/kopiur-webhook",
        "ghcr.io/home-operations/kopiur-mover",
        "home-operations/kopiur",
    }
)

REQUIRED_SILENCE_ALERTS = frozenset(
    {
        "RenovateJobFailed",
        "RenovateRunMissing",
        "RenovateNeverSucceeded",
        "RenovateCronJobAbsent",
    }
)


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_comments(text: str) -> Any:
    """Minimal JSON5 subset parser matching kopiur-stage0-test.py's contract."""

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
        raise Failure(f"failed to parse autoMerge.json5 subset as JSON: {e}") from e


@dataclass(frozen=True)
class DepUpdate:
    """A concrete dependency update Renovate would evaluate packageRules against."""

    package_name: str
    datasource: str
    update_type: str
    file_name: str


def rule_matches(rule: dict[str, Any], dep: DepUpdate) -> bool:
    """Approximate Renovate packageRules matchers used in autoMerge.json5.

    Only the matchers this repo's autoMerge preset actually uses are modelled.
    Unknown matchers fail closed (no match) so a future rule shape cannot
    silently pass this pin without being represented.
    """
    known = {
        "description",
        "matchDatasources",
        "matchUpdateTypes",
        "matchPackageNames",
        "matchFileNames",
        "automerge",
        "automergeType",
        "minimumReleaseAge",
        "ignoreTests",
    }
    unknown = set(rule) - known
    require(
        not unknown,
        f"autoMerge rule uses unmodelled matcher keys {sorted(unknown)}; "
        "extend rule_matches() before relying on this pin",
    )

    datasources = rule.get("matchDatasources")
    if datasources is not None and dep.datasource not in datasources:
        return False

    update_types = rule.get("matchUpdateTypes")
    if update_types is not None and dep.update_type not in update_types:
        return False

    names = rule.get("matchPackageNames")
    if names is not None and dep.package_name not in names:
        return False

    files = rule.get("matchFileNames")
    if files is not None:
        if not any(fnmatch.fnmatch(dep.file_name, pat) for pat in files):
            return False

    return True


def winning_automerge(rules: list[dict[str, Any]], dep: DepUpdate) -> tuple[bool | None, int]:
    """Last matching rule that sets automerge wins (Renovate packageRules order)."""
    winner: bool | None = None
    idx = -1
    for i, rule in enumerate(rules):
        if "automerge" not in rule:
            continue
        if rule_matches(rule, dep):
            winner = bool(rule["automerge"])
            idx = i
    return winner, idx


def _resolve_kustomize() -> list[str]:
    """Return a kustomize-build argv without requiring mise trust in the sandbox."""
    from shutil import which

    kustomize = which("kustomize")
    if kustomize and Path(kustomize).is_file():
        # mise shims re-exec mise and fail when this worktree's .mise.toml is
        # untrusted; prefer a real binary if the shim is just a mise pointer.
        try:
            target = Path(kustomize).resolve()
        except OSError:
            target = Path(kustomize)
        if target.name != "mise":
            return [kustomize, "build"]

    installs = Path.home() / ".local/share/mise/installs/kustomize"
    if installs.is_dir():
        # aqua/mise layouts vary: <ver>/bin/kustomize or <ver>/kustomize.
        candidates = sorted(
            [
                *installs.glob("*/bin/kustomize"),
                *installs.glob("*/kustomize"),
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for cand in candidates:
            if cand.is_file() and os.access(cand, os.X_OK):
                return [str(cand), "build"]

    kubectl = which("kubectl")
    if kubectl:
        return [kubectl, "kustomize"]
    raise Failure("neither kustomize nor kubectl is available")


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    cmd = _resolve_kustomize() + [str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    require(
        proc.returncode == 0,
        f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}",
    )
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    require(docs, f"kustomize build {path} produced no documents")
    return docs


def test_automerge_config_parses() -> list[dict[str, Any]]:
    cfg = parse_json5_comments(AUTOMERGE.read_text())
    rules = cfg.get("packageRules") or []
    require(isinstance(rules, list) and rules, "autoMerge.json5 has no packageRules")
    # Exactly three blanket enable rules, then exclusions. Do not allow a fourth
    # automerge:true blanket to slip in (that would widen automerge).
    enable_rules = [r for r in rules if r.get("automerge") is True]
    require(
        len(enable_rules) == 3,
        f"expected exactly 3 automerge:true blanket rules (digest/patch/minor), "
        f"got {len(enable_rules)} - do not widen automerge",
    )
    disable_rules = [r for r in rules if r.get("automerge") is False]
    require(
        len(disable_rules) >= 2,
        f"expected at least renovate + kopiur automerge:false exclusions, got {len(disable_rules)}",
    )
    return rules


def test_renovate_self_update_blocked(rules: list[dict[str, Any]]) -> None:
    """Condition 4: Renovate's own chart path must lose automerge after blankets."""
    cases = [
        DepUpdate(RENOVATE_CHART_PACKAGE, "docker", "digest", RENOVATE_CHART_FILE),
        DepUpdate(RENOVATE_CHART_PACKAGE, "docker", "patch", RENOVATE_CHART_FILE),
        DepUpdate(RENOVATE_CHART_PACKAGE, "docker", "minor", RENOVATE_CHART_FILE),
        DepUpdate(RENOVATE_CHART_PACKAGE, "helm", "patch", RENOVATE_CHART_FILE),
        DepUpdate(RENOVATE_CHART_PACKAGE, "helm", "minor", RENOVATE_CHART_FILE),
        # Path match must also cover a hypothetical helm-values image.tag under
        # the same tree (the exclusion's stated secondary purpose).
        DepUpdate(
            "ghcr.io/renovatebot/renovate",
            "docker",
            "minor",
            "kubernetes/apps/base/renovate/app/helmrelease.yaml",
        ),
    ]
    for dep in cases:
        winner, idx = winning_automerge(rules, dep)
        require(
            winner is False,
            f"{dep}: final automerge must be false (got {winner} from rule {idx})",
        )
        require(
            idx > 0,
            f"{dep}: exclusion must override a prior blanket rule (won at index {idx})",
        )


def test_unrelated_updates_still_automerge(rules: list[dict[str, Any]]) -> None:
    """Exclusion must not disable automerge fleet-wide (narrow only, never widen)."""
    cases = [
        DepUpdate(
            "ghcr.io/some-org/unrelated",
            "docker",
            "digest",
            "kubernetes/apps/base/media/plex/app/helmrelease.yaml",
        ),
        DepUpdate(
            "ghcr.io/some-org/unrelated",
            "docker",
            "patch",
            "kubernetes/apps/base/media/plex/app/helmrelease.yaml",
        ),
        DepUpdate(
            "ghcr.io/some-org/unrelated",
            "helm",
            "minor",
            "kubernetes/apps/base/media/plex/app/helmrelease.yaml",
        ),
    ]
    for dep in cases:
        winner, idx = winning_automerge(rules, dep)
        require(
            winner is True,
            f"{dep}: unrelated update must still automerge (got {winner} from rule {idx})",
        )


def test_kopiur_exclusion_still_last_and_wins(rules: list[dict[str, Any]]) -> None:
    """The renovate exclusion must not displace the kopiur last-rule contract."""
    last = rules[-1]
    require(
        set(last.get("matchPackageNames") or []) >= KOPIUR_PACKAGE_NAMES,
        f"last packageRule must remain the kopiur package-name exclusion, got {last}",
    )
    require(last.get("automerge") is False, "last rule must set automerge: false")

    for pkg in sorted(KOPIUR_PACKAGE_NAMES):
        dep = DepUpdate(
            pkg,
            "docker",
            "patch",
            "kubernetes/apps/base/system/kopiur/app/helmrelease.yaml",
        )
        winner, idx = winning_automerge(rules, dep)
        require(
            winner is False and idx == len(rules) - 1,
            f"{pkg}: kopiur exclusion must win as last rule (winner={winner}, idx={idx})",
        )


def test_rendered_renovate_app_contract(docs: list[dict[str, Any]]) -> None:
    """Conditions 1-3 as shipped: silence alerts live; hostRules intentionally absent."""
    oci = [d for d in docs if d.get("kind") == "OCIRepository"]
    require(len(oci) == 1, f"expected 1 OCIRepository, got {len(oci)}")
    url = (oci[0].get("spec") or {}).get("url", "")
    require(
        url.rstrip("/") == "oci://ghcr.io/renovatebot/charts/renovate",
        f"OCIRepository must track Renovate's own chart, got {url!r}",
    )
    ref = (oci[0].get("spec") or {}).get("ref") or {}
    require("tag" in ref, f"OCIRepository must pin an exact tag, got ref={ref!r}")

    hrs = [d for d in docs if d.get("kind") == "HelmRelease"]
    require(len(hrs) == 1, f"expected 1 HelmRelease, got {len(hrs)}")
    values = (hrs[0].get("spec") or {}).get("values") or {}

    # Walk the rendered values tree for host-rule / dockerconfig wiring. Condition
    # 3 is formally retired: the shipped design must not claim credentials it
    # does not have, and must not leave a half-configured hostRules block.
    def walk(obj: Any, path: str, hits: list[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = str(k)
                lower = key.lower()
                if lower in {
                    "hostrules",
                    "host_rules",
                    "dockerconfigjson",
                    "dockercfg",
                } or lower.startswith("renovate_host_rules"):
                    hits.append(f"{path}.{key}={v!r}")
                if isinstance(v, str) and "RENOVATE_HOST_RULES" in v:
                    hits.append(f"{path}.{key} contains RENOVATE_HOST_RULES")
                walk(v, f"{path}.{key}", hits)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", hits)

    host_hits: list[str] = []
    walk(values, "values", host_hits)
    require(
        not host_hits,
        "condition 3 is retired: rendered HelmRelease must not declare hostRules / "
        f"RENOVATE_HOST_RULES / dockerconfig wiring, found {host_hits}",
    )

    secrets = [
        d
        for d in docs
        if d.get("kind") == "Secret"
        and "dockerconfig" in str((d.get("type") or "")).lower()
    ]
    require(
        not secrets,
        f"condition 3 retired: no dockerconfig Secret should ship in renovate app, got {secrets}",
    )

    rules = [d for d in docs if d.get("kind") == "PrometheusRule"]
    require(len(rules) >= 1, "expected PrometheusRule for silence/failure alerts")
    alert_names: set[str] = set()
    for pr in rules:
        for group in ((pr.get("spec") or {}).get("groups") or []):
            for rule in group.get("rules") or []:
                name = rule.get("alert")
                if name:
                    alert_names.add(str(name))
    missing = REQUIRED_SILENCE_ALERTS - alert_names
    require(
        not missing,
        f"conditions 1/2 require silence/failure alerts {sorted(REQUIRED_SILENCE_ALERTS)}; "
        f"missing {sorted(missing)}; have {sorted(alert_names)}",
    )


def main() -> int:
    failed = 0
    passed = 0

    def run(name: str, fn, *args) -> Any:
        nonlocal failed, passed
        try:
            result = fn(*args)
            passed += 1
            print(f"[PASS] {name}")
            return result
        except Failure as e:
            failed += 1
            print(f"[FAIL] {name}: {e}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001 - surface unexpected errors as failures
            failed += 1
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}", file=sys.stderr)
            return None

    rules = run("automerge_config_parses", test_automerge_config_parses)
    if rules is not None:
        run("renovate_self_update_blocked", test_renovate_self_update_blocked, rules)
        run("unrelated_updates_still_automerge", test_unrelated_updates_still_automerge, rules)
        run("kopiur_exclusion_still_last_and_wins", test_kopiur_exclusion_still_last_and_wins, rules)

    docs = run("kustomize_build_renovate_app", kustomize_build, RENOVATE_APP)
    if docs is not None:
        run("rendered_renovate_app_contract", test_rendered_renovate_app_contract, docs)

    print(f"Summary: {passed} passed, {failed} failed")
    if failed:
        print(f"Summary: FAILED ({failed})", file=sys.stderr)
        return 1
    print("Summary: all binding-condition checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
