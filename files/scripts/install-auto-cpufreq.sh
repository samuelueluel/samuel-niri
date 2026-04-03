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

# Install missing scripts that the python package expects at specific paths
install -Dm755 scripts/cpufreqctl.sh /usr/share/auto-cpufreq/scripts/cpufreqctl.sh

# Install the systemd service file (the installer would normally do this
# via systemctl, which doesn't work in a container build)
install -Dm644 scripts/auto-cpufreq.service \
    /usr/lib/systemd/system/auto-cpufreq.service

# Patch the service file to use /usr paths instead of /opt venv
sed -i 's|/opt/auto-cpufreq/venv/bin/python /opt/auto-cpufreq/venv/bin/auto-cpufreq|/usr/bin/auto-cpufreq|' /usr/lib/systemd/system/auto-cpufreq.service
sed -i 's|WorkingDirectory=/opt/auto-cpufreq/venv|WorkingDirectory=/tmp|' /usr/lib/systemd/system/auto-cpufreq.service
sed -i 's|Environment=PYTHONPATH=/opt/auto-cpufreq|Environment=PYTHONPATH=/usr/lib/python3.14/site-packages|' /usr/lib/systemd/system/auto-cpufreq.service
# Note: python version might change, but this is a better guess than /opt

echo "auto-cpufreq installed."
