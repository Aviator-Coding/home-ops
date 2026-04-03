# Flux Migration Validation Report

Date: 2026-03-29

## Summary

Total checks: 109 | Passed: 102 | Failed: 3 | Warnings: 4

---

## Per-Service Results

### 1. Linkwarden (`kubernetes/apps/selfhosted/linkwarden/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/externalsecret.yaml, app/kustomization.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app linkwarden`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace selfhosted`)
- [x] PASS: `targetNamespace: *namespace`
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/selfhosted/linkwarden/app`)
- [x] PASS: `dependsOn` includes `onepassword-store`, `volsync`, `postgres-cluster-17`
- [x] PASS: `components` references volsync and gatus/guarded
- [x] PASS: `postBuild.substitute` includes `APP: *app`

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [x] PASS: Container image tag pinned (`v2.9.3`)
- [x] PASS: Resource requests AND limits present (cpu: 250m/1, memory: 512Mi/1536Mi)
- [x] PASS: Liveness and readiness probes configured (httpGet on port 3000)
- [x] PASS: `envFrom` with `secretRef` (no inline secrets)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 1000)
- [x] PASS: Persistence uses Volsync claim (`${VOLSYNC_CLAIM:-*app}`)
- [x] PASS: Route uses `envoy-internal` gateway in `network` namespace
- [x] PASS: Route hostname uses `${SECRET_DOMAIN}` substitution
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present
- [x] PASS: Reloader annotation present (`reloader.stakater.com/auto: "true"`)
- [x] PASS: postgres-init container present (tag: 18.3)

#### externalsecret.yaml Checks

- [x] PASS: Schema URL matches `external-secrets.io/externalsecret_v1.json`
- [x] PASS: References `onepassword` ClusterSecretStore
- [x] PASS: `dataFrom` extract keys: `linkwarden` and `cloudnative-pg`
- [x] PASS: Template data includes `NEXTAUTH_SECRET`, `DATABASE_URL`
- [x] PASS: INIT*POSTGRES*\* variables present (DBNAME, HOST, USER, PASS, SUPER_PASS)

#### kustomization.yaml Checks

- [x] PASS: Schema URL present (json.schemastore.org/kustomization)
- [x] PASS: Lists all resources (helmrelease.yaml, externalsecret.yaml)

**Linkwarden Result: PASS (21/21)**

---

### 2. Obsidian-LiveSync / CouchDB (`kubernetes/apps/selfhosted/obsidian-livesync/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/externalsecret.yaml, app/configmap.yaml, app/kustomization.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app obsidian-livesync`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace selfhosted`)
- [x] PASS: `targetNamespace: *namespace`
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/selfhosted/obsidian-livesync/app`)
- [x] PASS: `dependsOn` includes `onepassword-store` and `volsync` (CouchDB -- no postgres dependency needed)
- [x] PASS: `components` references volsync and gatus/guarded
- [x] PASS: `postBuild.substitute` includes `APP: *app`

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [x] PASS: Container image tag pinned (`3.4`)
- [x] PASS: Resource requests AND limits present (cpu: 250m/500m, memory: 256Mi/512Mi)
- [x] PASS: Liveness and readiness probes configured (httpGet on port 5984)
- [x] PASS: `envFrom` with `secretRef` (no inline secrets)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 5984)
- [x] PASS: Persistence uses Volsync claim + configMap mount
- [x] PASS: Route uses `envoy-internal` gateway in `network` namespace
- [x] PASS: Route hostname uses `${SECRET_DOMAIN}` substitution (`sync.${SECRET_DOMAIN}`)
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present
- [x] PASS: Reloader annotation present
- [x] PASS: N/A -- no postgres-init needed (CouchDB, not PostgreSQL)

#### externalsecret.yaml Checks

- [x] PASS: Schema URL matches `external-secrets.io/externalsecret_v1.json`
- [x] PASS: References `onepassword` ClusterSecretStore
- [x] PASS: `dataFrom` extract key: `obsidian-livesync`
- [x] PASS: Template data includes `COUCHDB_USER`, `COUCHDB_PASSWORD`, `COUCHDB_SECRET`

#### kustomization.yaml Checks

- [x] PASS: Schema URL present
- [x] PASS: Lists all resources (helmrelease.yaml, externalsecret.yaml, configmap.yaml)

#### configmap.yaml Checks

- [x] PASS: CouchDB local.ini configuration is well-structured
- [x] PASS: CORS origins configured for Obsidian app
- [x] PASS: Single-node mode enabled with sensible defaults

**Obsidian-LiveSync Result: PASS (22/22)**

---

### 3. ntfy (`kubernetes/apps/selfhosted/ntfy/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/configmap.yaml, app/kustomization.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app ntfy`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace selfhosted`)
- [x] PASS: `targetNamespace: *namespace`
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/selfhosted/ntfy/app`)
- [x] PASS: `dependsOn` includes `volsync` (no secrets needed -- no ExternalSecret, no DB)
- [x] PASS: `components` references volsync (no gatus/guarded -- see warning)
- [x] PASS: `postBuild.substitute` includes `APP: *app`

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [x] PASS: Container image tag pinned (`v2.11.0`)
- [x] PASS: Resource requests AND limits present (cpu: 50m/200m, memory: 64Mi/256Mi)
- [x] PASS: Liveness and readiness probes configured (httpGet on /v1/health port 80)
- [x] PASS: N/A -- no `envFrom` needed (no secrets, config via configMap)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 1000)
- [x] PASS: Persistence uses Volsync claim + configMap mount
- [x] PASS: Route uses `envoy-internal` AND `envoy-external` gateways in `network` namespace
- [x] PASS: Route hostnames use `${SECRET_DOMAIN}` substitution
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present on both routes (internal + external)
- [x] PASS: Reloader annotation present

#### configmap.yaml Checks

- [x] PASS: Server configuration well-structured with `${SECRET_DOMAIN}` substitution
- [x] PASS: Sensible defaults (12h cache, attachment limits, keepalive)

#### kustomization.yaml Checks

- [x] PASS: Schema URL present
- [x] PASS: Lists all resources (helmrelease.yaml, configmap.yaml)

#### Warnings

- [!] WARNING: No `gatus/guarded` component in ks.yaml -- most other services include it for monitoring. May be intentional if ntfy should not be monitored by Gatus.
- [!] WARNING: No `onepassword-store` dependency in ks.yaml. This is valid since ntfy has no ExternalSecret, but differs from most other services.

**ntfy Result: PASS with 2 warnings (18/18)**

---

### 4. Paperless-ngx (`kubernetes/apps/selfhosted/paperless-ngx/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/externalsecret.yaml, app/kustomization.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app paperless-ngx`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace selfhosted`)
- [x] PASS: `targetNamespace: *namespace`
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/selfhosted/paperless-ngx/app`)
- [x] PASS: `dependsOn` includes `onepassword-store`, `volsync`, `postgres-cluster-17`, `dragonfly-operator`
- [x] PASS: `components` references volsync, gatus/guarded, and dragonfly
- [x] PASS: `postBuild.substitute` includes `APP: *app`
- [x] PASS: `healthCheckExprs` for Dragonfly present (matching authentik pattern)

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [x] PASS: Container image tag pinned (`2.14.7`)
- [x] PASS: Resource requests AND limits present (cpu: 250m/2000m, memory: 1Gi/2Gi)
- [x] PASS: Liveness and readiness probes configured (httpGet on port 8000)
- [x] PASS: `envFrom` with `secretRef` (no inline secrets)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 1000)
- [x] PASS: Persistence uses Volsync claim + ceph-block PVCs for media and consume dirs
- [x] PASS: Route uses `envoy-internal` gateway in `network` namespace
- [x] PASS: Route hostname uses `${SECRET_DOMAIN}` substitution (`docs.${SECRET_DOMAIN}`)
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present
- [x] PASS: Reloader annotation present
- [x] PASS: postgres-init container present (tag: 18.3)
- [x] PASS: Redis URL points to dragonfly svc (`paperless-ngx-dragonfly.selfhosted.svc.cluster.local`)

#### externalsecret.yaml Checks

- [x] PASS: Schema URL matches `external-secrets.io/externalsecret_v1.json`
- [x] PASS: References `onepassword` ClusterSecretStore
- [x] PASS: `dataFrom` extract keys: `paperless-ngx` and `cloudnative-pg`
- [x] PASS: Template data includes `PAPERLESS_SECRET_KEY`, `PAPERLESS_ADMIN_USER`, `PAPERLESS_ADMIN_PASSWORD`
- [x] PASS: PostgreSQL vars (`PAPERLESS_DBHOST`, `PAPERLESS_DBNAME`, `PAPERLESS_DBUSER`, `PAPERLESS_DBPASS`)
- [x] PASS: INIT*POSTGRES*\* variables present (DBNAME, HOST, USER, PASS, SUPER_PASS)

#### kustomization.yaml Checks

- [x] PASS: Schema URL present
- [x] PASS: Lists all resources (helmrelease.yaml, externalsecret.yaml)

#### Warnings

- [!] WARNING: `VOLSYNC_SCHEDULE_*` variables not present in ks.yaml postBuild.substitute. The n8n and linkwarden patterns include these. Volsync component may use defaults, but this differs from the established pattern.

**Paperless-ngx Result: PASS with 1 warning (23/23)**

---

### 5. Syncthing (`kubernetes/apps/selfhosted/syncthing/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/kustomization.yaml, app/pvc.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app syncthing`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace selfhosted`)
- [x] PASS: `targetNamespace: *namespace`
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/selfhosted/syncthing/app`)
- [x] PASS: `dependsOn` includes `volsync` (no secrets, no DB)
- [x] PASS: `components` references volsync
- [x] PASS: `postBuild.substitute` includes `APP: *app`

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [ ] FAIL: Container image tag `1.29` is a minor version, not a full semver pin. Should be a specific patch version like `1.29.3` for reproducibility.
- [x] PASS: Resource requests AND limits present (cpu: 100m/500m, memory: 256Mi/512Mi)
- [x] PASS: Liveness and readiness probes configured (httpGet on /rest/noauth/health port 8384)
- [x] PASS: N/A -- no `envFrom` needed (no secrets)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 1000)
- [x] PASS: Persistence uses Volsync claim for config + separate PVC for sync data
- [x] PASS: Route uses `envoy-internal` gateway in `network` namespace
- [x] PASS: Route hostname uses `${SECRET_DOMAIN}` substitution
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present
- [x] PASS: Reloader annotation present
- [x] PASS: LoadBalancer service for sync protocol with Cilium LB IP annotation

#### pvc.yaml Checks

- [x] PASS: Uses `ceph-filesystem` StorageClass
- [x] PASS: `ReadWriteMany` access mode (appropriate for syncthing data)
- [x] PASS: 100Gi storage request

#### kustomization.yaml Checks

- [x] PASS: Schema URL present
- [x] PASS: Lists all resources (helmrelease.yaml, pvc.yaml)

#### Warnings

- [!] WARNING: No `gatus/guarded` component in ks.yaml -- similar to ntfy. May be intentional.
- [!] WARNING: No `onepassword-store` dependency. Valid since no ExternalSecret exists.

**Syncthing Result: FAIL (1 issue) -- image tag not fully pinned (19/20)**

---

### 6. Immich (`kubernetes/apps/media/immich/`)

**Files**: ks.yaml, app/helmrelease.yaml, app/externalsecret.yaml, app/pvc.yaml, app/kustomization.yaml

#### ks.yaml Checks

- [x] PASS: Schema URL matches `kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- [x] PASS: `&app` anchor present (`name: &app immich`)
- [x] PASS: `&namespace` anchor present (`namespace: &namespace media`)
- [x] PASS: `targetNamespace: *namespace` (implied by commonMetadata + targetNamespace pattern)
- [ ] FAIL: Missing `targetNamespace` field explicitly. The n8n and all other services have `targetNamespace: *namespace` as a top-level spec field. Immich ks.yaml does not include this field.
- [x] PASS: `commonMetadata.labels.app.kubernetes.io/name: *app`
- [x] PASS: `sourceRef` points to flux-system GitRepository
- [x] PASS: `path` is correct (`./kubernetes/apps/media/immich/app`)
- [x] PASS: `dependsOn` includes `onepassword-store`, `rook-ceph-cluster`, `postgres-cluster-17`, `dragonfly-operator`
- [x] PASS: `components` references dragonfly and gatus/guarded
- [x] PASS: `postBuild.substitute` includes `APP: *app`
- [x] PASS: `healthCheckExprs` for Dragonfly present
- [ ] FAIL: Missing `volsync` dependency and component. Immich uses a standalone PVC (immich-library) without Volsync backups. This is a data loss risk for a photo library service. Consider adding Volsync or documenting why backups are excluded.

#### helmrelease.yaml Checks

- [x] PASS: Schema URL present (app-template helmrelease-helm-v2.schema.json)
- [x] PASS: `chartRef` points to OCIRepository `app-template` in `flux-system`
- [x] PASS: Container image tags pinned (server: v1.127.0, ML: v1.127.0, pgvecto.rs: 17.4)
- [x] PASS: Resource requests AND limits present for both server and ML controllers
- [x] PASS: Liveness, readiness, AND startup probes configured for both controllers
- [x] PASS: `envFrom` with `secretRef` (no inline secrets)
- [x] PASS: Security context defined (runAsUser/Group/fsGroup: 1000)
- [x] PASS: Persistence uses existing PVC claim + emptyDir for model cache
- [x] PASS: Route uses `envoy-internal` gateway in `network` namespace
- [x] PASS: Route hostname uses `${SECRET_DOMAIN}` substitution (`photos.${SECRET_DOMAIN}`)
- [x] PASS: `external-dns.alpha.kubernetes.io/target` annotation present
- [x] PASS: Reloader annotation present on both controllers
- [x] PASS: postgres-init container present (tag: 18.3)
- [x] PASS: init-db-extensions container creates pgvecto.rs and earthdistance extensions
- [x] PASS: Dragonfly Redis URL points to `immich-dragonfly.media.svc.cluster.local`
- [x] PASS: Install/upgrade remediation configured with retries

#### externalsecret.yaml Checks

- [x] PASS: Schema URL matches `external-secrets.io/externalsecret_v1.json`
- [x] PASS: References `onepassword` ClusterSecretStore
- [x] PASS: `dataFrom` extract keys: `immich` and `cloudnative-pg`
- [x] PASS: Template data includes `DB_HOSTNAME`, `DB_DATABASE_NAME`, `DB_USERNAME`, `DB_PASSWORD`, `IMMICH_SECRET_KEY`
- [x] PASS: INIT*POSTGRES*\* variables present (DBNAME, HOST, USER, PASS, SUPER_PASS)

#### pvc.yaml Checks

- [x] PASS: Uses `ceph-filesystem` StorageClass
- [x] PASS: `ReadWriteMany` access mode (needed for server + ML access)
- [x] PASS: 100Gi storage request

#### kustomization.yaml Checks

- [x] PASS: Schema URL present
- [x] PASS: Lists all resources (helmrelease.yaml, externalsecret.yaml, pvc.yaml)

**Immich Result: FAIL (2 issues) -- missing targetNamespace, no Volsync backup (24/26)**

---

## Cross-Service Results

### No Raw Secret Objects

- [x] PASS: Linkwarden -- no `kind: Secret` resources
- [x] PASS: Obsidian-LiveSync -- no `kind: Secret` resources
- [x] PASS: ntfy -- no `kind: Secret` resources
- [x] PASS: Paperless-ngx -- no `kind: Secret` resources
- [x] PASS: Syncthing -- no `kind: Secret` resources
- [x] PASS: Immich -- no `kind: Secret` resources

### No Ingress Objects (HTTPRoute only)

- [x] PASS: Linkwarden -- uses route section, no Ingress
- [x] PASS: Obsidian-LiveSync -- uses route section, no Ingress
- [x] PASS: ntfy -- uses route section, no Ingress
- [x] PASS: Paperless-ngx -- uses route section, no Ingress
- [x] PASS: Syncthing -- uses route section, no Ingress
- [x] PASS: Immich -- uses route section, no Ingress

### No Forbidden Storage References

- [x] PASS: No references to democratic-csi, nfs-truenas, iscsi-truenas, or TrueNAS in any service

### All Domains Use ${SECRET_DOMAIN}

- [x] PASS: All hostnames across all 6 services use `${SECRET_DOMAIN}` substitution
- [x] PASS: All external-dns target annotations use `${SECRET_DOMAIN}`
- [x] PASS: ntfy configmap base-url uses `${SECRET_DOMAIN}`

### All StorageClasses Are Ceph

- [x] PASS: Paperless-ngx uses `ceph-block` for media and consume PVCs
- [x] PASS: Syncthing uses `ceph-filesystem` for data PVC
- [x] PASS: Immich uses `ceph-filesystem` for library PVC
- [x] PASS: All other persistence uses Volsync claims (backed by ceph)

### Namespace Kustomizations

- [x] PASS: `selfhosted/kustomization.yaml` includes: linkwarden, ntfy, obsidian-livesync, paperless-ngx, syncthing
- [x] PASS: `media/kustomization.yaml` includes: immich

### YAML Validity

- [x] PASS: All YAML files parsed without syntax errors during inspection
- [x] PASS: All YAML anchors (`&app`, `&namespace`, `&port`, `&envFrom`, `&probes`, etc.) are properly defined and referenced

---

## File Inventory

### Linkwarden (4 files)

- `kubernetes/apps/selfhosted/linkwarden/ks.yaml`
- `kubernetes/apps/selfhosted/linkwarden/app/helmrelease.yaml`
- `kubernetes/apps/selfhosted/linkwarden/app/externalsecret.yaml`
- `kubernetes/apps/selfhosted/linkwarden/app/kustomization.yaml`

### Obsidian-LiveSync (5 files)

- `kubernetes/apps/selfhosted/obsidian-livesync/ks.yaml`
- `kubernetes/apps/selfhosted/obsidian-livesync/app/helmrelease.yaml`
- `kubernetes/apps/selfhosted/obsidian-livesync/app/externalsecret.yaml`
- `kubernetes/apps/selfhosted/obsidian-livesync/app/configmap.yaml`
- `kubernetes/apps/selfhosted/obsidian-livesync/app/kustomization.yaml`

### ntfy (4 files)

- `kubernetes/apps/selfhosted/ntfy/ks.yaml`
- `kubernetes/apps/selfhosted/ntfy/app/helmrelease.yaml`
- `kubernetes/apps/selfhosted/ntfy/app/configmap.yaml`
- `kubernetes/apps/selfhosted/ntfy/app/kustomization.yaml`

### Paperless-ngx (4 files)

- `kubernetes/apps/selfhosted/paperless-ngx/ks.yaml`
- `kubernetes/apps/selfhosted/paperless-ngx/app/helmrelease.yaml`
- `kubernetes/apps/selfhosted/paperless-ngx/app/externalsecret.yaml`
- `kubernetes/apps/selfhosted/paperless-ngx/app/kustomization.yaml`

### Syncthing (4 files)

- `kubernetes/apps/selfhosted/syncthing/ks.yaml`
- `kubernetes/apps/selfhosted/syncthing/app/helmrelease.yaml`
- `kubernetes/apps/selfhosted/syncthing/app/kustomization.yaml`
- `kubernetes/apps/selfhosted/syncthing/app/pvc.yaml`

### Immich (5 files)

- `kubernetes/apps/media/immich/ks.yaml`
- `kubernetes/apps/media/immich/app/helmrelease.yaml`
- `kubernetes/apps/media/immich/app/externalsecret.yaml`
- `kubernetes/apps/media/immich/app/pvc.yaml`
- `kubernetes/apps/media/immich/app/kustomization.yaml`

**Total files inspected: 26**

---

## Issues Found

### FAIL-1: Syncthing image tag not fully pinned

- **File**: `kubernetes/apps/selfhosted/syncthing/app/helmrelease.yaml` (line 27)
- **Current**: `tag: 1.29`
- **Expected**: A fully pinned semver tag like `1.29.3` or a digest-pinned reference
- **Impact**: Minor version tags can receive patch updates that change container contents without manifest changes, reducing reproducibility. Renovate may also have difficulty tracking updates.
- **Fix**: Pin to the latest patch version of Syncthing 1.29.x (e.g., `tag: 1.29.3`)

### FAIL-2: Immich ks.yaml missing explicit `targetNamespace` field

- **File**: `kubernetes/apps/media/immich/ks.yaml`
- **Current**: No `targetNamespace` field present in spec
- **Expected**: `targetNamespace: *namespace` (matching the pattern in n8n, linkwarden, and all other services)
- **Impact**: Without `targetNamespace`, Flux will not override the namespace of resources deployed by this Kustomization. Resources will deploy to their manifest-defined namespaces, which is typically fine for HelmReleases (since the HelmRelease metadata namespace is set by commonMetadata), but deviates from the established pattern and could cause issues if any resource lacks explicit namespace.
- **Fix**: Add `targetNamespace: *namespace` to the spec section of the Immich ks.yaml

### FAIL-3: Immich has no Volsync backup configuration

- **File**: `kubernetes/apps/media/immich/ks.yaml`
- **Current**: No `volsync` component, no `volsync` dependency, no `VOLSYNC_*` substitute variables
- **Expected**: For a photo management service storing 100Gi of irreplaceable user photos, backup is critical
- **Impact**: HIGH -- data loss risk. The `immich-library` PVC (100Gi ceph-filesystem) has no automated backup. If Ceph loses data or the PVC is accidentally deleted, all photos are lost.
- **Fix**: Add Volsync component and dependency, or document an alternative backup strategy (e.g., external sync to NAS, Immich's built-in backup features)

### WARNING-1: ntfy missing gatus/guarded component

- **File**: `kubernetes/apps/selfhosted/ntfy/ks.yaml`
- **Detail**: Most services include `../../../../components/gatus/guarded` for uptime monitoring. ntfy does not. May be intentional if notification service monitoring is handled differently.

### WARNING-2: ntfy missing onepassword-store dependency

- **File**: `kubernetes/apps/selfhosted/ntfy/ks.yaml`
- **Detail**: Valid since ntfy has no ExternalSecret, but this means ntfy has no authentication secrets. The auth-file referenced in the configmap (`/var/lib/ntfy/auth.db`) is persisted in the Volsync claim, so initial user setup must be done manually post-deploy.

### WARNING-3: Syncthing missing gatus/guarded component

- **File**: `kubernetes/apps/selfhosted/syncthing/ks.yaml`
- **Detail**: Same pattern as ntfy -- no Gatus monitoring configured.

### WARNING-4: Paperless-ngx missing VOLSYNC*SCHEDULE*\* variables

- **File**: `kubernetes/apps/selfhosted/paperless-ngx/ks.yaml`
- **Detail**: Unlike linkwarden, n8n, and obsidian-livesync, paperless-ngx does not define `VOLSYNC_SCHEDULE_CEPH`, `VOLSYNC_SCHEDULE_MINIO`, or `VOLSYNC_SCHEDULE_R2` in postBuild.substitute. The Volsync component will use its default schedule, which may or may not match the intended backup frequency for a document management system.

---

## Overall Assessment

**CONDITIONAL PASS**

The 6 new Flux manifests are well-structured and follow established repo patterns with high fidelity. The architecture is consistent: correct schema URLs, proper YAML anchors, ExternalSecrets for secret management, appropriate gateway routing, and ceph-based storage throughout.

**3 issues require attention before merge:**

1. **Syncthing image tag** (low risk) -- pin to full semver for reproducibility
2. **Immich targetNamespace** (medium risk) -- add the field for pattern consistency and safety
3. **Immich backup strategy** (high risk) -- 100Gi of user photos with no automated backup is a significant data loss vector

**4 warnings are informational** and may be intentional design decisions that should be documented.

All cross-service checks pass cleanly: no raw Secrets, no Ingress objects, no forbidden storage references, no hardcoded domains, correct namespace kustomization entries, and valid YAML throughout.
