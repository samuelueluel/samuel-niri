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

# ── 2. Configure chezmoi (reads .chezmoi.toml.tmpl from source dir) ──────────
chezmoi init --source="$HOME/dotfiles"

# ── 3. Pre-create directories chezmoi won't create on its own ────────────────
mkdir -p \
    ~/.local/bin \
    ~/.local/share/applications \
    ~/.local/share/zsh/plugins \
    ~/.config \
    ~/.gnome2/accels \
    ~/.claude \
    ~/.ssh

chmod 700 ~/.ssh

# ── 3.5. Install Zsh Plugins (Powerlevel10k, fzf-tab) ────────────────────────
echo "Installing external zsh plugins..."
if [[ ! -d ~/.local/share/zsh/plugins/powerlevel10k ]]; then
    git clone --depth=1 https://github.com/romkatv/powerlevel10k.git ~/.local/share/zsh/plugins/powerlevel10k
fi
if [[ ! -d ~/.local/share/zsh/plugins/fzf-tab ]]; then
    git clone --depth=1 https://github.com/Aloxaf/fzf-tab ~/.local/share/zsh/plugins/fzf-tab
fi

# ── 4. Apply dotfiles ─────────────────────────────────────────────────────────
# chezmoi will:
#   - apply all config files (niri, waybar, kitty, alacritty, zsh, etc.)
#   - run run_once_load-nemo-dconf.sh to set Nemo preferences
#   - apply vivaldi launcher .desktop files to ~/.local/share/applications/
echo "Applying dotfiles via chezmoi..."
chezmoi apply --force

# ── 5. Refresh desktop file MIME database ────────────────────────────────────
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

# ── 5.5. Configure Flatpak Permissions ───────────────────────────────────────
echo "Configuring Flatpak permissions for Vivaldi..."
flatpak override --user --filesystem=~/.config/vivaldi-casual --filesystem=~/.config/vivaldi-work --filesystem=~/.config/vivaldi-llm com.vivaldi.Vivaldi || true

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
echo "  - Vivaldi profiles (Preferences / contextmenu.json) — copy from:"
echo "    ~/system_config_git/vivaldi/{casual,work,llm}/ to ~/.config/vivaldi-{casual,work,llm}/Default/"
echo "  - Wallpapers — cp -r ~/system_config_git/Wallpapers ~/Pictures/Wallpapers"
echo "  - Dropbox — sign in"
echo "  - EasyEffects — presets are applied; open the app to confirm they loaded"
