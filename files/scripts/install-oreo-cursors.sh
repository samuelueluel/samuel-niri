#!/bin/bash
# Installs oreo cursors from pre-compiled GitHub repo (not packaged for Fedora)
set -euo pipefail

REPO="milkmadedev/oreo-cursors-compiled"
WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

dnf install -y git

git clone --depth=1 "https://github.com/$REPO.git" "$WORK_DIR/oreo-cursors"

# Install all cursor variants to system icons directory
find "$WORK_DIR/oreo-cursors" -maxdepth 1 -type d -name "oreo*" \
    -exec cp -r {} /usr/share/icons/ \;
