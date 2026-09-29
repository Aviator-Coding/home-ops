# k8tz imageVolume

imageVolume stays off. Chart 0.20.0 already contains the strategy. Reopen only when upstream can mount `/etc/localtime` and changes its recommendation. Dropping the init container also drops its requests.

Full reasoning: skill `system-namespaces`, [`.agents/skills/system-namespaces/references/k8tz.md`](../.agents/skills/system-namespaces/references/k8tz.md).
