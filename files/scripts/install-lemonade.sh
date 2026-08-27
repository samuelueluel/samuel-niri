#!/usr/bin/env bash
# Installs the latest Lemonade Server (AMD NPU/GPU LLM inference) from the
# official Fedora 44 RPM on GitHub releases. Queries releases to find the newest
# release containing an fc44 x86_64 RPM asset, so temporary upstream release asset
# gaps (e.g. tag published before Linux packaging completes) don't break the build.
# Provides the `lemonade-server` CLI + systemd integration, used for the
# XDNA2 NPU path (Zed autocomplete + RAG embeddings) on Strix Halo.
set -euo pipefail

echo "Installing Lemonade Server (latest fc44 RPM)..."

# Query GitHub releases API for the latest release containing an fc44 x86_64 RPM asset
RPM_URL="$(curl -fsSL --retry 5 --retry-delay 3 https://api.github.com/repos/lemonade-sdk/lemonade/releases | jq -r '[.[] | .assets[]? | select(.name | test("fc44.*x86_64\\.rpm$")) | .browser_download_url][0] // empty')"

if [ -z "$RPM_URL" ]; then
  echo "ERROR: No release with an fc44 x86_64 RPM found at https://github.com/lemonade-sdk/lemonade/releases." >&2
  echo "Upstream may have changed its asset naming or API structure; check https://github.com/lemonade-sdk/lemonade/releases/latest" >&2
  exit 1
fi

echo "Found: ${RPM_URL}"
dnf install -y --setopt=install_weak_deps=False "${RPM_URL}"

echo "Lemonade Server installed: $(command -v lemonade-server || echo 'NOT on PATH')"
