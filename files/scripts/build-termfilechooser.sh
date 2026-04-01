#!/usr/bin/env bash
# Builds and installs xdg-desktop-portal-termfilechooser (hunkyburrito fork).
# Provides Yazi as the system file picker via the XDG portal protocol.
# C/Meson project — not packaged for Fedora.
set -euo pipefail

REPO_URL="https://github.com/hunkyburrito/xdg-desktop-portal-termfilechooser"
BUILD_DIR="$(mktemp -d)"
trap "rm -rf '$BUILD_DIR'" EXIT

# Build-time dependencies
dnf install -y --setopt=install_weak_deps=False \
    meson ninja-build gcc \
    inih-devel systemd-devel scdoc git

git clone --depth=1 "$REPO_URL" "$BUILD_DIR/src"
cd "$BUILD_DIR/src"

meson setup --prefix=/usr "$BUILD_DIR/build"
ninja -C "$BUILD_DIR/build"
ninja -C "$BUILD_DIR/build" install

# Default config — use yazi, open with kitty (both are in the image)
SHARE_DIR="/usr/share/xdg-desktop-portal-termfilechooser"
mkdir -p "$SHARE_DIR"
cat > "$SHARE_DIR/config" << 'EOF'
[filechooser]
cmd=yazi-wrapper.sh
default_dir=$HOME
env=TERMCMD=kitty --title 'File Picker'
open_mode=suggested
save_mode=suggested
EOF

# Remove build toolchain
dnf remove -y meson ninja-build gcc inih-devel systemd-devel scdoc
