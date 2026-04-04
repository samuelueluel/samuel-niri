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

# Pre-create the directory and files auto-cpufreq tries to write to at runtime
# Since /usr is immutable at runtime, we must bake these into the image layer.
# Note: In the image build environment, /usr is writable.
mkdir -p /usr/local/share/auto-cpufreq/scripts
install -m 755 scripts/cpufreqctl.sh /usr/local/share/auto-cpufreq/scripts/cpufreqctl.sh

# The Python code checks for this file and tries to copy if missing.
# We pre-install it so the check passes and the copy is skipped.
mkdir -p /usr/local/bin
install -m 755 scripts/cpufreqctl.sh /usr/local/bin/cpufreqctl.auto-cpufreq

# We also need to patch the Python code because even if the file exists, 
# it might still try to run the copy function which fails if the directory 
# is considered read-only by some OS abstractions.
# Actually, the check 'if not os.path.isfile("/usr/local/bin/cpufreqctl.auto-cpufreq")' 
# should be enough if the file is there.
