#!/usr/bin/env bash
# Install ryzenadj via rpm --nodeps from COPR shdwchn10/ryzenadj.
#
# ryzenadj requires /dev/cpu/0/msr and does not need the ryzen_smu kernel module.
# Installing via standard dnf pulls akmod-ryzen_smu, whose root %post fails in build.
set -euo pipefail

COPR="shdwchn10/ryzenadj"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cat >/etc/yum.repos.d/_copr_ryzenadj.repo <<EOF
[copr:copr.fedorainfracloud.org:shdwchn10:ryzenadj]
name=Copr repo for ryzenadj owned by shdwchn10
baseurl=https://download.copr.fedorainfracloud.org/results/${COPR}/fedora-\$releasever-\$basearch/
type=rpm-md
gpgcheck=1
gpgkey=https://download.copr.fedorainfracloud.org/results/${COPR}/pubkey.gpg
repo_gpgcheck=0
enabled=1
EOF

echo ">>> Downloading ryzenadj RPM..."
dnf download --destdir="$WORK_DIR" ryzenadj

RYZENADJ_RPM="$(ls "$WORK_DIR"/ryzenadj-*.rpm | head -1)"
echo ">>> Installing ${RYZENADJ_RPM} with --nodeps..."
rpm -i --nodeps "$RYZENADJ_RPM"

rm -f /etc/yum.repos.d/_copr_ryzenadj.repo
echo ">>> Done: ryzenadj installed"
