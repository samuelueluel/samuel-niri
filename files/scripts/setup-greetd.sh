#!/usr/bin/env bash
# Configures greetd to use tuigreet, launching a niri Wayland session.
set -euo pipefail

# Use the 'greetd' system user provided by the Fedora package.
# We ensure it is in the right groups via sysusers.d and has a cache dir via tmpfiles.d.

mkdir -p /etc/greetd

cat > /etc/greetd/config.toml << 'EOF'
[terminal]
vt = 2

[default_session]
command = "tuigreet --time --remember --remember-session --sessions /usr/share/wayland-sessions"
user = "greetd"
EOF

# Ensure the cache directory for tuigreet's --remember flags exists with correct ownership
mkdir -p /var/cache/tuigreet
chown greetd:greetd /var/cache/tuigreet
chmod 0755 /var/cache/tuigreet

echo "greetd configured with tuigreet on VT 2."
