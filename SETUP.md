# First-Time Setup Guide

Complete walkthrough from a fresh Fedora Silverblue install to a fully working samuel-niri system.

---

## 0. Before you wipe EOS — do this first

Save your SSH keys to Bitwarden as secure notes. On your current EOS machine:

```bash
cat ~/.ssh/id_ed25519      # copy this → new Bitwarden secure note: "SSH Private Key"
cat ~/.ssh/id_ed25519.pub  # copy this → new Bitwarden secure note: "SSH Public Key"
```

Include the full content including header/footer lines for the private key. You'll retrieve
these from bitwarden.com on first boot using Vivaldi, which is already in the image.

---

## 1. Install Fedora Silverblue

During the Anaconda installer:
- Choose **XFS** for the filesystem
- Set username to **`samuel`** — the niri config has hardcoded `/home/samuel/` paths in keybinds and scripts
- Connect to WiFi during install (the network profile carries over to the installed system)

---

## 2. Rebase to the custom image

After first boot into stock Silverblue GNOME, open a terminal and run:

```bash
rpm-ostree rebase ostree-unverified-registry:ghcr.io/samuelueluel/samuel-niri:latest
systemctl reboot
```

This pulls the full image (~3–5 GB). After reboot you land at **tuigreet → Niri**.

---

## 3. First boot — before dotfiles

Niri starts with no config (built-in defaults only). Open a terminal:

- Default niri keybind: **`Super+T`** → opens Alacritty
- Fallback: **`Ctrl+Alt+F2`** → TTY login

Connect to WiFi if not already connected:

```bash
nmtui
```

---

## 4. SSH keys

Open Vivaldi (it's already installed), log into bitwarden.com, and retrieve
your SSH keys from the secure notes you saved in step 0.

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh

# Paste private key content from Bitwarden (include the header/footer lines):
nano ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519

# Paste public key content from Bitwarden (single line):
nano ~/.ssh/id_ed25519.pub
chmod 644 ~/.ssh/id_ed25519.pub
```

Test that GitHub recognizes the key before continuing:
```bash
ssh -T git@github.com
# Expected: "Hi samuelueluel! You've successfully authenticated..."
```

---

## 5. Clone repos

```bash
git clone git@github.com:samuelueluel/dotfiles.git ~/dotfiles
git clone git@github.com:samuelueluel/samuel-niri.git ~/work-image
git clone git@github.com:samuelueluel/system_config_git.git ~/system_config_git
```

`system_config_git` is needed for manual steps in step 8 (Vivaldi preferences and wallpapers).
It is not used by chezmoi.

---

## 6. Run dotfiles setup

```bash
bash ~/work-image/setup-dotfiles.sh
```

This runs `chezmoi apply`, which deploys all config files (niri, waybar, zsh, etc.),
vivaldi launcher .desktop files, and nemo settings.
Log out and back in — this activates zsh.

---

## 7. Install Claude Code and Gemini CLI

```bash
# Claude Code — standalone binary installer (not npm)
curl -fsSL https://claude.ai/install.sh | bash

# Gemini CLI — npm
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
npm install -g @google/gemini-cli
```

Authenticate both:
```bash
claude   # browser-based login on first run
gemini   # browser OAuth on first run
```

---

## 8. Remaining manual steps

**Dropbox** — sign in via tray icon or:
```bash
dropbox start -i
```

**Vivaldi** — launch each profile once (Casual, Work, LLMs) to generate its data directory, then copy saved preferences.
*(Requires `~/system_config_git` — see step 5.)*

**Important Note for Vivaldi RPM on Wayland:** Vivaldi may auto-generate its own `.desktop` files (e.g., `com.vivaldi.Vivaldi.Casual.desktop`) when profiles are launched. These default files lack the necessary Wayland flags (`--ozone-platform-hint=wayland`) and their filenames do not match the exact Niri/Waybar `app-id` window class. If Waybar icons stop working or Vivaldi hangs, ensure you delete the auto-generated ones: `rm ~/.local/share/applications/com.vivaldi.Vivaldi.*.desktop`. The correct ones applied by `chezmoi` are named exactly after their app-ids (e.g., `VivaldiCasual.desktop`).

```bash
# Copy preferences for each profile (casual, work, llm):
cp ~/system_config_git/vivaldi/casual/Preferences      ~/.config/vivaldi-casual/Default/
cp ~/system_config_git/vivaldi/casual/contextmenu.json ~/.config/vivaldi-casual/Default/

cp ~/system_config_git/vivaldi/work/Preferences        ~/.config/vivaldi-work/Default/
cp ~/system_config_git/vivaldi/work/contextmenu.json   ~/.config/vivaldi-work/Default/

cp ~/system_config_git/vivaldi/llm/Preferences         ~/.config/vivaldi-llm/Default/
cp ~/system_config_git/vivaldi/llm/contextmenu.json    ~/.config/vivaldi-llm/Default/
```

**Extensions** install normally through the Chrome Web Store — they live in their respective `~/.config/vivaldi-*/Default/`
(mutable home dir) and survive reboots and image updates.

**Wallpapers** *(Requires `~/system_config_git` — see step 5.)*
```bash
cp -r ~/system_config_git/Wallpapers ~/Pictures/Wallpapers
```

**EasyEffects** — presets are applied by chezmoi; open the app to confirm they loaded.

**Okular (and other Flatpaks)** — Because many apps are now Flatpaks, their config files do not go in `~/.config/`. They must go in `~/.var/app/<app-id>/config/`.
*(Requires `~/system_config_git` — see step 5.)*

```bash
# Launch Okular once to generate its sandbox directory
flatpak run org.kde.okular & sleep 2 && kill $!

# Copy your custom Okular config into the Flatpak sandbox
mkdir -p ~/.var/app/org.kde.okular/config
cp ~/system_config_git/okular/.config/okularrc ~/.var/app/org.kde.okular/config/okularrc
```

**Other** - check the `system_config_git` folder for any other settings not auto-applied. Remember: if the app is a Flatpak, its configs belong in `~/.var/app/`, not `~/.config/`.

FYI: Trigger rebuild: gh workflow run bluebuild --repo samuelueluel/samuel-niri

  gh workflow run triggers a workflow_dispatch event on the named workflow. The workflow file has
  workflow_dispatch: in its on: triggers, which is what allows it to be fired manually like this.
