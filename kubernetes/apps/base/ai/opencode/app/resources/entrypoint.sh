#!/bin/bash
set -euo pipefail

git config --global user.name "Sascha Krumbach"
git config --global user.email "sascha.s.krumbach@gmail.com"
git config --global init.defaultBranch main
git config --global credential.helper store
git config --global --add safe.directory '*'

# GITHUB_TOKEN is optional: home-ops is public, so an anonymous clone works
# without it. When set (see this app's README.md), it also enables push.
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  umask 077
  printf 'https://x-access-token:%s@github.com\n' "${GITHUB_TOKEN}" > "${HOME}/.git-credentials"
  umask 022
fi

mkdir -p "${HOME}/projects"
if [[ ! -d "${HOME}/projects/home-ops/.git" ]]; then
  git clone --filter=blob:none https://github.com/Aviator-Coding/home-ops.git "${HOME}/projects/home-ops" \
    || echo "WARN: home-ops clone failed; clone it from a session instead"
fi

cd "${HOME}/projects"
exec opencode serve --hostname 0.0.0.0 --port 4096
