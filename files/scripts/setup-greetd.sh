#!/usr/bin/env bash
# Configures greetd to use tuigreet, launching a niri Wayland session.
set -euo pipefail

# Use the 'greetd' system user provided by the Fedora package.
# We ensure it is in the right groups via sysusers.d and has a cache dir via tmpfiles.d.
# In Fedora Atomic, sysusers.d might not apply to pre-existing users, so we add them here.
echo "Adding greetd user to video and input groups..."
getent group video >/dev/null && usermod -aG video greetd || echo "Group 'video' not found, skipping."
getent group input >/dev/null && usermod -aG input greetd || echo "Group 'input' not found, skipping."

mkdir -p /etc/greetd

# Determine the session directory (Fedora uses /usr/share/wayland-sessions)
SESSION_DIR="/usr/share/wayland-sessions"
TUIGREET_CMD="tuigreet --time --remember --remember-session"

if [ -d "$SESSION_DIR" ]; then
    TUIGREET_CMD="$TUIGREET_CMD --sessions $SESSION_DIR"
fi

cat > /etc/greetd/config.toml << EOF
[terminal]
vt = 2

[default_session]
command = "$TUIGREET_CMD"
user = "greetd"
EOF

# Ensure the cache directory for tuigreet's --remember flags exists with correct ownership
mkdir -p /var/cache/tuigreet
chown greetd:greetd /var/cache/tuigreet
chmod 0755 /var/cache/tuigreet

echo "greetd configured with tuigreet on VT 2."
