#!/usr/bin/env bash
# Build the out-of-tree mt7925 Wi-Fi driver against the installed kernel at image build time.
# Replaces the in-tree MT7925/MT7921 modules with patched versions from zbowling/mt7925.
# Remove this script once the patches land upstream in the Linux kernel.
set -euo pipefail

ARCH="$(uname -m)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Build for the installed kernel
KVER="$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' kernel-core | sort -V | tail -1)"
echo ">>> Building mt7925 kmod for kernel ${KVER} (${ARCH})"

# Install prerequisites
BUILD_PKGS=(git gcc make "kernel-devel-${KVER}")
ADDED_PKGS=()
for p in "${BUILD_PKGS[@]}"; do
  rpm -q "$p" >/dev/null 2>&1 || ADDED_PKGS+=("$p")
done
if [ "${#ADDED_PKGS[@]}" -gt 0 ]; then
  dnf install -y --setopt=install_weak_deps=False "${ADDED_PKGS[@]}"
fi

echo ">>> Cloning mt7925 repository..."
git clone --depth 1 https://github.com/zbowling/mt7925.git "$WORK/mt7925"

echo ">>> Patching for kernel 7.1+ & 7.2+ API changes..."
# In kernel 7.1+, the `u` union inside `ieee80211_mgmt->u.action` became anonymous, and action_code moved to the parent action struct.
find "$WORK/mt7925/dkms/src" -type f -name "*.c" -exec sed -i -e 's/mgmt->u\.action\.u\.addba_req\.action_code/mgmt->u.action.action_code/g' -e 's/mgmt->u\.action\.u\.addba_req\.capab/mgmt->u.action.addba_req.capab/g' {} +
# In kernel 7.2+, IEEE80211_EML_CAP_EMLSR_* padding/transition delay constants were renamed to IEEE80211_EML_CAP_EML_*
find "$WORK/mt7925/dkms/src" -type f -name "*.c" -exec sed -i -e 's/IEEE80211_EML_CAP_EMLSR_PADDING_DELAY/IEEE80211_EML_CAP_EML_PADDING_DELAY/g' -e 's/IEEE80211_EML_CAP_EMLSR_TRANSITION_DELAY/IEEE80211_EML_CAP_EML_TRANSITION_DELAY/g' {} +


echo ">>> Building mt7925 modules..."
cd "$WORK/mt7925/dkms/src"
make KDIR="/usr/src/kernels/${KVER}" all

echo ">>> Installing mt7925 modules..."
MODDIR="/usr/lib/modules/${KVER}/extra/mt7925"
mkdir -p "$MODDIR"
cp *.ko "$MODDIR/"

echo ">>> Regenerating module dependencies for kernel ${KVER}..."
depmod -a "${KVER}"

echo ">>> Cleaning up build dependencies..."
if [ "${#ADDED_PKGS[@]}" -gt 0 ]; then
  dnf remove -y --setopt=clean_requirements_on_remove=False "${ADDED_PKGS[@]}" || true
fi

echo ">>> Done. Modules installed to ${MODDIR}:"
ls -l "$MODDIR"
