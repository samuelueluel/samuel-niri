#!/usr/bin/env bash
# Installs auto-cpufreq from source using pip.
# The principis/auto-cpufreq COPR does not publish Fedora 43 builds.
# We avoid the auto-cpufreq-installer because it calls `systemctl enable`
# inside the container, which fails. Instead: pip install + manual service file.
set -euo pipefail

WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

dnf install -y --setopt=install_weak_deps=False git python3-pip

git clone --depth=1 https://github.com/AdnanHodzic/auto-cpufreq.git "$WORK_DIR/auto-cpufreq"
cd "$WORK_DIR/auto-cpufreq"

# Install to /usr so it's part of the immutable image layer
pip3 install --prefix=/usr .

# Install the systemd service file (the installer would normally do this
# via systemctl, which doesn't work in a container build)
install -Dm644 scripts/auto-cpufreq.service \
    /usr/lib/systemd/system/auto-cpufreq.service

echo "auto-cpufreq installed."
