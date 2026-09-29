# Live-testing a HelmRelease

Flux tracks `main`. A `kubectl` edit of a live HelmRelease is valid only
as a pre-merge experiment, and only on an app that already exists.

1. `flux suspend ks <name> -n <namespace>`.
2. Patch. Hand-substitute `${SECRET_DOMAIN}` and any other postBuild vars.
   `kubectl kustomize <app-dir>` has no namespace and does not substitute.
   `kubectl apply -n <namespace>` or the object lands in `default`.
3. Exercise the behavior.
4. `flux resume ks <name> -n <namespace>`. Resume reconciles back to `main`
   immediately. That is the point.

## Apply does not delete keys

A Flux-managed HelmRelease has no
`kubectl.kubernetes.io/last-applied-configuration`. Client-side apply keeps
every key your edit omitted. Both the old and the new key survive, and Helm
then fails the upgrade with a duplicate that looks like a bad manifest.
`helm template` of the intended values renders one. Use `kubectl replace`
when a key is being removed, or delete the stale key, then re-read
`.spec.values` before blaming the chart.

## Helm does not delete fields it never set

Three-way merge removes only keys present in Helm's previous release
manifest. Hand-added `securityContext` keys stay. A hand-added volume or
volumeMount stays even after `flux reconcile ks --with-source` reports
`applied revision: main`. Delete a whole added entry with a strategic-merge
`$patch: delete` keyed on `name` (volumes) or `mountPath` (volumeMounts),
not a null field patch. Then confirm the live object matches git.

## Reading Ready

`HelmRelease.status.conditions[Ready]=True` means the last helm action
succeeded, not that pods are up. Read the workload.

A controller that has been failing does not retry the moment the cause is
gone. `status.conditions` keeps the original message and
`lastTransitionTime` until the next attempt, which can be many minutes out
after repeated failures. Compare that timestamp with `date -u` and
`kubectl -n <ns> logs deploy/<operator> --since=5m`. A restart forces
reconcile on a stateless operator (litellm-operator, toolhive). Do not
restart one that is mid-operation (cloudnative-pg switchover, rook-ceph OSD
update).

## Secrets

Client-side `kubectl apply` of a Secret writes the whole object, data
included, into `kubectl.kubernetes.io/last-applied-configuration`. Anyone
who can get the Secret can read the annotation. Create the Secret with
`kubectl create`, or apply it server-side. Do not print the value.

## flux-system namespace

`components/common` renders `Namespace/flux-system`, but flux-operator owns
the live object and it carries `ssa: Ignore`. Confirm a namespace-label
change there with `kubectl get ns flux-system` after merge.

## Shimmed kubectl

`.mise.toml` sets `KUBECONFIG` to the worktree `kubeconfig`, which is
gitignored and absent in a fresh worktree. A `kubectl` on the mise shim
PATH then talks to `localhost:8080`. Suppressed stderr looks like empty
output. Pass `--kubeconfig=<path>` in any script that also puts the shims
on `PATH`.
