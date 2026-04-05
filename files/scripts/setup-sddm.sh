#!/usr/bin/env bash
# Configures SDDM as the display manager for a niri Wayland session.
set -euo pipefail

mkdir -p /etc/sddm.conf.d

# Minimal config — SDDM auto-detects sessions from
# /usr/share/wayland-sessions and /usr/share/xsessions.
# gnome-keyring auto-unlocks via systemd user session (no PAM changes needed).
cat > /etc/sddm.conf.d/10-samuel.conf << 'EOF'
[General]
HideUsers=false
EOF

echo "SDDM configured."
