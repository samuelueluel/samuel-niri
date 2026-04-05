#!/usr/bin/env bash
# Configures SDDM as the display manager for a niri Wayland session.
set -euo pipefail

mkdir -p /etc/sddm.conf.d

# Create the sddm system user/group directly.
# systemd-sysusers is unreliable in container/ostree image builds — the user
# may not end up in the committed /etc/passwd, causing a black screen at boot.
if ! getent passwd sddm &>/dev/null; then
    useradd -r -d /var/lib/sddm -s /sbin/nologin -c "SDDM Greeter Account" sddm
fi

# Create directories the sddm RPM owns, with correct ownership.
# /var/lib/sddm persists across boots; /run/sddm is handled by tmpfiles at runtime.
install -d -m 1770 -o sddm -g sddm /var/lib/sddm
systemd-tmpfiles --create /usr/lib/tmpfiles.d/sddm.conf 2>/dev/null || true

# Minimal config — SDDM auto-detects sessions from
# /usr/share/wayland-sessions and /usr/share/xsessions.
# gnome-keyring auto-unlocks via systemd user session (no PAM changes needed).
cat > /etc/sddm.conf.d/10-samuel.conf << 'EOF'
[General]
HideUsers=false
EOF

echo "SDDM configured."
