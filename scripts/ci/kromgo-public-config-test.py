#!/usr/bin/env python3
# ci: gate
"""kromgo is public: it may serve only the allow-listed badges, and no badge may leak labels.

Prevents: node names, pod IPs, alert or app labels published on the internet. kromgo sits on
envoy-external with no auth, and since 0.12 `/badges/{id}?format=json` returns every label of
the result sample, so one un-aggregated query publishes whatever its series carry.

The test does not grep for "sum(". It parses every PromQL query with a small conservative
label analysis (selectors carry unknown labels; aggregations keep only their `by` labels;
label_replace adds its target; binary operators union both sides) and requires the result
labels of each badge to be a subset of ALLOWED[id]. It also pins:

  1. The badge ids are exactly ALLOWED (a new public metric is a reviewed diff here).
  2. No graphs (range data), no gallery, instant badges only.
  3. valueExpr reads only allowed labels.
  4. The config contains no dollar-brace sequence (Flux would substitute it and an unknown
     variable fails the whole Kustomization).
  5. The HelmRelease route forwards only /badges/ on envoy-external, mounts the config where
     kromgo 0.16 reads it, and serves no /metrics listener.
  6. promtool parses every query (when promtool is on PATH; the gate job installs it).

A self-test runs the analysis on known-bad queries first, so the checker can fail.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/monitoring/kromgo/app"
CONFIG = APP / "resources/config.yaml"
HELMRELEASE = APP / "helmrelease.yaml"

# badge id -> labels its result may carry. Empty set: a bare number.
ALLOWED: dict[str, set[str]] = {
    "talos_version": {"version_id"},
    "kubernetes_version": {"git_version"},
    "flux_version": {"revision"},
    "cluster_node_count": set(),
    "cluster_node_ready_count": set(),
    "cluster_pod_count": set(),
    "cluster_cpu_usage": set(),
    "cluster_memory_usage": set(),
    "cluster_age_days": set(),
    "cluster_uptime_days": set(),
    "cluster_alert_count": set(),
    "ai_tokens_7d": set(),
    "ai_requests_7d": set(),
    "ai_local_share_7d": set(),
    "ai_cache_hit_rate_7d": set(),
    "gpu_vram_used_gib": set(),
    "gpu_vram_total_gib": set(),
    "gpu_temp_celsius": set(),
    "gpu_power_watts": set(),
    "gpu_fan_rpm": set(),
    "llm_decode_tps_24h": set(),
    "llm_model": {"model"},
    "llm_requests_processing": set(),
}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


# ---------------------------------------------------------------------------
# Conservative PromQL label analysis
# ---------------------------------------------------------------------------


class Any_:
    """Unknown label set: a raw selector or a label-preserving aggregation."""

    def __repr__(self) -> str:
        return "ANY"


ANY = Any_()
Labels = "set[str] | Any_"

AGGREGATIONS = {"sum", "min", "max", "avg", "group", "stddev", "stdvar", "count", "quantile",
                "count_values", "topk", "bottomk", "limitk", "limit_ratio"}
KEEPS_SERIES_LABELS = {"topk", "bottomk", "limitk", "limit_ratio", "count_values"}
SET_OPS = {"and", "or", "unless", "atan2"}
MODIFIERS = {"on", "ignoring", "group_left", "group_right"}

TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`)
  | (?P<braces>\{(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^{}"'])*\})
  | (?P<range>\[[^\]]*\])
  | (?P<num>(?:0x[0-9a-fA-F]+|\d[\w.]*|\.\d[\w.]*)(?:[eE][+-]?\d+)?)
  | (?P<ident>[a-zA-Z_:][a-zA-Z0-9_:]*)
  | (?P<op>==|!=|<=|>=|=~|!~|[-+*/%^<>(),@])
    """,
    re.VERBOSE,
)


def tokenize(q: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(q):
        m = TOKEN.match(q, pos)
        if not m:
            raise ValueError(f"cannot tokenize at {q[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind != "ws":
            out.append((kind, m.group()))
    return out


def union(a: Any, b: Any) -> Any:
    if a is ANY or b is ANY:
        return ANY
    return a | b


class Parser:
    def __init__(self, q: str) -> None:
        self.t = tokenize(q)
        self.i = 0

    def peek(self, k: int = 0) -> tuple[str, str]:
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def label_list(self) -> set[str]:
        self.take("(")
        names: set[str] = set()
        while self.peek()[1] != ")":
            kind, val = self.take()
            if kind == "ident":
                names.add(val)
        self.take(")")
        return names

    def parse(self) -> Any:
        labels = self.expr()
        if self.peek()[0] != "eof":
            raise ValueError(f"trailing input at {self.peek()[1]!r}")
        return labels

    def expr(self) -> Any:
        labels = self.unary()
        while True:
            kind, val = self.peek()
            is_binop = (kind == "op" and val not in {"(", ")", ","}) or (kind == "ident" and val in SET_OPS)
            if not is_binop:
                return labels
            self.take()
            while self.peek()[1] in {"bool"} | MODIFIERS:
                word = self.take()[1]
                if word in MODIFIERS and self.peek()[1] == "(":
                    self.label_list()
            labels = union(labels, self.unary())

    def unary(self) -> Any:
        if self.peek()[1] in {"-", "+"}:
            self.take()
        labels = self.primary()
        while True:
            kind, val = self.peek()
            if kind == "range":
                self.take()
            elif val == "offset":
                self.take()
                if self.peek()[1] in {"-", "+"}:
                    self.take()
                self.take()
            elif val == "@":
                self.take()
                self.take()
                if self.peek()[1] == "(":
                    self.take("(")
                    self.take(")")
            else:
                return labels

    def args(self) -> list[tuple[Any, str | None]]:
        """Parse a call's arguments: (labels, string literal value or None) per argument."""
        self.take("(")
        out: list[tuple[Any, str | None]] = []
        while self.peek()[1] != ")":
            if self.peek()[0] == "str" and self.peek(1)[1] in {",", ")"}:
                out.append((set(), self.take()[1][1:-1]))
            else:
                out.append((self.expr(), None))
            if self.peek()[1] == ",":
                self.take(",")
        self.take(")")
        return out

    def primary(self) -> Any:
        kind, val = self.peek()
        if kind in {"num", "str"}:
            self.take()
            return set()
        if val == "(":
            self.take("(")
            labels = self.expr()
            self.take(")")
            return labels
        if kind == "braces":
            self.take()
            return ANY
        if kind != "ident":
            raise ValueError(f"unexpected token {val!r}")

        if val in AGGREGATIONS:
            self.take()
            grouping: tuple[str, set[str]] | None = None
            if self.peek()[1] in {"by", "without"}:
                grouping = (self.take()[1], self.label_list())
            args = self.args()
            if self.peek()[1] in {"by", "without"}:
                grouping = (self.take()[1], self.label_list())
            if val in KEEPS_SERIES_LABELS:
                return ANY
            if grouping is None:
                return set()
            if grouping[0] == "without":
                return ANY
            return set(grouping[1])

        if self.peek(1)[1] == "(":
            self.take()
            args = self.args()
            if val in {"vector", "time", "scalar", "pi"}:
                return set()
            if val in {"label_replace", "label_join"}:
                base = args[0][0] if args else ANY
                target = args[1][1] if len(args) > 1 else None
                return union(base, {target} if target else ANY)
            result: Any = set()
            for labels, _ in args:
                result = union(result, labels)
            return result

        # A metric name, optionally followed by {matchers}: a raw selector.
        self.take()
        if self.peek()[0] == "braces":
            self.take()
        return ANY


def result_labels(query: str) -> Any:
    return Parser(query).parse()


def labels_ok(query: str, allowed: set[str]) -> tuple[bool, str]:
    try:
        labels = result_labels(query)
    except ValueError as exc:
        return False, f"unparseable ({exc})"
    if labels is ANY:
        return False, "result keeps unknown labels (raw selector, without, topk, ...)"
    extra = labels - allowed
    return not extra, f"labels {sorted(labels)}" + (f", not allowed {sorted(extra)}" if extra else "")


def self_test() -> None:
    bad = {
        "raw selector": ("node_os_info", set()),
        "by a disallowed label": ("max by (instance) (up)", set()),
        "without keeps the rest": ("sum without (pod) (up)", set()),
        "topk keeps series labels": ("topk(1, up)", set()),
        "label_replace adds a label": ('label_replace(max(up), "instance", "$1", "x", "(.*)")', set()),
        "selector beside an aggregate": ("sum(up) + up", set()),
        "or with a raw selector": ("sum(up) or up", set()),
        "label_replace outside the by": ('max by (v) (label_replace(up, "v", "$1", "x", "(.*)")) * on() group_left node_uname_info', {"v"}),
    }
    good = {
        "aggregate": ("round(sum(rate(x_total[5m])))", set()),
        "nested count": ('count(count by (node) (kube_node_status_condition{condition="Ready"}))', set()),
        "or vector": ('count(ALERTS{alertstate="firing"}) or vector(0)', set()),
        "time arithmetic": ("round((time() - min(kube_node_created)) / 86400)", set()),
        "allowed by": ('max by (v) (label_replace(up, "v", "$1", "x", "(.*)"))', {"v"}),
        "power and clamp": ("round(100 * sum(a) / clamp_min(sum(b), 1), 0.1) + 2^3", set()),
    }
    for name, (q, allowed) in bad.items():
        ok, why = labels_ok(q, allowed)
        check(not ok, f"self-test rejects {name}: {q!r} ({why})")
    for name, (q, allowed) in good.items():
        ok, why = labels_ok(q, allowed)
        check(ok, f"self-test accepts {name}: {q!r} ({why})")


# ---------------------------------------------------------------------------
# Repo checks
# ---------------------------------------------------------------------------


def config_checks() -> list[dict[str, Any]]:
    raw = CONFIG.read_text()
    check("${" not in raw, "config.yaml contains no dollar-brace sequence (Flux postBuild substitutes it)")
    cfg = yaml.safe_load(raw)
    check(set(cfg) <= {"gallery", "cache", "defaults", "badges"}, f"top-level keys {sorted(cfg)} (no graphs, no prometheus override)")
    check(not cfg.get("graphs"), "no graphs: range data is never served")
    check((cfg.get("gallery") or {}).get("enabled") is False, "gallery.enabled is false")
    badges = cfg.get("badges") or []
    ids = [b.get("id") for b in badges]
    check(len(ids) == len(set(ids)), "badge ids are unique")
    check(set(ids) == set(ALLOWED),
          f"badge ids match ALLOWED (extra {sorted(set(ids) - set(ALLOWED))}, missing {sorted(set(ALLOWED) - set(ids))})")
    return badges


def badge_checks(badges: list[dict[str, Any]]) -> None:
    for b in badges:
        bid = b.get("id")
        allowed = ALLOWED.get(bid, set())
        check(b.get("type", "instant") == "instant" and "range" not in b, f"{bid}: instant badge")
        ok, why = labels_ok(b.get("query", ""), allowed)
        check(ok, f"{bid}: {why}")
        refs = set(re.findall(r'labels\[\??"([^"]+)"\]', (b.get("valueExpr") or "") + (b.get("colorExpr") or "")))
        check(refs <= allowed, f"{bid}: CEL reads labels {sorted(refs)} within {sorted(allowed)}")


def promtool_checks(badges: list[dict[str, Any]]) -> None:
    promtool = shutil.which("promtool")
    if not promtool:
        check(not os.environ.get("CI"), "promtool on PATH (required in CI; skipped locally)")
        return
    rules = {"groups": [{"name": "kromgo", "rules": [
        {"record": f"kromgo_check_{i}", "expr": b["query"]} for i, b in enumerate(badges)]}]}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(rules, fh)
    res = subprocess.run([promtool, "check", "rules", fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    check(res.returncode == 0, f"promtool parses every query {res.stderr.strip()[:300]}")


def helmrelease_checks() -> None:
    hr = yaml.safe_load(HELMRELEASE.read_text())
    values = hr["spec"]["values"]
    app = values["controllers"]["kromgo"]["containers"]["app"]
    env = app.get("env", {})
    check(str(env.get("KROMGO_METRICS_ENABLED")) == "false", "KROMGO_METRICS_ENABLED is false (no /metrics listener)")
    check(set(values["service"]["app"]["ports"]) == {"http"}, "the Service exposes only the main port")
    mounts = values["persistence"]["config-file"]["globalMounts"]
    check([m["path"] for m in mounts] == ["/config/config.yaml"], "config mounted at /config/config.yaml (kromgo 0.16 default)")
    for name, route in (values.get("route") or {}).items():
        public = any(p.get("name") == "envoy-external" for p in route.get("parentRefs", []))
        if not public:
            continue
        prefixes = [m.get("path", {}).get("value") for r in route.get("rules", []) for m in (r.get("matches") or [{}])]
        check(prefixes == ["/badges/"], f"route {name} on envoy-external forwards only /badges/ (got {prefixes})")


def main() -> int:
    self_test()
    badges = config_checks()
    badge_checks(badges)
    promtool_checks(badges)
    helmrelease_checks()
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
