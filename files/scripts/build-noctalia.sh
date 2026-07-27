#!/usr/bin/env bash
# Builds and installs Noctalia v5 from the latest GitHub release tarball.
# Replaces the lionheartp/Hyprland COPR RPM.
set -euo pipefail

BUILD_DIR="$(mktemp -d)"
trap "rm -rf '$BUILD_DIR'" EXIT

# Fetch latest release tarball URL from noctalia-dev/noctalia
RELEASE_URL="$(curl -sL https://api.github.com/repos/noctalia-dev/noctalia/releases/latest | jq -r '.assets[] | select(.name | endswith(".tar.gz")) | .browser_download_url' | head -n 1)"

echo "Downloading Noctalia release from: ${RELEASE_URL}"
curl -sL "${RELEASE_URL}" | tar -xzf - -C "$BUILD_DIR"

cd "$BUILD_DIR"/noctalia-*

# PREFIX=/usr: Fedora Atomic's /usr/local/ is a writable overlay, not part of the image.
meson setup build-release --buildtype=release -Db_lto=true --prefix=/usr
meson compile -C build-release
meson install -C build-release

echo "Done: $(find /usr/bin/noctalia* /usr/share/noctalia* -maxdepth 0 2>/dev/null | tr '\n' ' ')"

