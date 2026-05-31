#!/usr/bin/env -S just --justfile
set quiet
set shell := ['bash', '-euo', 'pipefail', '-c']

# Talos Recipes
mod talos "talos"

# Bootstrap Recipes
mod bootstrap "bootstrap"

# Kubernetes Recipes
mod kube "kubernetes"

[private]
default:
    just -l

# Structured log line (level message key value ...) via gum
[private]
log lvl msg *args:
    gum log -t rfc3339 -s -l "{{ lvl }}" "{{ msg }}" {{ args }}

# Render a *.j2 template with minijinja, then resolve ref+op:// secrets via vals
[private]
template file *args:
    minijinja-cli "{{ file }}" {{ args }} | vals eval -f -
