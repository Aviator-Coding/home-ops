# CREATE-only admission webhook drift

k8tz mutates objects at CREATE. It does not revisit a CronJob or Pod that already exists. After the webhook is fixed, reinstalled, or its own pod is recreated, older objects keep whatever they had at birth. Status stays green.

## Detect

Compare the object's `metadata.creationTimestamp` with the webhook's. An object older than the webhook was not stamped by the current admission config.

On CronJobs, the column that matters is `spec.timeZone` (k8tz sets `.spec.timeZone` from `cronJobTimeZone`, and `spec.jobTemplate.spec.timeZone` is not a field). A `<none>` there on a CronJob that should be `America/New_York` is drift. Pods show it as a missing `TZ` env and a missing k8tz init container.

Do not treat a k8tz annotation as proof the spec field is still there. A later apply can clear an unowned injected field and leave the annotation.

## Repair

Delete and recreate. Do not `kubectl patch` `spec.timeZone` onto a live object: the next owner reconcile does not know that field and the patch is how the drift hides.

- HelmRelease-owned (system/fstrim and anything else helm-controller installed): `flux reconcile helmrelease <name> -n <namespace> --force`. Helm diffs its last-applied release, not the live object, so a plain reconcile leaves the old object in place.
- Kustomization-owned: `flux reconcile kustomization <name> -n <namespace>` recreates objects the inventory owns. Suspend first if you are live-testing a spec change (skill `flux-gitops`).

Read the object after the reconcile, then reconcile once more and read it again. The second pass is where an unowned field disappears.

Re-list CronJobs across namespaces after a webhook reinstall. Do not trust a stored list of which ones were stamped. The healthy end state is `spec.timeZone: America/New_York` on each CronJob k8tz should cover, plus `TZ` on pods created afterwards.

## What this is not

A namespace move of k8tz is not how you repair drift. The move's failure modes are in [k8tz.md](k8tz.md). Recreating a CronJob re-stamps it only while the webhook is up and the namespace is not excluded.
