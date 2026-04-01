#!/usr/bin/env bash
# Builds and installs nwg-look from source.
# dustee071/unstable-apps COPR does not publish Fedora 43 builds.
set -euo pipefail

WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

dnf install -y --setopt=install_weak_deps=False golang git \
    glib2-devel gtk3-devel

git clone --depth=1 https://github.com/nwg-piotr/nwg-look.git "$WORK_DIR/nwg-look"
cd "$WORK_DIR/nwg-look"

make build
make install

dnf remove -y golang glib2-devel gtk3-devel

echo "nwg-look installed."
