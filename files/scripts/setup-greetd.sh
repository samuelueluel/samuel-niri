#!/usr/bin/env bash
# Configures greetd to use gtkgreet (via cage), launching a niri Wayland session.
set -euo pipefail

# In Fedora Atomic, sysusers.d might not apply to pre-existing users, so add groups here.
echo "Adding greetd user to video, render, and input groups..."
for grp in video render input; do
    if getent group "$grp" >/dev/null; then
        usermod -aG "$grp" greetd
    else
        echo "Group '$grp' not found, skipping."
    fi
done

mkdir -p /etc/greetd

cat > /etc/greetd/gtkgreet.css << 'EOF'
window {
    background-color: #914059;
    color: #000000;
}
EOF

cat > /etc/greetd/config.toml << 'EOF'
[terminal]
vt = 2

[default_session]
command = "cage -s -- env GTK_THEME=adw-gtk3-dark gtkgreet -s /etc/greetd/gtkgreet.css"
user = "greetd"
EOF

echo "greetd configured with gtkgreet+cage on VT 2."
