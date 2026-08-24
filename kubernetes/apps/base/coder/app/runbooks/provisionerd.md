# Provisionerd Runbooks

## ProvisionerdReplicas

One or more provisioner daemons is down. Workspace builds may be queued and
processed slower.

This cluster does **not** run separate `coder-provisioner-*` pods. coderd uses
built-in provisioners (Grafana dashboard text: control with
`CODER_PROVISIONER_DAEMONS` / `--provisioner-daemons`). The HelmRelease does not
set that env, so the chart default applies. Live `coderd_provisionerd_num_daemons`
has been 3.

Alerts fire on that metric: notify `< 3`, warning `< 2`, critical `< 1`. If the
count is wrong, re-adjust those thresholds or set `CODER_PROVISIONER_DAEMONS`
explicitly. Looking for CrashLoopBackOff on provisioner pods will find nothing.
