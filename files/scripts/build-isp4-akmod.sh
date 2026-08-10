#!/usr/bin/env bash
# Build the AMD ISP4 webcam kmod against the installed vanilla kernel at image
# build time, and bake in the resulting binary kmod RPM.
#
# Why a script instead of just installing akmod-amd-isp4-capture via the dnf module:
#   1. The akmod package's install-time %post runs akmods, which refuses to run as
#      root. The image build runs as root, so the whole dnf transaction fails with
#      "ERROR: Not to be used as root; start as user or 'akmodsbuild' instead."
#   2. On a bootc/ostree system akmods.service is skipped at boot
#      (ConditionPathExists=!/run/ostree-booted), so the "rebuilds on first boot"
#      path never runs either. The module must be built now and shipped as a kmod.
# So: build the kmod here as the non-root 'akmods' user, then install the result.
#
# REMOVE this script + its recipe entry when kernel-vanilla ships amd_isp4_capture
# in-tree (driver mainlined upstream ~7.2; Fedora's config enabled it already in
# 7.1.6+, but kernel-vanilla builds still lack it — check the kernel-modules
# filelists before removing).
set -euo pipefail

COPR="abn/amd-isp4-capture-kmod"
ARCH="$(uname -m)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Build for the installed (vanilla) kernel-core, pinned exactly to avoid the
# version skew that exists in the kernel-vanilla COPR (multiple 7.0.x present).
KVER="$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' kernel-core | sort -V | tail -1)"
echo ">>> Building amd-isp4-capture kmod for kernel ${KVER} (${ARCH})"

# Ensure modules.dep exists for KVER so kernel scriptlets / dracut during dnf install do not fail
depmod -a "${KVER}" || true

# Build prerequisites. The shared build toolchain was already removed by the common
# module list (this script runs after the from-file include), so install what
# akmodsbuild/rpmbuild needs here, then clean up at the end.
#   akmods                → akmodsbuild + the non-root 'akmods' user
#   kmodtool              → required by the kmod .spec
#   dnf5-plugins          → provides `dnf download`
#   cpio                  → extract the akmod payload (ALREADY in base: dep of dracut)
#   gcc/make/elfutils-libelf-devel + kernel-devel-<KVER> → compile the module
BUILD_PKGS=(akmods kmodtool dnf5-plugins cpio gcc make elfutils-libelf-devel "kernel-devel-${KVER}")
# Snapshot which of these are NOT already installed, so cleanup only removes what
# this script actually adds. Several build deps (cpio, dnf5-plugins,
# elfutils-libelf-devel) already ship in base-main; cpio in particular is a hard
# dep of dracut → ostree → {rpm-ostree, bootc}. Blindly removing it in cleanup
# cascades and strips the entire OS management stack out of the image — that is
# exactly what produced the toolless 20260531.0 build (no bootc/rpm-ostree/ostree).
ADDED_PKGS=()
for p in "${BUILD_PKGS[@]}"; do
  rpm -q "$p" >/dev/null 2>&1 || ADDED_PKGS+=("$p")
done
# Install build prereqs. akmods pulls dkms, which requires kernel-devel-matched;
# Terra's kernel-p03 (7.2.0-rc*) satisfies that with a higher version than the
# vanilla COPR and drags in the whole Terra kernel, whose %posttrans dracut run
# fails (modules.dep missing) and aborts the build. Exclude it so
# kernel-devel-matched resolves to the vanilla devel matching KVER.
# (Same trap as the ryzenadj block in the halo recipe — see 6b484b5.)
dnf install -y --setopt=install_weak_deps=False --exclude='kernel-p03*' "${BUILD_PKGS[@]}"

# COPR repo, download-only: we deliberately do NOT install the akmod package
# (its root %post is what fails). The kernel-vanilla COPR (added by the common
# recipe, cleanup disabled) stays enabled and supplies the matching kernel-devel.
cat >/etc/yum.repos.d/_copr_amd-isp4-capture.repo <<EOF
[copr:copr.fedorainfracloud.org:abn:amd-isp4-capture-kmod]
name=Copr repo for amd-isp4-capture-kmod owned by abn
baseurl=https://download.copr.fedorainfracloud.org/results/${COPR}/fedora-\$releasever-\$basearch/
type=rpm-md
gpgcheck=1
gpgkey=https://download.copr.fedorainfracloud.org/results/${COPR}/pubkey.gpg
repo_gpgcheck=0
enabled=1
EOF

# Fetch the akmod package (it carries the source RPM under ./usr/src/akmods/).
dnf download --destdir="$WORK" akmod-amd-isp4-capture
( cd "$WORK" && rpm2cpio "$WORK"/akmod-amd-isp4-capture-*.rpm | cpio -idm )
SRPM="$(ls "$WORK"/usr/src/akmods/amd-isp4-capture-kmod-*.src.rpm | head -1)"
echo ">>> Source RPM: ${SRPM}"

# Build as the non-root 'akmods' user (akmodsbuild refuses root).
AKB="$(command -v akmodsbuild || echo /usr/sbin/akmodsbuild)"
install -d -o akmods -g akmods "$WORK/build" "$WORK/out"
chmod 755 "$WORK"
chmod -R a+rX "$WORK/usr"
if ! runuser -u akmods -- env HOME="$WORK/build" \
      "$AKB" --target "$ARCH" --kernels "$KVER" \
      --outputdir "$WORK/out" --logfile "$WORK/build/akmodsbuild.log" "$SRPM"; then
  echo ">>> akmodsbuild FAILED — build log follows:" >&2
  cat "$WORK/build/akmodsbuild.log" >&2 || true
  exit 1
fi

# Install the freshly built binary kmod into the image.
# Use rpm --nodeps, NOT dnf: the per-kernel kmod carries a hard
# "Requires: akmod-amd-isp4-capture" (kmodtool adds it so stock systems can
# rebuild on new kernels), and pulling that akmod in re-triggers its root %post,
# which fails. On an atomic image we never want the akmod (akmods.service is
# skipped at boot), so install just the self-contained kmod and skip the dep.
KMOD_RPM="$(ls "$WORK"/out/kmod-amd-isp4-capture-*.rpm | head -1)"
echo ">>> Installing ${KMOD_RPM} (rpm --nodeps, skipping the akmod meta-dep)"
rpm -i --nodeps "$KMOD_RPM"

echo ">>> Regenerating module dependencies for kernel ${KVER}..."
depmod -a "${KVER}"

# Clean up ONLY the packages this script actually added (never base-image deps),
# and disable cascade/orphan removal (clean_requirements_on_remove=False) so a
# leaf build tool can't drag out the boot or ostree stack. See ADDED_PKGS above.
if [ "${#ADDED_PKGS[@]}" -gt 0 ]; then
  dnf remove -y --setopt=clean_requirements_on_remove=False "${ADDED_PKGS[@]}" || true
fi
rm -f /etc/yum.repos.d/_copr_amd-isp4-capture.repo

echo ">>> Done: $(rpm -qa 'kmod-amd-isp4-capture*')"
find "/usr/lib/modules/${KVER}/extra" -iname '*isp4*' -print 2>/dev/null \
  || echo ">>> NOTE: no module under extra/; verify kmod payload path on first boot"
