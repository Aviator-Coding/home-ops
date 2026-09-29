# CREATE-only admission webhook drift

Detection and repair: skill `system-namespaces`, [`.agents/skills/system-namespaces/references/webhook-drift.md`](../.agents/skills/system-namespaces/references/webhook-drift.md).

k8tz mutates CREATE only. A CronJob older than the webhook can keep a missing `spec.timeZone` while every GitOps status stays green. Fix is delete and recreate. HelmRelease-owned objects need `flux reconcile hr <name> --force`. Read the object again after a second reconcile.
