#!/usr/bin/env bash
# Installs Microsoft TrueType core fonts (Arial, Times New Roman, Courier New,
# Georgia, Verdana, Trebuchet MS, etc.) from SourceForge.
# Equivalent of Arch AUR's ttf-ms-fonts package.
set -euo pipefail

FONT_DIR="/usr/share/fonts/msttcore"
WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

dnf install -y --setopt=install_weak_deps=False cabextract curl

mkdir -p "$FONT_DIR"
cd "$WORK_DIR"

# Microsoft TrueType core font packages hosted on SourceForge
FONT_PKGS=(
    andale32.exe arial32.exe arialb32.exe comic32.exe
    courie32.exe georgi32.exe impact32.exe times32.exe
    trebuc32.exe verdan32.exe webdin32.exe
)

BASE_URL="https://downloads.sourceforge.net/corefonts"

for pkg in "${FONT_PKGS[@]}"; do
    echo "Downloading $pkg..."
    curl -fsSLo "$pkg" "$BASE_URL/$pkg" || {
        echo "Warning: failed to download $pkg, skipping"
        continue
    }
    cabextract -q -d fonts/ "$pkg"
done

# Install extracted TTF files
find fonts/ -iname "*.ttf" -exec install -Dm644 {} "$FONT_DIR/" \;

# Rebuild font cache
fc-cache -f "$FONT_DIR"

echo "Microsoft core fonts installed to $FONT_DIR"
