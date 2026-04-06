#!/usr/bin/env bash
# Builds and installs Anyrun Wayland launcher from source.
# The available COPR repositories are currently broken or outdated.
set -euo pipefail

VERSION="v25.12.0"
REPO_URL="https://github.com/anyrun-org/anyrun.git"
PROVIDER_URL="https://github.com/anyrun-org/anyrun-provider.git"
BUILD_DIR="$(mktemp -d)"
trap "rm -rf '$BUILD_DIR'" EXIT

# Build-time dependencies
dnf install -y --setopt=install_weak_deps=False \
    cargo rust \
    gtk4-devel \
    gtk4-layer-shell-devel \
    pango-devel \
    cairo-devel \
    gdk-pixbuf2-devel \
    glib2-devel \
    git

git clone --depth=1 --branch "$VERSION" "$REPO_URL" "$BUILD_DIR/src"
git clone --depth=1 --branch "$VERSION" "$PROVIDER_URL" "$BUILD_DIR/provider"

cd "$BUILD_DIR/src"
cargo build --release

cd "$BUILD_DIR/provider"
cargo build --release

# Install binary
install -Dm755 "$BUILD_DIR/src/target/release/anyrun" /usr/bin/anyrun
install -Dm755 "$BUILD_DIR/provider/target/release/anyrun-provider" /usr/bin/anyrun-provider

# Install plugins
mkdir -p /usr/lib64/anyrun/plugins
install -Dm755 "$BUILD_DIR/src/target/release"/lib*.so -t /usr/lib64/anyrun/plugins/

# Remove build toolchain
dnf remove -y cargo rust gtk4-devel gtk4-layer-shell-devel pango-devel cairo-devel gdk-pixbuf2-devel glib2-devel
