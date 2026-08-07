#!/bin/bash

case "$1" in
    start)
        # Max sustained performance below the 95C TjMax floor, verified 2026-08-07:
        # - tctl-temp=92: SMU smoothly regulates power to hold ~92C (no panic sawtooth)
        # - apu-slow-limit=70000 (stock): explicitly non-binding; temperature is the
        #   only governor. fast/slow/STAPM writes revert/are ignored on this SMU, so
        #   short bursts still get stock 81W fast PPT regardless.
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=92 --apu-slow-limit=70000 2>/dev/null || true
        fi
        ;;
    stop)
        # Restore full stock behavior (TjMax 95C, stock apu-slow 70W).
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=95 --apu-slow-limit=70000 2>/dev/null || true
        fi
        ;;
esac

exit 0
