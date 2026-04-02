#!/usr/bin/env bash
# Builds and installs a patched niri_window_buttons waybar CFFI module.
# Patch: set_halign(Center) on the icon box when titles are hidden, so the
# focused-window highlight rectangle is correctly centered around the icon.
# Not packaged for Fedora; built from source (Rust).
set -euo pipefail

VERSION="0.4.0"
REPO_URL="https://github.com/adelmonte/niri_window_buttons"
BUILD_DIR="$(mktemp -d)"
trap "rm -rf '$BUILD_DIR'" EXIT

# Build-time dependencies
dnf install -y --setopt=install_weak_deps=False \
    cargo rust \
    gtk3-devel \
    glib2-devel \
    git

git clone --depth=1 --branch "v${VERSION}" "$REPO_URL" "$BUILD_DIR/src"

# Patch: center icon layout box when window titles are hidden
sed -i \
    's/let layout_box = gtk::Box::new(Orientation::Horizontal, icon_gap);/let layout_box = gtk::Box::new(Orientation::Horizontal, icon_gap);\n        if !display_titles {\n            layout_box.set_halign(gtk::Align::Center);\n        }/' \
    "$BUILD_DIR/src/src/widget.rs"

cd "$BUILD_DIR/src"
cargo build --release

install -Dm755 target/release/libniri_window_buttons.so \
    /usr/lib/waybar/libniri_window_buttons.so

# Remove build toolchain
dnf remove -y cargo rust gtk3-devel glib2-devel
