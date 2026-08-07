#!/bin/bash

case "$1" in
    start)
        # Apply 90C thermal target and flat power limits (55W) if ryzenadj is installed
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=90 --fast-limit=55000 --slow-limit=55000 --stapm-limit=55000 2>/dev/null || true
        fi
        ;;
    stop)
        # Reset default thermal target
        if command -v ryzenadj &> /dev/null; then
            ryzenadj --tctl-temp=95 2>/dev/null || true
        fi
        ;;
esac

exit 0
