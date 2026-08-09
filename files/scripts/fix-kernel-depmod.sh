#!/usr/bin/env bash
# Ensure modules.dep exists for the installed vanilla kernel immediately after
# the kernel-vanilla/stable swap. This prevents subsequent dnf transactions
# (which execute kernel %posttrans scriptlets / dracut) from failing with:
# "dracut[F]: /usr/lib/modules/<KVER>/modules.dep is missing."
set -euo pipefail

KVER="$(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' kernel-core | sort -V | tail -1)"
echo ">>> Pre-generating module dependencies (depmod -a) for kernel ${KVER}..."
depmod -a "${KVER}" || true
