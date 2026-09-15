#!/usr/bin/env python3
"""Semantic regression test for the `ai/hermes` state.db retention contract.

Pins the 2026-09-15 decision recorded in docs/ai-system/hermes-state-db-growth.md.
Measured then: `/opt/data/state.db` was 9.31 GiB (2,440,957 x 4 KiB pages) on a
25Gi claim already 76% full (6.0 GiB free), growing ~175-235 MB/day, against
Hermes' own 1 GiB `doctor` warning threshold.

Two of the three pinned keys are SAFETY invariants, not tuning preferences:

`vacuum_after_prune: false` is the important one. VACUUM is the only thing that
returns freed pages to the filesystem, and it cannot fit here: it rewrites every
page through the WAL, so a ~9.3 GiB database needs comparable free space against
6.0 GiB available. Worse, it would not fail quietly once a month - `last_vacuum`
is absent from `state_meta`, so `since_vacuum is None` holds forever and the
`min_vacuum_interval_days` throttle NEVER engages, while the post-prune freelist
ratio (~33%) clears the 25% `AUTO_VACUUM_MIN_FREELIST_RATIO` gate. So at
upstream's `true` default it would be attempted on every pass that deletes rows,
write until ENOSPC and roll back, on a volume already at 76%. Filling that
volume stops Hermes persisting anything at all. Reverting this key to upstream's
default to "reclaim the wasted space" is the exact mistake this test exists to
refuse.

`auto_prune: true` is pinned because it is load-bearing BY DEFAULT ONLY: both
call sites read `_sess_cfg.get("auto_prune", False)` (gateway/run.py's
`_init_session_db`, cli.py's `_run_state_db_auto_maintenance`), so nothing in
this repo makes it true - only upstream's DEFAULT_CONFIG does. An upstream flip
would silently disable pruning with no signal here.

`retention_days` is pinned as a SHAPE, not a literal, deliberately. AGENTS.md
records what happens when a gate freezes a past PR's exact value: the next
legitimate change to the same field goes red (falkordb-lan-browser-access-test
had to be rewritten for precisely this). The invariant that actually matters is
"narrower than the upstream default that provably does not fit this claim", so
any value in 1..89 passes and 30 -> 21 -> 14 needs no CI edit. Only reverting to
90 (or removing the key, which resolves to 90 via the deep-merge) fails.

Nothing else in CI looks at this file: `flate` validates the ConfigMap's YAML but
has no opinion on its contents, and the values live in an app config blob rather
than a Kubernetes field, so no schema catches them either.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "kubernetes" / "apps" / "base" / "ai" / "hermes" / "app" / "resources" / "config.yaml"
KUSTOMIZATION = REPO / "kubernetes" / "apps" / "base" / "ai" / "hermes" / "app" / "kustomization.yaml"
EVIDENCE_DOC = REPO / "docs" / "ai-system" / "hermes-state-db-growth.md"

# Upstream's default, from hermes_cli/config_defaults.py `sessions.retention_days`.
# Anything >= this is "no narrower than the default", i.e. the state that was
# measured not to fit.
UPSTREAM_DEFAULT_RETENTION_DAYS = 90


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def sessions_block() -> dict[str, Any]:
    require(CONFIG.is_file(), f"{CONFIG.relative_to(REPO)} is missing")
    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    block = doc.get("sessions")
    require(
        isinstance(block, dict),
        "the `sessions:` block is gone from hermes config.yaml - without it every key "
        "falls back to upstream's DEFAULT_CONFIG (retention_days 90, vacuum_after_prune "
        "true), which is the combination measured not to fit this 25Gi claim",
    )
    return block


def test_auto_prune_is_pinned_on() -> None:
    """`auto_prune` must be explicitly true, not left to upstream's default."""
    block = sessions_block()
    require(
        block.get("auto_prune") is True,
        f"sessions.auto_prune must be explicitly `true`, got {block.get('auto_prune')!r}. "
        "Both call sites read .get('auto_prune', False), so only upstream's DEFAULT_CONFIG "
        "makes it true today - an upstream flip would silently stop all pruning.",
    )


def test_vacuum_after_prune_is_off() -> None:
    """`vacuum_after_prune` must be false: a VACUUM cannot fit and would retry forever."""
    block = sessions_block()
    got = block.get("vacuum_after_prune")
    require(
        got is False,
        f"sessions.vacuum_after_prune must be `false`, got {got!r}. VACUUM rewrites every "
        "page through the WAL and needs ~9.3 GiB against 6.0 GiB free; with `last_vacuum` "
        "absent from state_meta its interval throttle never engages, so it would be retried "
        "on every prune pass, write until ENOSPC and risk a 100%-full volume. See "
        "docs/ai-system/hermes-state-db-growth.md section 5.",
    )


def test_retention_is_narrower_than_the_upstream_default() -> None:
    """SHAPE, not a literal: any window narrower than upstream's 90 days passes."""
    block = sessions_block()
    got = block.get("retention_days")
    require(
        isinstance(got, int) and not isinstance(got, bool),
        f"sessions.retention_days must be an integer number of days, got {got!r}",
    )
    require(
        got > 0,
        f"sessions.retention_days must be positive, got {got!r} - 0 or negative would not "
        "mean 'keep nothing', it would make the prune window nonsensical",
    )
    require(
        got < UPSTREAM_DEFAULT_RETENTION_DAYS,
        f"sessions.retention_days is {got}, which is not narrower than upstream's default "
        f"{UPSTREAM_DEFAULT_RETENTION_DAYS}. The 90-day window was measured to project a "
        "~15.6 GiB state.db steady state on top of ~9.7 GB of other /opt/data content, "
        "against a 25Gi claim. Narrow it (21 and 14 are both fine and need no CI change) "
        "or re-do the arithmetic in docs/ai-system/hermes-state-db-growth.md first.",
    )


def test_config_is_still_the_configmap_source() -> None:
    """The block only reaches the pod if this file is still the hermes-configmap source.

    `copy-config` copies /run/config/config.yaml onto the PVC on every start, and that
    mount comes from the `hermes-configmap` ConfigMap. If the generator stops including
    this file the retention block silently never reaches Hermes while every gate here
    still passes on the file's contents.
    """
    require(KUSTOMIZATION.is_file(), f"{KUSTOMIZATION.relative_to(REPO)} is missing")
    doc = yaml.safe_load(KUSTOMIZATION.read_text(encoding="utf-8")) or {}
    generators = doc.get("configMapGenerator") or []
    hit = [
        g for g in generators
        if g.get("name") == "hermes-configmap"
        and any(str(f).endswith("resources/config.yaml") for f in (g.get("files") or []))
    ]
    require(
        hit,
        "no configMapGenerator named `hermes-configmap` sources resources/config.yaml in "
        f"{KUSTOMIZATION.relative_to(REPO)} - the retention block would never reach the pod",
    )


def test_evidence_document_exists() -> None:
    """Existence only: the values are meaningless without the measurements behind them."""
    require(
        EVIDENCE_DOC.is_file(),
        f"{EVIDENCE_DOC.relative_to(REPO)} is missing - it is the record of why these "
        "three values are what they are, including why VACUUM is off",
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

    run("auto_prune_is_pinned_on", test_auto_prune_is_pinned_on)
    run("vacuum_after_prune_is_off", test_vacuum_after_prune_is_off)
    run("retention_is_narrower_than_the_upstream_default",
        test_retention_is_narrower_than_the_upstream_default)
    run("config_is_still_the_configmap_source", test_config_is_still_the_configmap_source)
    run("evidence_document_exists", test_evidence_document_exists)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
