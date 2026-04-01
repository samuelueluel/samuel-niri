#!/usr/bin/env bash
# Configures greetd to use tuigreet, launching a niri Wayland session.
set -euo pipefail

# Create the greeter system user greetd requires
useradd -r -d /var/lib/greeter -s /sbin/nologin greeter || true

mkdir -p /etc/greetd

cat > /etc/greetd/config.toml << 'EOF'
[terminal]
vt = 1

[default_session]
command = "tuigreet --time --remember --remember-session --sessions /usr/share/wayland-sessions"
user = "greeter"
EOF

echo "greetd configured with tuigreet."
