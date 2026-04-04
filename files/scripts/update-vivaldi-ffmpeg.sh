#!/usr/bin/env bash
set -euo pipefail

echo "Updating Vivaldi ffmpeg codecs..."
if [ -x /opt/vivaldi/update-ffmpeg ]; then
    /opt/vivaldi/update-ffmpeg
else
    echo "Warning: /opt/vivaldi/update-ffmpeg not found."
fi
