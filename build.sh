#!/bin/bash
# Rebuild KDownloader.app (standalone, arm64). Requires: python3, pip install pyinstaller.
set -e
cd "$(dirname "$0")"

# 1. ensure a static ffmpeg is present to bundle
if [ ! -x vendor/ffmpeg ]; then
  echo "downloading static arm64 ffmpeg…"
  mkdir -p vendor
  curl -L -o vendor/ffmpeg \
    https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-darwin-arm64
  chmod +x vendor/ffmpeg
fi

# 2. build
python3 -m PyInstaller --noconfirm --clean KDownloader.spec

echo
echo "Built: dist/KDownloader.app"
echo "First launch (unsigned): right-click the app > Open, then confirm."
