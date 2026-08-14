# Turquoise (BlueBuild image)

Personal Fedora Atomic image built with [BlueBuild](https://blue-build.org) on Universal Blue `base-main`, niri compositor. This repo is the *source* for the host OS image of Samuel's HP ZBook. Global agent rules live in `~/.pi/agent/APPEND_SYSTEM.md`; this file adds repo-local conventions.

## Layout

- `recipes/*.yml` — BlueBuild recipe files: `turquoise-common.yml`, `turquoise-recipe.yml`, `turquoise-halo-recipe.yml`. Active build config: `config/config.yaml`.
- `files/` — overlay copied into the image: `files/system/` → `/` (e.g. `files/system/usr/share/turquoise/justfile` becomes `/usr/share/turquoise/justfile`, run on the host as `sjust <recipe>`); `files/halo/` → halo-variant additions; `files/scripts/` → helper scripts.
- `data/` (build metadata), `state/` (build state), `cosign.pub` (signing key).

## Working here

- Editing files in this repo does NOT change the running system. Changes land only after the GitHub Actions build (`.github/workflows/build.yml`: push to `main`, schedule, `workflow_dispatch`) and an OS rebase. Workflow: make the edit, commit, push, then tell Samuel a rebase will be needed.
- Never run `sjust`, `just`, `uupd`, or build commands. Agents have no sudo, and `sjust update` (justfile line 446) runs heavy host updates, including the Lemonade llama.cpp engine refresh (see vault note `10_Projects/Local-LLMs/Hybrid-Backend-Transition.md`). Host updates run automatically via `uupd` at 4 AM; manual runs are Samuel's call.
- Don't touch `config/config.yaml.bak` (backup).
