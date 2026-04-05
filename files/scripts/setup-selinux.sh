#!/usr/bin/env bash
# Installs a custom SELinux policy module allowing greetd (xdm_t) to
# start/status systemd unit files. This is needed if niri-session or the
# greetd PAM stack triggers systemctl --user under the xdm_t context.
set -euo pipefail

echo "Installing custom SELinux policy: xdm_t -> systemd_unit_file_t:service..."

dnf install -y checkpolicy policycoreutils 2>&1 | tail -5

WORK_DIR="$(mktemp -d)"
trap "rm -rf '$WORK_DIR'" EXIT

cat > "$WORK_DIR/niri_greetd.te" << 'EOF'
module niri_greetd 1.0;

require {
    type xdm_t;
    type systemd_unit_file_t;
    class service { start status };
}

allow xdm_t systemd_unit_file_t:service { start status };
EOF

checkmodule -M -m -o "$WORK_DIR/niri_greetd.mod" "$WORK_DIR/niri_greetd.te"
semodule_package -o "$WORK_DIR/niri_greetd.pp" -m "$WORK_DIR/niri_greetd.mod"

if semodule -i "$WORK_DIR/niri_greetd.pp" 2>&1; then
    echo "SELinux policy installed via semodule."
else
    echo "WARNING: semodule failed (expected in some container builds)."
    echo "Falling back to semanage permissive for xdm_t..."
    dnf install -y policycoreutils-python-utils 2>&1 | tail -3
    if semanage permissive -a xdm_t 2>&1; then
        echo "xdm_t set to permissive mode."
    else
        echo "ERROR: Both semodule and semanage failed. SELinux policy not installed."
        echo "You may need to run 'sudo semanage permissive -a xdm_t' on the host after deployment."
        exit 1
    fi
fi
