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
    ~/.ssh \
    ~/.npm-global/bin

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
echo "Applying dotfiles via chezmoi..."
chezmoi apply --force

# ── 4.5. Clean up conflicting Vivaldi generated desktop files ────────────────
rm -f ~/.local/share/applications/com.vivaldi.Vivaldi.*.desktop || true

# ── 4.6. Copy Vivaldi Preferences ────────────────────────────────────────────
if [[ -d "$HOME/system_config_git/vivaldi" ]]; then
    echo "Copying Vivaldi preferences..."
    
    mkdir -p ~/.config/vivaldi-casual/Default
    cp ~/system_config_git/vivaldi/casual/Preferences      ~/.config/vivaldi-casual/Default/ 2>/dev/null || true
    cp ~/system_config_git/vivaldi/casual/contextmenu.json ~/.config/vivaldi-casual/Default/ 2>/dev/null || true

    mkdir -p ~/.config/vivaldi-work/Default
    cp ~/system_config_git/vivaldi/work/Preferences        ~/.config/vivaldi-work/Default/ 2>/dev/null || true
    cp ~/system_config_git/vivaldi/work/contextmenu.json   ~/.config/vivaldi-work/Default/ 2>/dev/null || true

    mkdir -p ~/.config/vivaldi-llm/Default
    cp ~/system_config_git/vivaldi/llm/Preferences         ~/.config/vivaldi-llm/Default/ 2>/dev/null || true
    cp ~/system_config_git/vivaldi/llm/contextmenu.json    ~/.config/vivaldi-llm/Default/ 2>/dev/null || true
fi

# ── 5. Create Distrobox Dev Environment ──────────────────────────────────────
echo "Setting up Distrobox dev-box..."
if ! distrobox list | grep -q "dev-box"; then
    distrobox create -Y -n dev-box -i registry.fedoraproject.org/fedora-toolbox:43
fi

echo "Installing dev tools inside distrobox (Node.js, Claude Code, Gemini CLI)..."
distrobox enter dev-box -- sudo dnf install -y nodejs npm
distrobox enter dev-box -- bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
distrobox enter dev-box -- bash -c 'npm config set prefix ~/.npm-global && npm install -g @google/gemini-cli'

# ── 6. Refresh desktop file MIME database ────────────────────────────────────
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

# ── 7. Set zsh as default shell ──────────────────────────────────────────────
ZSH_PATH="$(command -v zsh)"
if [[ "$SHELL" != "$ZSH_PATH" ]]; then
    echo "Setting zsh as default shell (requires password)..."
    sudo usermod -s "$ZSH_PATH" "$(whoami)"
fi

echo ""
echo "Done. If shell was changed, log out and back in for it to take effect."
echo ""
echo "Manual steps still needed:"
echo "  - Wallpapers — cp -r ~/system_config_git/Wallpapers ~/Pictures/Wallpapers"
echo "  - Dropbox — sign in"
echo "  - EasyEffects — presets are applied; open the app to confirm they loaded"
