# Tdarr: safe transcode path

Operational record for the `media/tdarr` transcode path: what enforces scope,
what verifies an encode before it overwrites a master, and the state of the
seven parked 4K remux masters.

**Almost everything here is Tdarr server state (SQLite under
`/app/server/Tdarr/DB2/`), not GitOps.** It does not survive a rebuild of the
Tdarr PVC and is invisible to Flux. `docs/tdarr/*.before.json` are recovery
copies of the flow and node config as they stood on 2026-08-31.

Diagnosis this builds on: the 2026-08-31 scout report on the errored remuxes.

---

## 1. Scope: `librariesToNotProcess` never worked here

**It is a Tdarr Pro feature and is inert on an unlicensed install.** It is
stored, shown in the UI and returned by the API, so it looks live. It has never
excluded anything on this server.

`talos-3` carried `librariesToNotProcess: {"j5g_Es7sD": true}` (Series) and
still transcoded `/media/TV-Shows/The Simpsons/.../S35E05` on 2026-08-31
11:35:30Z.

### 1.1 Mechanism (from the server's own bundled source, 2.86.01)

Both queue readers gate the exclusion on `auth`:

```js
// api/nodeRelay/fileQueues/getQueuedFiles.js
if (auth && librariesToNotProcess.length > 0) {
  wheres.push(`tdqx.${fileColumns.db} NOT IN (${ids})`);
}

// api/nodeRelay/fileQueues/getStagedFiles.js
return auth && librariesToNotProcess.length > 0 && (files = await filter(...)), files;
```

`auth` is `await authStatus(false)`, computed once per dispatch in
`getNextTask.js`. And `authStatus` is a **licence** check, not a login:

```js
// server/auth.js
let authorised = false;
const authUpdate = async () => {
  const { tdarrKey } = await getById('SettingsGlobalJSONDB', 'globalsettings');
  const r = await axios.post(`${tdarrioURL}/api/v2/verify-key`, { tdarrKey });
  if (r.status === 200 && r.data === true) authorised = true;
};
const authStatus = async (saU) =>
  (saU === true && authorised !== true && await authUpdate(), authorised);
```

`/api/v2/auth-status` describes itself verbatim as *"For checking Tdarr Pro
status"*. Called with `saU: false` - which is what `getNextTask` does - it
cannot even trigger verification; it returns the cached flag.

Live, on this server:

```console
$ curl -s -XPOST .../api/v2/auth-status -d '{"data":{"saU":false}}'
false
$ sqlite3 database.db "select json_extract(json_data,'$.tdarrKey') from settingsglobaljsondb"
                       # empty string
```

So the clause is never added, on either path.

### 1.2 The "server push" hypothesis is refuted

The scout report suggested the server *pushes* work to the node, bypassing a
node-side accept filter. It does not.

`api/v2/get-new-task.js` is a **node-initiated poll**. The log line
`Server relay sending job to Node relay: talos-3` is emitted on the response
path, immediately after `insert('StagedJSONDB', ...)`, as the server replies to
the node's own `requestNewItem`. There is no node-side accept filter at all -
`librariesToNotProcess` is evaluated server-side inside the queue query. The
failure is licence-gating, not push-bypass.

### 1.3 What actually holds: the library-level toggles

`server/qb/qbUtils.js` -> `plugins/queueQueryFuncs.js` build the same
`NOT IN` clause with **no `auth` check anywhere**:

```js
const { disabledLibrariesFromSchedules, disabledLibraries,
        disabledTranscodeLibraries, disabledHealthCheckLibraries }
      = await getAllDisabledLibraries();
excluded.push(...disabledLibrariesFromSchedules, ...disabledLibraries);
if (type === 'transcode')   excluded.push(...disabledTranscodeLibraries);
if (type === 'healthcheck') excluded.push(...disabledHealthCheckLibraries);
if (excluded.length) wheres.push(`${fileColumns.db} NOT IN (...)`);
```

| Library field | Excludes from | Licence-gated? |
|---|---|---|
| `processLibrary: false` | transcodes **and** health checks | no |
| `processTranscodes: false` | transcodes only | no |
| `processHealthChecks: false` | health checks only | no |
| `schedule[i].checked: false` | both, that hour slot | no |

**Applied 2026-08-31: `processTranscodes: false` on Series (`j5g_Es7sD`).**
Health checks and folder scanning stay on; only transcoding is refused.

### 1.4 Proof that it holds

Node paused first (`nodePaused: true`), so no dispatch was possible during the
test. Both files set to `TranscodeDecisionMaker: Queued`. The probe is
`POST /api/v2/client/status-tables {"opts":{"table":"table1"}}`, which runs the
**same** `reqTableDataDirect` + `getAdditionalQueries({type:'transcode'})`
chain the node's `getQueuedFiles` uses.

```
BEFORE (processTranscodes: true)            table1 totalCount = 2
  [SERIES] .../The Simpsons/Season 35/S35E05 - Treehouse of Horror XXXIV
  [MOVIES] .../Everyone Is Lying to You for Money (2026)

AFTER  (processTranscodes: false)           table1 totalCount = 1
  [MOVIES] .../Everyone Is Lying to You for Money (2026)
```

Same query, same moment. The Movies file is the **positive control**: it proves
the query still works and the exclusion is library-specific, not a global
break.

This is a **refusal, not an absence**. The Series file was still genuinely
queued at the moment it was refused:

```console
$ cruddb getById FileJSONDB "<series file>"
  TranscodeDecisionMaker = "Queued"
  DB                     = "j5g_Es7sD"
```

And the boundary is precisely transcode-scoped - the *same file*, at the *same
moment*, is accepted by the health-check queue and refused by the transcode
queue:

```
table4 (health check) totalCount = 1   [SERIES] ...S35E05...   <- accepted
table1 (transcode)    totalCount = 1   [MOVIES] ...            <- Series refused
```

Both test files were restored to `Transcode error` / `HealthCheck: Success`
afterwards and both queues returned to 0.

`librariesToNotProcess` was **left in place** on the node. It is harmless, but
it is decorative - do not add to it and do not trust it.

---

## 2. The three "guards" in the flow have never run

`movies_av1_nvenc_v1` contains five `customFunction` nodes: `dv_check`,
`snapshot`, and Guards 1-3 (`size_check`, `duration_check`, `hdr_survival`).
Their JavaScript is written, stored, and shipped to the node on every job.
**None of it has ever executed.**

### 2.1 Root cause

The Community `customFunction` plugin reads its code from `args.inputs.code`:

```js
// FlowPlugins/CommunityFlowPlugins/tools/customFunction/1.0.0/index.js
inputs: [ { label: 'JS Code', name: 'code', type: 'string', defaultValue: "<stub>" } ]
...
args.inputs = lib.loadDefaultValues(args.inputs, details);
code = String(args.inputs.code);
```

Every node in this flow stores it under `inputsDB.**function**`. `code` is
therefore missing, `loadDefaultValues` substitutes Tdarr's **default stub**,
and the stub ends:

```js
return { outputFileObj: args.inputFileObj, outputNumber: 1, variables: args.variables };
```

so every one of the five nodes unconditionally returns **output 1**.

### 2.2 Evidence

Job reports dump the resolved inputs, and show both keys side by side - the
default in `code`, the real code stranded in `function`:

```
"code":     "\nmodule.exports = async (args) => {\n\n// see args object data here ...
"function": "// Node: \"Is Dolby Vision? (P5 + P7)\"\n// Place: AFTER guard_home ...
```

Behavioural confirmation, from the 2026-08-31 Series job: on a plain
`h264` / `bt709` file, `dv_check` returned `outputNumber: 1`. Its written code
returns `isDV ? 1 : 2`, so it should have returned 2. The stub returns 1.

Sweep of **all 6514 job reports** for strings only the real code can emit:

| Executed-only signature | reports |
|---|---|
| `Size check: <digit>` | **0** |
| `Duration check: <digit>` | **0** |
| `Snapshot: size=<digit>` | **0** |
| `DV detect: codec_tag="[` | **0** |
| (the same strings *as source text* in the inputs dump) | 1187 |

### 2.3 Consequence

| Node | Intended | Actual |
|---|---|---|
| `dv_check` | Dolby Vision dead-ends | always output 1; and edge `e_dv_bypass` routes output 1 -> `snapshot`, so DV files encode anyway |
| `snapshot` | capture source size/duration/HDR | never sets `variables.user.*` |
| `size_check` | reject <5% or >=95% of source | always "pass" |
| `duration_check` | reject >0.5% duration drift | always "pass" |
| `hdr_survival` | reject lost HDR side data | always "pass" |

**`Replace Original File` is therefore reached unconditionally after any
successful encode, with zero verification.** Six of the seven parked masters
are `DV HDR10`, which is exactly what `dv_check` and `hdr_survival` were
written to protect.

This contradicts section 4.1 of the scout report, which described the guards as
live. They are not, and no manifest test or CI gate in this repo can see it -
the flow is Tdarr database state.

---

## 3. The seven parked masters

All seven verified byte-intact on 2026-08-31 (size + mtime + inode). None was
queued, retried or processed by this work.

| GB | mtime | File |
|---|---|---|
| 66.08 | 2023-08-31 | The Silence of the Lambs (1991) |
| 64.95 | 2024-04-28 | The Departed (2006) |
| 52.70 | 2023-09-05 | Gladiator (2000) |
| 33.33 | 2025-12-24 | Amelie (2001) |
| 27.00 | 2025-12-12 | Wake Up Dead Man (2025) |
| 21.74 | 2026-01-18 | The Rip (2026) |
| 20.92 | 2026-06-05 | A House of Dynamite (2025) |

An eighth, `Johnny Mnemonic (1995)`, was rewritten in place on 2026-08-30 by a
bulk UI requeue (7.02 GB h264 -> 1.41 GB AV1). Lossy and irreversible.

**Never bulk-requeue.** Tdarr rewrites in place.
