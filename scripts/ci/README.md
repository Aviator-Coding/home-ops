# scripts/ci

Two kinds of script live here.

## Shell scripts: CI-run, wired today

`talos-validate.sh`, `version-consistency.sh`, `tofu-validate.sh` are invoked by
`.github/workflows/validate.yaml` (jobs `talos`, `versions`, `terraform`). Each has a
WHAT-THIS-CATCHES / WHAT-THIS-DOES-NOT-CATCH header explaining its scope - read that before
trusting a green run for more than it claims.

## Python regression tests

The 12 `*-test.py` files are hand-written semantic/behavioral regression tests, each pinning
one specific captain decision or invariant against the live repo state (not a source grep -
most render the real manifests via `kubectl kustomize`/`kustomize build`, parse the result,
and assert on the parsed structure; the `litellm-*` ones additionally exercise the real
`litellm` library the cluster's pinned proxy image runs). Every file's docstring names the
decision it pins - read it first if you're touching the app or feature it covers.

| file | pins |
|---|---|
| `gpu-node-dashboard-test.py` | Intel GPU node Grafana dashboard (hwmon/device-plugin PromQL contract) |
| `grafana-mcp-deploy-test.py` | in-cluster grafana-mcp ToolHive `MCPServer` deployment |
| `grafana-sa-provisioner-test.py` | grafana-sa-provisioner (captain option C) |
| `hostpolicy-ceph-selector-test.py` | Ceph LAN-isolation host CCNP selector |
| `igpu-xe-allowids-test.py` | scoping `gpu.intel.com/xe` to the iGPU |
| `litellm-auto-router-test.py` | D3 complexity-tier auto-router config |
| `litellm-claude-code-subscription-test.py` | Claude Code Max/Pro subscription pass-through |
| `litellm-fallback-chain-test.py` | Phase 5 LiteLLM availability/context fallback chains |
| `litellm-request-logs-test.py` | full prompt/response capture in LiteLLM spend logs |
| `litellm-sso-test.py` | LiteLLM UI SSO through Authentik |
| `terraform-ci-workflows-test.py` | terraform-diff / terraform-publish CI contract |
| `tofu-authentik-stack-test.py` | the Authentik OpenTofu adoption stack |

### How to run them locally

Each file is a standalone script, not a pytest module pytest can discover normally: `main()`
calls its `test_*` functions itself, prints one `[PASS]`/`[FAIL]` line per assertion plus a
summary, and the file's own `if __name__ == "__main__":` block is the only entrypoint.
Run one directly:

```bash
python3 scripts/ci/<name>-test.py
```

Setup, once per environment:

```bash
# .venv is provisioned by .mise.toml's _.python.venv; activate it first
source .venv/bin/activate
uv pip install python-hcl2 "litellm[proxy]==1.98.0"

# mise-managed CLIs some tests shell out to (kubectl, kustomize, tofu, yq) -
# put the venv's own bin/ FIRST or its python3 gets shadowed by mise's bare interpreter
export PATH="$(mise bin-paths | tr '\n' ':')$PATH"
export PATH="$VIRTUAL_ENV/bin:$PATH"

for f in scripts/ci/*-test.py; do python3 "$f" || echo "FAILED: $f"; done
```

`python-hcl2` is needed by `litellm-sso-test.py` (hard import) and
`tofu-authentik-stack-test.py` (guarded import). `litellm[proxy]==1.98.0` matches the
pinned cluster image (`ghcr.io/berriai/litellm-non_root:v1.98.0`, see
`kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml`) and is required by
`litellm-auto-router-test.py` and `litellm-request-logs-test.py`, which hard-fail without
it; `litellm-claude-code-subscription-test.py` and `litellm-fallback-chain-test.py` degrade
to a soft pass-with-note instead.

### CI status

**Not yet wired into any workflow, taskfile, just recipe, or pre-commit hook** - running
them is manual today. `.github/workflows/validate.yaml`'s path filter already watches
`scripts/ci/**`, so editing one of these currently triggers CI jobs that never execute it.
See the task tracked under `homeops-ci-pytest-wiring` (a pending decision blocks wiring a
`validate.yaml` job that runs them: one test currently fails against the tip of `main`,
`litellm-auto-router-test.py`'s `externalsecret_only_existing_1password_items` check, after
`d159d7f5` added a new `litellm-sso` 1Password item the test's allow-list doesn't know
about yet - see that task's report for detail). Wiring the job is only appropriate once all
12 pass clean.
