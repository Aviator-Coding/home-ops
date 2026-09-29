# talos-3 scheduling

The ledger is [`.agents/skills/node-scheduling/references/talos-3-ledger.md`](../.agents/skills/node-scheduling/references/talos-3-ledger.md). This file stays so comments outside that change can still resolve.

Current effect, including what older notes called section 9: talos-3's taint is `home-operations.com/dedicated: PreferNoSchedule` (PR #1798). It is a scheduler score penalty, not a filter. A hard `NoSchedule` strands a drain of talos-1 or talos-2 once the general fleet has rolled off.

`nodeTaintsPolicy: Honor` does not match `PreferNoSchedule`. Leave those entries. They are not what keeps pods off talos-3.

Request and limit rules: [`.agents/skills/node-scheduling/references/sizing-method.md`](../.agents/skills/node-scheduling/references/sizing-method.md).
