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
# Using --break-system-packages because we are building a container image where this is intentional.
pip3 install --prefix=/usr --break-system-packages .

# Install missing scripts that the python package expects at specific paths
install -Dm755 scripts/cpufreqctl.sh /usr/share/auto-cpufreq/scripts/cpufreqctl.sh

# Install the systemd service file (the installer would normally do this
# via systemctl, which doesn't work in a container build)
install -Dm644 scripts/auto-cpufreq.service \
    /usr/lib/systemd/system/auto-cpufreq.service

# Patch the service file to use /usr paths instead of /opt venv
# Using absolute path for auto-cpufreq and ensuring PYTHONPATH points to where pip installed it
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
sed -i "s|ExecStart=.*|ExecStart=/usr/bin/auto-cpufreq --daemon|" /usr/lib/systemd/system/auto-cpufreq.service
sed -i "s|WorkingDirectory=.*|WorkingDirectory=/tmp|" /usr/lib/systemd/system/auto-cpufreq.service
sed -i "s|Environment=PYTHONPATH=.*|Environment=PYTHONPATH=/usr/lib/python${PYTHON_VERSION}/site-packages|" /usr/lib/systemd/system/auto-cpufreq.service

# Pre-create the directory auto-cpufreq tries to write to at runtime
# Since /usr is immutable, we want to ensure it has what it needs.
mkdir -p /usr/share/auto-cpufreq/scripts
install -m 755 scripts/cpufreqctl.sh /usr/share/auto-cpufreq/scripts/cpufreqctl.sh
