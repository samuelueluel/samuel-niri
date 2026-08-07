#!/bin/bash

case "$1" in
    start)
        # Max sustained performance below 95C TjMax floor for GPU/LLM workloads:
        # - tctl-temp=92: SMU smoothly regulates power to hold ~92C
        # - apu-slow-limit=70000 (stock): explicitly non-binding
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=92 --apu-slow-limit=70000 2>/dev/null || true
        fi
        ;;
    stop)
        # Restore stock behavior (TjMax 95C, stock apu-slow 70W).
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=95 --apu-slow-limit=70000 2>/dev/null || true
        fi
        ;;
esac

exit 0
