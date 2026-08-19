#!/usr/bin/env bash
# Install ryzenadj static binary from the official GitHub release.
#
# The shdwchn10/ryzenadj COPR stopped shipping builds (all chroot dirs empty
# as of 2026-08-18), which silently broke the TuneD power hook (llm_tune.sh
# -> ryzenadj --tctl-temp=92). The musl-static x86_64 release is dependency-free.
#
# ryzenadj requires /dev/cpu/0/msr and does not need the ryzen_smu kernel module.
set -euo pipefail

VERSION="v0.19.0"
URL="https://github.com/FlyGoat/ryzenadj/releases/download/${VERSION}/ryzenadj-linux-musl-static-x86_64.tar.gz"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo ">>> Downloading ryzenadj ${VERSION} (musl static)..."
curl -fsSL -o "$WORK_DIR/ryzenadj.tar.gz" "$URL"
tar xzf "$WORK_DIR/ryzenadj.tar.gz" -C "$WORK_DIR"
install -m 0755 "$WORK_DIR"/ryzenadj-linux-musl-static-x86_64/ryzenadj /usr/local/bin/ryzenadj

echo ">>> Done: $(/usr/local/bin/ryzenadj -h 2>&1 | head -1 || true)"
