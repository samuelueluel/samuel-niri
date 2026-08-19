#!/bin/bash
# Sustained-profile power hook (gpu-sustained / cpu-sustained).
#
# Sets the SMU temp target so power is regulated smoothly toward ~92C instead of
# sawtoothing against the 95C TjMax floor. Limit writes (fast/slow/stapm) are
# reverted by the PMFW on this family-1Ah SMU and are deliberately NOT attempted;
# apu-slow-limit is pinned at stock 70 W so it never binds (70 W is the envelope).
#
# Every run logs to /var/log/ryzenadj-hook.log with a self-check of the applied
# target — if ryzenadj is missing or the write fails, the log says so loudly
# instead of degrading silently (see 2026-08-18: COPR died, hook was a no-op).

LOG=/var/log/ryzenadj-hook.log

case "$1" in
    start)
        if command -v ryzenadj &> /dev/null; then
            if ryzenadj --tctl-temp=92 --apu-slow-limit=70000 2>>"$LOG"; then
                if ryzenadj -i 2>/dev/null | grep -q "THM LIMIT CORE.*92"; then
                    echo "$(date +%F_%T) OK: tctl-temp=92 applied (apu-slow 70W stock)" >> "$LOG"
                else
                    echo "$(date +%F_%T) WARN: ryzenadj ran but THM LIMIT CORE != 92" >> "$LOG"
                fi
            else
                echo "$(date +%F_%T) ERROR: ryzenadj write failed (exit $?)" >> "$LOG"
            fi
        else
            echo "$(date +%F_%T) ERROR: ryzenadj not found — tctl-temp=92 NOT applied (power profile degraded)" >> "$LOG"
        fi
        ;;
    stop)
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=95 --apu-slow-limit=70000 2>>"$LOG" || true
            echo "$(date +%F_%T) stop: restored stock tctl-temp=95" >> "$LOG"
        else
            echo "$(date +%F_%T) stop: ryzenadj not found, nothing to restore" >> "$LOG"
        fi
        ;;
esac

exit 0
