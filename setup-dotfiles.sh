#!/usr/bin/env bash
# First-run dotfiles setup for samuel-niri work laptop image.
# Run once after first login: bash ~/work-image/setup-dotfiles.sh
#
# Assumes: ~/dotfiles has been cloned (SSH key must be set up first).
# Assumes: ~/work-image (this repo) has been cloned.

set -euo pipefail

DOTFILES="$HOME/dotfiles"

# ── 1. Verify dotfiles repo is present ───────────────────────────────────────
if [[ ! -d "$DOTFILES/.git" ]]; then
    echo "ERROR: ~/dotfiles not found. Clone it first:"
    echo "  git clone git@github.com:samuelueluel/dotfiles.git ~/dotfiles"
    exit 1
fi

# ── 2. Configure chezmoi to use ~/dotfiles as source ─────────────────────────
mkdir -p ~/.config/chezmoi
cat > ~/.config/chezmoi/chezmoi.toml <<'EOF'
sourceDir = "/home/samuel/dotfiles"
EOF

# ── 3. Pre-create directories chezmoi won't create on its own ────────────────
mkdir -p \
    ~/.local/bin \
    ~/.local/share/applications \
    ~/.config \
    ~/.gnome2/accels \
    ~/.claude \
    ~/.ssh

chmod 700 ~/.ssh

# ── 4. Apply dotfiles ─────────────────────────────────────────────────────────
# chezmoi will:
#   - apply all config files (niri, waybar, kitty, alacritty, zsh, etc.)
#   - run run_once_load-nemo-dconf.sh to set Nemo preferences
#   - apply vivaldi launcher .desktop files to ~/.local/share/applications/
echo "Applying dotfiles via chezmoi..."
chezmoi apply

# ── 5. Refresh desktop file MIME database ────────────────────────────────────
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

# ── 6. Set zsh as default shell ──────────────────────────────────────────────
ZSH_PATH="$(command -v zsh)"
if [[ "$SHELL" != "$ZSH_PATH" ]]; then
    echo "Setting zsh as default shell (requires password)..."
    chsh -s "$ZSH_PATH"
fi

echo ""
echo "Done. If shell was changed, log out and back in for it to take effect."
echo ""
echo "Manual steps still needed:"
echo "  - Vivaldi profiles (Preferences / contextmenu.json) — launch each profile"
echo "    once, then copy from system_config_git/vivaldi/{casual,work,llm}/"
echo "  - Vivaldi launchers: Exec lines in ~/.local/share/applications/Vivaldi*.desktop"
echo "    need updating for Flatpak (replace /usr/bin/vivaldi with flatpak run com.vivaldi.Vivaldi)"
echo "  - Wallpapers — copy from ~/work-image/files/Wallpapers/ to ~/Pictures/Wallpapers/"
echo "  - Dropbox — sign in"
echo "  - EasyEffects — presets are applied; open the app to confirm they loaded"
