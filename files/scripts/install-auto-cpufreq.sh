#!/usr/bin/env bash
# Installs auto-cpufreq from source using the official installer.
# The principis/auto-cpufreq COPR does not publish Fedora 43 builds.
set -euo pipefail

WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

dnf install -y --setopt=install_weak_deps=False git python3-pip

git clone --depth=1 https://github.com/AdnanHodzic/auto-cpufreq.git "$WORK_DIR/auto-cpufreq"
cd "$WORK_DIR/auto-cpufreq"

# The installer handles binary install, service files, and daemon setup.
# --install runs non-interactively in a container environment.
./auto-cpufreq-installer --install

echo "auto-cpufreq $(auto-cpufreq --version) installed."
