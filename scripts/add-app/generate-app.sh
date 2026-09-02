#!/usr/bin/env bash
# Scaffold a new app's manifests to match this repo's app-structure convention.
# See docs/app-structure.md for the three shapes this generates and the traps
# it encodes by construction. Run from the repo root.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/add-app/generate-app.sh <namespace> <app> [options]

Positional:
  <namespace>   existing namespace dir under kubernetes/apps/base/, e.g. selfhosted
  <app>         new app name (kebab-case), e.g. my-app

Options:
  --shape=app|family|crd-split|parameterized   default: app
  --piece=<name>          family shape only: sibling directory name (e.g. operator,
                          cluster, exporter). Use "app" for the main piece.
  --key=<name>            parameterized shape only: the grouping directory under app/
  --instance=<name>       parameterized shape only: the instance directory under app/<key>/
  --secrets               also write an externalsecret.yaml stub and depend on onepassword-store
  --dragonfly             add the components/dragonfly include with a correctly computed
                          relative path (namespace-scoped cache; see components/dragonfly)
  --dry-run               print what would be written/changed, write nothing
  -h, --help              show this help

Examples:
  # plain single-component app in selfhosted
  scripts/add-app/generate-app.sh selfhosted my-app

  # CRD-split app in ai (chart ships its own CRDs)
  scripts/add-app/generate-app.sh ai my-controller --shape=crd-split

  # add a second family member ("playwright") to an existing app ("rsshub")
  scripts/add-app/generate-app.sh selfhosted rsshub --shape=family --piece=playwright

  # parameterized instance (rare - see docs/app-structure.md "Parameterized instance")
  scripts/add-app/generate-app.sh actions-runner-system gha-runner-scale-set \
    --shape=parameterized --key=aviator-coding --instance=home-ops
EOF
}

NAMESPACE=""
APP=""
SHAPE="app"
PIECE="app"
KEY=""
INSTANCE=""
WITH_SECRETS=0
WITH_DRAGONFLY=0
DRY_RUN=0

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shape=*) SHAPE="${1#*=}" ;;
    --piece=*) PIECE="${1#*=}" ;;
    --key=*) KEY="${1#*=}" ;;
    --instance=*) INSTANCE="${1#*=}" ;;
    --secrets) WITH_SECRETS=1 ;;
    --dragonfly) WITH_DRAGONFLY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    *) POSITIONAL+=("$1") ;;
  esac
  shift
done
set -- "${POSITIONAL[@]}"
NAMESPACE="${1:-}"
APP="${2:-}"

if [[ -z "$NAMESPACE" || -z "$APP" ]]; then
  echo "error: <namespace> and <app> are required" >&2
  usage >&2
  exit 1
fi
if [[ ! "$APP" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "error: <app> must be lowercase kebab-case" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BASE_NS_DIR="kubernetes/apps/base/$NAMESPACE"
MAIN_NS_DIR="kubernetes/apps/main/$NAMESPACE"
if [[ ! -d "$BASE_NS_DIR" ]]; then
  echo "error: namespace '$NAMESPACE' has no kubernetes/apps/base/$NAMESPACE - this scaffold" >&2
  echo "  only adds apps to existing namespaces. A new top-level namespace needs a manual" >&2
  echo "  entry on kubernetes/apps/main/kustomization.yaml first (see AGENTS.md NOTES)." >&2
  exit 1
fi
if [[ ! -d "$MAIN_NS_DIR" ]]; then
  echo "error: expected overlay dir $MAIN_NS_DIR to exist alongside $BASE_NS_DIR" >&2
  exit 1
fi

write_file() {
  local path="$1" content="$2"
  if [[ -e "$path" ]]; then
    echo "error: $path already exists - refusing to overwrite" >&2
    exit 1
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "--- would write $path ---"
    printf '%s\n' "$content"
    return
  fi
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  echo "wrote $path"
}

append_file() {
  local path="$1" content="$2"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "--- would append to $path ---"
    printf '%s\n' "$content"
    return
  fi
  printf '%s\n' "$content" >> "$path"
  echo "appended to $path"
}

# Compute the relative-path depth from a base kustomize directory up to
# kubernetes/, matching the trap documented in docs/app-structure.md: a
# components: include's ../ count depends on how deep the base path is, and
# this is the exact bug the searxng app/ rename hit on 2026-09-01.
components_relpath() {
  local dir="$1" name="$2"
  # dir is repo-relative, e.g. kubernetes/apps/base/ai/my-app/app
  local rest="${dir#kubernetes/}"
  local n
  n=$(awk -F/ '{print NF}' <<<"$rest")
  local dots=""
  for ((i = 0; i < n; i++)); do dots+="../"; done
  echo "${dots}components/${name}"
}

kustomization_yaml_header='# yaml-language-server: $schema=https://json.schemastore.org/kustomization'
helmrelease_schema='# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s-labs/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json'
flux_helmrelease_schema='# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/helm.toolkit.fluxcd.io/helmrelease_v2.json'
externalsecret_schema='# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/external-secrets.io/externalsecret_v1.json'
overlay_ks_schema='# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json'
ocirepository_schema='# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/source.toolkit.fluxcd.io/ocirepository_v1.json'

# Renders base/<piece>/helmrelease.yaml - app-template chartRef, TODOs pointing
# at exactly the two most consequential silent failures: an unrecognized
# pod-options key (Helm drops it with no warning - use defaultPodOptions,
# spelled exactly as shown) and image/port placeholders to fill in.
render_helmrelease() {
  local app="$1"
  cat <<EOF
---
$helmrelease_schema
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app $app
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: app-template
  install:
    remediation:
      retries: -1
  upgrade:
    cleanupOnFail: true
    remediation:
      retries: 3
  values:
    controllers:
      $app:
        replicas: 1
        strategy: RollingUpdate
        annotations:
          reloader.stakater.com/auto: "true"
        containers:
          main:
            image:
              repository: TODO/set-repository
              tag: TODO@sha256:0000000000000000000000000000000000000000000000000000000000000
            # env: {}
            probes:
              liveness: &probes
                enabled: true
                custom: true
                spec:
                  httpGet:
                    path: /
                    port: &port 8080
                  initialDelaySeconds: 0
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
              readiness: *probes
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities:
                drop:
                  - ALL
            resources:
              requests:
                cpu: 10m
                memory: 64Mi
              limits:
                memory: 256Mi
    # Pod-level security lives here, spelled exactly "defaultPodOptions" and
    # a sibling of controllers:/service: - not nested inside controllers:.
    # A wrong key or wrong nesting here is silently dropped by Helm (no
    # warning from flate, no error from Flux; see docs/app-structure.md "A
    # pod-options key the chart doesn't recognize is silently discarded").
    # Fill in the real uid/gid this image runs as.
    defaultPodOptions:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000 # TODO: verify against the image's real uid
        runAsGroup: 1000 # TODO: verify against the image's real gid
        fsGroup: 1000 # TODO: verify against the image's real gid
        fsGroupChangePolicy: OnRootMismatch
    service:
      app:
        ports:
          http:
            port: *port
    route:
      app:
        hostnames:
          - "{{ .Release.Name }}.\${SECRET_DOMAIN}"
        parentRefs:
          - name: envoy-internal
            namespace: network
            sectionName: https
EOF
}

render_base_kustomization() {
  local secrets="$1"
  local resources="  - ./helmrelease.yaml"
  if [[ "$secrets" -eq 1 ]]; then
    resources="  - ./externalsecret.yaml
$resources"
  fi
  cat <<EOF
---
$kustomization_yaml_header
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
$resources
EOF
}

render_externalsecret() {
  local app="$1"
  cat <<EOF
---
$externalsecret_schema
# 1Password vault item "$app" - TODO: create it and list the real keys below.
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: $app
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: $app-secret
  dataFrom:
    - extract:
        key: $app
EOF
}

render_crds_kustomization() {
  cat <<EOF
---
$kustomization_yaml_header
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./ocirepository.yaml
  - ./helmrelease.yaml
EOF
}

render_crds_ocirepository() {
  local app="$1"
  cat <<EOF
---
$ocirepository_schema
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: $app-crds
spec:
  interval: 1h
  layerSelector:
    mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
    operation: copy
  ref:
    tag: TODO # renovate: datasource=docker depName=TODO/chart
  url: oci://TODO/chart
EOF
}

render_crds_helmrelease() {
  local app="$1"
  cat <<EOF
---
$flux_helmrelease_schema
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app $app-crds
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: $app-crds
  install:
    crds: CreateReplace
    remediation:
      retries: -1
  upgrade:
    crds: CreateReplace
    cleanupOnFail: true
    remediation:
      retries: 3
EOF
}

# Renders one overlay Kustomization CR document (no leading/trailing ---
# management here - callers decide whether this is the whole file or one
# document among several separated by ---).
render_overlay_doc() {
  local ks_name="$1" namespace="$2" base_path="$3" secrets="$4" dragonfly="$5" wait_false="$6" depends_on_crds="$7" workload_name="$8"
  # workload_name is the rendered Deployment/HelmRelease name. For the default
  # app shape it matches ks_name; for a family sibling piece it is the piece
  # name (live precedent: KS rsshub-playwright, Deployment playwright).
  [[ -z "$workload_name" ]] && workload_name="$ks_name"
  local depends=()
  [[ "$secrets" -eq 1 ]] && depends+=("    - name: onepassword-store
      namespace: security")
  [[ "$dragonfly" -eq 1 ]] && depends+=("    - name: dragonfly-operator
      namespace: database")
  [[ "$depends_on_crds" -eq 1 ]] && depends+=("    - name: $ks_name-crds
      namespace: $namespace")
  local depends_on_block=""
  if [[ ${#depends[@]} -gt 0 ]]; then
    depends_on_block="  dependsOn:
$(printf '%s\n' "${depends[@]}")
"
  fi
  local components_block=""
  if [[ "$dragonfly" -eq 1 ]]; then
    components_block="  components:
    - $(components_relpath "$base_path" dragonfly)
"
  fi
  local wait_line=""
  [[ "$wait_false" -eq 1 ]] && wait_line="  wait: false
"
  local healthcheck_name_line
  if [[ "$workload_name" == "$ks_name" ]]; then
    healthcheck_name_line="      name: *app"
  else
    healthcheck_name_line="      name: $workload_name"
  fi
  cat <<EOF
---
$overlay_ks_schema
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app $ks_name
  namespace: &namespace $namespace
spec:
$components_block  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: *namespace
  interval: 1h
  retryInterval: 2m
  timeout: 5m
  path: ./$base_path
  prune: true
  # healthChecks target the rendered workload, not the HelmRelease - a
  # HelmRelease-kind check stays Ready through a live crashloop (see
  # docs/app-structure.md). wait: false so Flux uses this list instead of
  # assessing the whole inventory.
  healthChecks:
    - apiVersion: apps/v1
      kind: Deployment
$healthcheck_name_line
      namespace: *namespace
$wait_line$depends_on_block  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  postBuild:
    substituteFrom:
      - name: cluster-secrets
        kind: Secret
    substitute:
      APP: *app
EOF
}

add_namespace_resource_entry() {
  local ns_kustomization="$MAIN_NS_DIR/kustomization.yaml"
  local entry="./$APP.yaml"
  if grep -qF -- "- $entry" "$ns_kustomization"; then
    echo "$ns_kustomization already references $entry, leaving it alone"
    return
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "--- would append '  - $entry' to the resources: list in $ns_kustomization ---"
    return
  fi
  # Appended at the end of the existing resources: block, not inserted
  # alphabetically - this repo's namespaces don't agree on an order (ai/
  # and downloads/ sort alphabetically, selfhosted/ is roughly
  # chronological), so append is the only assumption that holds everywhere.
  python3 - "$ns_kustomization" "$entry" <<'PYEOF'
import sys
path, entry = sys.argv[1], sys.argv[2]
with open(path) as f:
    lines = f.readlines()
try:
    start = next(i for i, l in enumerate(lines) if l.rstrip("\n") == "resources:")
except StopIteration:
    sys.exit(f"error: no 'resources:' line found in {path}")
end = start + 1
while end < len(lines) and (lines[end].startswith("  - ") or lines[end].startswith("  #")):
    end += 1
lines.insert(end, f"  - {entry}\n")
with open(path, "w") as f:
    f.writelines(lines)
PYEOF
  echo "appended '  - $entry' to the resources: list in $ns_kustomization"
}

write_or_append_overlay() {
  local overlay_path="$1" doc="$2"
  if [[ -f "$overlay_path" ]]; then
    append_file "$overlay_path" "$doc"
  else
    write_file "$overlay_path" "$doc"
  fi
}

case "$SHAPE" in
  app)
    base_dir="$BASE_NS_DIR/$APP/app"
    write_file "$base_dir/kustomization.yaml" "$(render_base_kustomization "$WITH_SECRETS")"
    write_file "$base_dir/helmrelease.yaml" "$(render_helmrelease "$APP")"
    [[ "$WITH_SECRETS" -eq 1 ]] && write_file "$base_dir/externalsecret.yaml" "$(render_externalsecret "$APP")"
    overlay_path="$MAIN_NS_DIR/$APP.yaml"
    doc="$(render_overlay_doc "$APP" "$NAMESPACE" "$base_dir" "$WITH_SECRETS" "$WITH_DRAGONFLY" 1 0 "$APP")"
    write_file "$overlay_path" "$doc"
    add_namespace_resource_entry
    ;;

  family)
    if [[ -z "$PIECE" ]]; then
      echo "error: --shape=family requires --piece=<name>" >&2
      exit 1
    fi
    # Live family pieces name the HelmRelease/controller after the piece
    # (playwright), and the overlay Kustomization after app-piece
    # (rsshub-playwright). Naming both after the parent app collides with the
    # main piece and leaves healthChecks looking for the wrong Deployment.
    if [[ "$PIECE" == "app" ]]; then
      ks_name="$APP"
      hr_name="$APP"
    else
      ks_name="$APP-$PIECE"
      hr_name="$PIECE"
    fi
    base_dir="$BASE_NS_DIR/$APP/$PIECE"
    write_file "$base_dir/kustomization.yaml" "$(render_base_kustomization "$WITH_SECRETS")"
    write_file "$base_dir/helmrelease.yaml" "$(render_helmrelease "$hr_name")"
    [[ "$WITH_SECRETS" -eq 1 ]] && write_file "$base_dir/externalsecret.yaml" "$(render_externalsecret "$hr_name")"
    overlay_path="$MAIN_NS_DIR/$APP.yaml"
    doc="$(render_overlay_doc "$ks_name" "$NAMESPACE" "$base_dir" "$WITH_SECRETS" "$WITH_DRAGONFLY" 1 0 "$hr_name")"
    write_or_append_overlay "$overlay_path" "$doc"
    add_namespace_resource_entry
    echo "reminder: family members that depend on each other need an explicit" \
         "spec.dependsOn on the dependent piece's Kustomization document - add it by hand."
    ;;

  crd-split)
    app_dir="$BASE_NS_DIR/$APP/app"
    crds_dir="$BASE_NS_DIR/$APP/crds"
    write_file "$app_dir/kustomization.yaml" "$(render_base_kustomization "$WITH_SECRETS")"
    write_file "$app_dir/helmrelease.yaml" "$(render_helmrelease "$APP")"
    [[ "$WITH_SECRETS" -eq 1 ]] && write_file "$app_dir/externalsecret.yaml" "$(render_externalsecret "$APP")"
    write_file "$crds_dir/kustomization.yaml" "$(render_crds_kustomization)"
    write_file "$crds_dir/ocirepository.yaml" "$(render_crds_ocirepository "$APP")"
    write_file "$crds_dir/helmrelease.yaml" "$(render_crds_helmrelease "$APP")"

    overlay_path="$MAIN_NS_DIR/$APP.yaml"
    app_doc="$(render_overlay_doc "$APP" "$NAMESPACE" "$app_dir" "$WITH_SECRETS" "$WITH_DRAGONFLY" 1 1 "$APP")"
    crds_doc="$(cat <<EOF
---
$overlay_ks_schema
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app $APP-crds
  namespace: &namespace $NAMESPACE
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: *namespace
  interval: 1h
  retryInterval: 1m
  timeout: 3m
  path: ./$crds_dir
  prune: true
  wait: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
EOF
)"
    write_file "$overlay_path" "$app_doc"
    append_file "$overlay_path" "$crds_doc"
    add_namespace_resource_entry
    ;;

  parameterized)
    if [[ -z "$KEY" || -z "$INSTANCE" ]]; then
      echo "error: --shape=parameterized requires --key=<name> and --instance=<name>" >&2
      exit 1
    fi
    base_dir="$BASE_NS_DIR/$APP/app/$KEY/$INSTANCE"
    write_file "$base_dir/helmrelease.yaml" "$(render_helmrelease "$INSTANCE")"
    cat >&2 <<EOF

This shape has exactly one live precedent in this repo
(actions-runner-system/gha-runner-scale-set) and its parent app/kustomization.yaml
resources list is hand-maintained, not machine-generated here - merging a new
resources: entry into an existing, possibly hand-edited kustomization.yaml
safely is not something this script attempts. Add manually:
  $base_dir/kustomization.yaml (resources: [./helmrelease.yaml])
  a resources: entry for it in $BASE_NS_DIR/$APP/app/kustomization.yaml
See docs/app-structure.md "Parameterized instance" before using this shape -
in almost every case shape=app or shape=family is the better fit.
EOF
    ;;

  *)
    echo "error: unknown --shape=$SHAPE (want app|family|crd-split|parameterized)" >&2
    exit 1
    ;;
esac

cat <<EOF

Next steps:
  - fill in every TODO left in the generated files (image/tag, ports, uid/gid,
    1Password item keys if --secrets was used)
  - decide whether this app needs a backup component (components/volsync
    and/or components/kopiur) - that requires measuring the live file
    ownership after first deploy, so it is deliberately not auto-generated;
    see kubernetes/components/volsync/Readme.md and
    kubernetes/components/kopiur/Readme.md, and the "one component include
    covers one volume" trap in docs/app-structure.md
  - validate: mise exec -- task flux:test:all
EOF
