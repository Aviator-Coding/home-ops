# scripts/ci gates

Each `*-test.py` is a standalone script. `main()` calls its tests, prints
`[PASS]`/`[FAIL]`, and exits from `if __name__`. pytest does not discover them.

```bash
python3 scripts/ci/<name>-test.py
```

CI globs `scripts/ci/*-test.py`. A new file needs no workflow edit. The
per-file docstring is the inventory. `scripts/ci/README.md` points here.

Setup when a test shells out or imports litellm: the `.mise.toml` venv, then
`uv pip install python-hcl2 "litellm[proxy]"` at the version `validate.yaml`
pins, and `PATH` with `mise bin-paths` plus the venv `bin` first.

## Before you change a value

`grep -rn '<app-or-field>' scripts/ci/`. Three shapes:

1. **Hardcoded path.** Retiring the app fails `path.is_file()` on a missing
   file. `igpu-xe-allowids-test.py` maps GPU consumers by path.
2. **Hardcoded tag.** A Renovate bump goes red. Assert repository, tag present,
   digest present, and read the cross-check tag from the manifest
   (`grafana-mcp-deploy-test.py`, `samba-shared-xml-test.py`).
3. **Frozen literal that pins no invariant.** Assert the relationship
   (`falkordb-lan-browser-access-test.py`: `--maxmemory` exists, sits under the
   cgroup limit, no eviction policy). `request == limit` is allowed where there
   is no headroom (`memory-request-declared-test.py`).

Leave `talos-renovate-pin-test.py::test_no_live_version_bump` as it is. It
freezes `.github/docker/talosctl-busybox/Dockerfile` and
`tuppr/upgrades/talosupgrade.yaml` for one historical reason, and it parses
`ARG TALOS_VERSION=vX.Y.Z`. That default is a Renovate canary. Relaxing the
gate to land an edit removes the canary.

## Docs and comments

- `docs-budget-test.py` ratchets sizes. `--update` lowers or drops a baseline.
  It never raises one. Shrink the file and the baseline in the same change.
- `doc-links-test.py` resolves Markdown links, `docs/…md`, skill paths,
  `references/<file>.md`, and ``skill `name` ``. Do not pin skill prose in a test.
- `functional-comments-guard-test.py` fails if a surviving file drops or
  reorders a `# yaml-language-server:` or `# renovate:` line. The annotation
  value must stay on the following line so the customManager can capture it.

`DIFF_GUARD_BASE` must be set for the functional-comment guard (CI sets it).
