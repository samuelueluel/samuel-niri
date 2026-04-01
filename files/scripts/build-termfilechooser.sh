#!/usr/bin/env bash
# Builds and installs xdg-desktop-portal-termfilechooser (hunkyburrito fork).
# Provides Yazi as the system file picker via the XDG portal protocol.
# Not packaged for Fedora; built from source with Cargo (Rust).
set -euo pipefail

REPO_URL="https://github.com/hunkyburrito/xdg-desktop-portal-termfilechooser"
SHARE_DIR="/usr/share/xdg-desktop-portal-termfilechooser"
BUILD_DIR="$(mktemp -d)"
trap "rm -rf '$BUILD_DIR'" EXIT

# Install build-time dependencies only
dnf install -y --setopt=install_weak_deps=False rust cargo git

git clone --depth=1 "$REPO_URL" "$BUILD_DIR/src"
cd "$BUILD_DIR/src"
cargo build --release

# Binary path matches the upstream installed location
install -Dm755 target/release/xdg-desktop-portal-termfilechooser \
    /usr/lib/xdg-desktop-portal-termfilechooser

# Wrapper scripts (yazi-wrapper.sh and others ship with the repo)
mkdir -p "$SHARE_DIR"
find contrib -name "*-wrapper.sh" -exec \
    install -Dm755 {} "$SHARE_DIR/" \;

# Default config — use yazi, open with kitty (both are in the image)
cat > "$SHARE_DIR/config" << 'EOF'
[filechooser]
cmd=yazi-wrapper.sh
default_dir=$HOME
env=TERMCMD=kitty --title 'File Picker'
open_mode=suggested
save_mode=suggested
EOF

# Portal descriptor
install -Dm644 contrib/termfilechooser.portal \
    /usr/share/xdg-desktop-portal/portals/termfilechooser.portal

# D-Bus service activation file
install -Dm644 \
    contrib/org.freedesktop.impl.portal.desktop.termfilechooser.service \
    /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.termfilechooser.service

# Systemd user service
install -Dm644 \
    contrib/xdg-desktop-portal-termfilechooser.service \
    /usr/lib/systemd/user/xdg-desktop-portal-termfilechooser.service

# Remove build toolchain only — do NOT autoremove, it can pull out
# packages installed by earlier dnf modules
dnf remove -y rust cargo
