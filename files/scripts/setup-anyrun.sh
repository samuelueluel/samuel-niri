#!/usr/bin/env bash
# Sets up default anyrun configuration in /etc/skel for new users.
set -euo pipefail

SKEL_DIR="/etc/skel/.config/anyrun"
mkdir -p "$SKEL_DIR"

# Main config.ron
cat > "$SKEL_DIR/config.ron" << 'EOF'
Config(
  x: Fraction(0.5),
  y: Fraction(0.3),
  width: Fraction(0.3),
  height: Fraction(0.0),
  hide_on_clicked: true,
  layer: Overlay,
  hide_plugin_info: true,
  close_on_click: true,
  show_results_immediately: true,
  max_entries: None,

  plugins: [
    "/usr/lib64/anyrun/plugins/libapplications.so",
    "/usr/lib64/anyrun/plugins/libshell.so",
    "/usr/lib64/anyrun/plugins/libniri_focus.so",
    "/usr/lib64/anyrun/plugins/libsymbols.so",
    "/usr/lib64/anyrun/plugins/librink.so",
    "/usr/lib64/anyrun/plugins/libwebsearch.so",
  ],
)
EOF

# Default style.css (Noctalia theme)
cat > "$SKEL_DIR/style.css" << 'EOF'
window {
  background: rgba(0, 0, 0, 0);
}

#main {
  background: #a14763;
  border: 2px solid #00b6c2;
  border-radius: 12px;
  padding: 8px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
}

#entry {
  background: rgba(0, 0, 0, 0.2);
  color: #00b6c2;
  border-radius: 8px;
  padding: 8px;
  font-size: 1.2rem;
}

#results {
  margin-top: 8px;
}

#plugin {
  color: #e7bac2;
  font-weight: bold;
}

#match {
  padding: 4px;
  border-radius: 4px;
  color: #b1b9f9;
}

#match:selected {
  background: rgba(0, 182, 194, 0.5);
  color: #b1b9f9;
}
EOF

# Niri focus plugin config
cat > "$SKEL_DIR/niri_focus.ron" << 'EOF'
Config(
  max_entries: 5,
)
EOF

echo "anyrun default configuration installed to $SKEL_DIR."
