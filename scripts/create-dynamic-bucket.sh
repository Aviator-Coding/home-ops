#!/bin/bash

# Dynamic Bucket Creation Script for Ceph Object Store
# This script creates ObjectBucketClaims dynamically for your Rook Ceph cluster

set -euo pipefail

# Default values
DEFAULT_NAMESPACE="default"
DEFAULT_STORAGE_CLASS="ceph-bucket"
DEFAULT_OBJECT_STORE="ceph-objectstore"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS] <bucket-name> [app-name]

Create a dynamic bucket using Ceph ObjectBucketClaim

ARGUMENTS:
    bucket-name     Name for the bucket (required)
    app-name        Application name for labeling (optional, defaults to bucket-name)

OPTIONS:
    -n, --namespace NAMESPACE       Kubernetes namespace (default: $DEFAULT_NAMESPACE)
    -s, --storage-class CLASS       Storage class name (default: $DEFAULT_STORAGE_CLASS)
    -o, --object-store STORE        Object store name (default: $DEFAULT_OBJECT_STORE)
    -q, --quota-size SIZE           Maximum size quota (e.g., 10G, 100G)
    -b, --max-buckets NUM           Maximum number of buckets for user
    --max-objects NUM               Maximum number of objects
    -v, --versioning                Enable bucket versioning
    -l, --lifecycle DAYS            Enable lifecycle policy with expiration days
    -u, --create-user               Create a dedicated user for this bucket
    -d, --dry-run                   Show what would be created without applying
    -h, --help                      Show this help message

EXAMPLES:
    # Create a simple bucket
    $0 my-app-backup

    # Create a bucket with custom namespace and quota
    $0 -n production -q 50G my-app-data

    # Create a bucket with versioning and lifecycle policy
    $0 -v -l 30 my-app-logs app-logging

    # Create a bucket with dedicated user
    $0 -u -q 20G my-secure-bucket secure-app

    # Dry run to see what would be created
    $0 -d my-test-bucket
EOF
}

# Function to create ObjectBucketClaim
create_bucket_claim() {
    local bucket_name="$1"
    local app_name="$2"
    local namespace="$3"
    local storage_class="$4"
    local versioning="$5"
    local lifecycle_days="$6"
    local dry_run="$7"

    local obc_name="ceph-bucket-${bucket_name}"

    print_info "Creating ObjectBucketClaim: $obc_name"

    # Build the YAML
    local yaml_content="---
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: $obc_name
  namespace: $namespace
  labels:
    app.kubernetes.io/name: $app_name
    app.kubernetes.io/component: object-storage
    bucket.rook.io/type: dynamic
    bucket.rook.io/created-by: dynamic-script
spec:
  generateBucketName: $bucket_name
  storageClassName: $storage_class"

    # Add additional config if needed
    if [[ "$versioning" == "true" ]] || [[ -n "$lifecycle_days" ]]; then
        yaml_content="$yaml_content
  additionalConfig:"

        if [[ "$versioning" == "true" ]]; then
            yaml_content="$yaml_content
    versioning: \"true\""
        fi

        if [[ -n "$lifecycle_days" ]]; then
            yaml_content="$yaml_content
    lifecycle: |
      {
        \"Rules\": [
          {
            \"ID\": \"DeleteOldVersions\",
            \"Status\": \"Enabled\",
            \"Expiration\": {
              \"Days\": $lifecycle_days
            }
          }
        ]
      }"
        fi
    fi

    if [[ "$dry_run" == "true" ]]; then
        print_warning "DRY RUN - ObjectBucketClaim that would be created:"
        echo "$yaml_content"
    else
        echo "$yaml_content" | kubectl apply -f -
        print_success "ObjectBucketClaim created: $obc_name"
    fi
}

# Function to create CephObjectStoreUser
create_object_store_user() {
    local bucket_name="$1"
    local app_name="$2"
    local namespace="$3"
    local object_store="$4"
    local quota_size="$5"
    local max_buckets="$6"
    local max_objects="$7"
    local dry_run="$8"

    local user_name="${bucket_name}-user"

    print_info "Creating CephObjectStoreUser: $user_name"

    local yaml_content="---
apiVersion: ceph.rook.io/v1
kind: CephObjectStoreUser
metadata:
  name: $user_name
  namespace: $namespace
  labels:
    app.kubernetes.io/name: $app_name
    app.kubernetes.io/component: object-storage
    bucket.rook.io/user-for: $bucket_name
spec:
  store: $object_store
  displayName: \"$app_name S3 User\""

    # Add quotas if specified
    if [[ -n "$quota_size" ]] || [[ -n "$max_buckets" ]] || [[ -n "$max_objects" ]]; then
        yaml_content="$yaml_content
  quotas:"

        [[ -n "$max_buckets" ]] && yaml_content="$yaml_content
    maxBuckets: $max_buckets"

        [[ -n "$quota_size" ]] && yaml_content="$yaml_content
    maxSize: $quota_size"

        [[ -n "$max_objects" ]] && yaml_content="$yaml_content
    maxObjects: $max_objects"
    fi

    yaml_content="$yaml_content
  capabilities:
    user: \"read, write\"
    bucket: \"read, write, delete\"
    metadata: \"read, write\"
    usage: \"read\"
    zone: \"read\""

    if [[ "$dry_run" == "true" ]]; then
        print_warning "DRY RUN - CephObjectStoreUser that would be created:"
        echo "$yaml_content"
    else
        echo "$yaml_content" | kubectl apply -f -
        print_success "CephObjectStoreUser created: $user_name"
    fi
}

# Function to show bucket status
show_bucket_status() {
    local bucket_name="$1"
    local namespace="$2"

    local obc_name="ceph-bucket-${bucket_name}"

    print_info "Checking status of bucket: $bucket_name"

    if kubectl get obc "$obc_name" -n "$namespace" &>/dev/null; then
        echo
        print_info "ObjectBucketClaim Status:"
        kubectl get obc "$obc_name" -n "$namespace" -o wide

        echo
        print_info "Generated Secret (contains S3 credentials):"
        local secret_name=$(kubectl get obc "$obc_name" -n "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [[ -n "$secret_name" ]]; then
            kubectl get secret "$obc_name" -n "$namespace" -o yaml 2>/dev/null || print_warning "Secret not yet created"
        fi

        echo
        print_info "Generated ConfigMap (contains bucket info):"
        kubectl get configmap "$obc_name" -n "$namespace" -o yaml 2>/dev/null || print_warning "ConfigMap not yet created"
    else
        print_error "ObjectBucketClaim $obc_name not found in namespace $namespace"
        return 1
    fi
}

# Parse command line arguments
NAMESPACE="$DEFAULT_NAMESPACE"
STORAGE_CLASS="$DEFAULT_STORAGE_CLASS"
OBJECT_STORE="$DEFAULT_OBJECT_STORE"
QUOTA_SIZE=""
MAX_BUCKETS=""
MAX_OBJECTS=""
VERSIONING="false"
LIFECYCLE_DAYS=""
CREATE_USER="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -s|--storage-class)
            STORAGE_CLASS="$2"
            shift 2
            ;;
        -o|--object-store)
            OBJECT_STORE="$2"
            shift 2
            ;;
        -q|--quota-size)
            QUOTA_SIZE="$2"
            shift 2
            ;;
        -b|--max-buckets)
            MAX_BUCKETS="$2"
            shift 2
            ;;
        --max-objects)
            MAX_OBJECTS="$2"
            shift 2
            ;;
        -v|--versioning)
            VERSIONING="true"
            shift
            ;;
        -l|--lifecycle)
            LIFECYCLE_DAYS="$2"
            shift 2
            ;;
        -u|--create-user)
            CREATE_USER="true"
            shift
            ;;
        -d|--dry-run)
            DRY_RUN="true"
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        -*)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

# Check required arguments
if [[ $# -lt 1 ]]; then
    print_error "Bucket name is required"
    show_usage
    exit 1
fi

BUCKET_NAME="$1"
APP_NAME="${2:-$BUCKET_NAME}"

# Validate bucket name
if [[ ! "$BUCKET_NAME" =~ ^[a-z0-9][a-z0-9-]*[a-z0-9]$ ]]; then
    print_error "Invalid bucket name. Must contain only lowercase letters, numbers, and hyphens, and cannot start or end with a hyphen"
    exit 1
fi

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    print_error "kubectl is required but not installed"
    exit 1
fi

# Check if namespace exists
if [[ "$DRY_RUN" != "true" ]] && ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    print_error "Namespace '$NAMESPACE' does not exist"
    exit 1
fi

print_info "Creating dynamic bucket with the following configuration:"
echo "  Bucket Name: $BUCKET_NAME"
echo "  App Name: $APP_NAME"
echo "  Namespace: $NAMESPACE"
echo "  Storage Class: $STORAGE_CLASS"
echo "  Object Store: $OBJECT_STORE"
echo "  Versioning: $VERSIONING"
[[ -n "$LIFECYCLE_DAYS" ]] && echo "  Lifecycle: $LIFECYCLE_DAYS days"
[[ -n "$QUOTA_SIZE" ]] && echo "  Quota Size: $QUOTA_SIZE"
[[ -n "$MAX_BUCKETS" ]] && echo "  Max Buckets: $MAX_BUCKETS"
[[ -n "$MAX_OBJECTS" ]] && echo "  Max Objects: $MAX_OBJECTS"
echo "  Create User: $CREATE_USER"
echo "  Dry Run: $DRY_RUN"
echo

# Create the bucket claim
create_bucket_claim "$BUCKET_NAME" "$APP_NAME" "$NAMESPACE" "$STORAGE_CLASS" "$VERSIONING" "$LIFECYCLE_DAYS" "$DRY_RUN"

# Create user if requested
if [[ "$CREATE_USER" == "true" ]]; then
    create_object_store_user "$BUCKET_NAME" "$APP_NAME" "$NAMESPACE" "$OBJECT_STORE" "$QUOTA_SIZE" "$MAX_BUCKETS" "$MAX_OBJECTS" "$DRY_RUN"
fi

# Show status if not dry run
if [[ "$DRY_RUN" != "true" ]]; then
    echo
    print_info "Waiting for bucket to be ready..."
    sleep 5
    show_bucket_status "$BUCKET_NAME" "$NAMESPACE"

    echo
    print_success "Dynamic bucket creation completed!"
    print_info "To get S3 credentials, run:"
    echo "  kubectl get secret ceph-bucket-$BUCKET_NAME -n $NAMESPACE -o yaml"
    print_info "To get bucket information, run:"
    echo "  kubectl get configmap ceph-bucket-$BUCKET_NAME -n $NAMESPACE -o yaml"
fi
