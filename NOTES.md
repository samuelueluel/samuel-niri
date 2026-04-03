# Build Notes

## Packages removed or changed from original recipe (Fedora 43 compatibility)

| Package | Status |
|---|---|
| `hyprpicker` | Removed — not in Fedora repos; add via script if needed |
| `dex` | Removed — not in Fedora repos; used for XDG autostart (dropbox, nm-applet, etc.) |
| `nano-syntax-highlighting` | Removed — not in Fedora repos |
| `resvg` | Removed — not in Fedora repos; yazi SVG previews won't work without it |
| `pipewire-jack` | Renamed → `pipewire-jack-audio-connection-kit` |
| `sof-firmware` | Renamed → `alsa-sof-firmware` |
| `modemmanager` | Renamed → `ModemManager` (case-sensitive) |
| `inetutils` | Replaced with `iputils` + `hostname` |
| `dropbox.service` | Removed from systemd — Dropbox RPM doesn't ship a systemd unit; autostart needs manual setup |

## VM test findings (2026-04-01)
- greetd fails without a `greeter` system user — fixed in `setup-greetd.sh`
- Niri fails in VM with `DeviceMissing` (no KMS/DRM device) — expected, works on real hardware

## Packages installed via script instead of dnf (COPR lacks Fedora 43 builds)

- `auto-cpufreq` — installed from source via pip (`install-auto-cpufreq.sh`)
- `nwg-look` — built from source via Go/CGo (`install-nwg-look.sh`)
- `xdg-desktop-portal-termfilechooser` — built from source via Meson (`build-termfilechooser.sh`)
- `yazi` — pre-built musl binary from GitHub releases (`install-yazi.sh`)

## Other
- need to decide how to install rtk and headroom for LLM CLIs
