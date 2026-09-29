# System namespace split

Do not merge `system-controller` or `system-upgrade` into `system`. k8tz's chart prepends its release namespace to `ignoredNamespaces`. tuppr's namespace is on the Talos API allowlist. The webhook move is not atomic.

skill `system-namespaces`: [`.agents/skills/system-namespaces/references/k8tz.md`](../.agents/skills/system-namespaces/references/k8tz.md).
