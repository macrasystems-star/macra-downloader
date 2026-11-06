#!/usr/bin/env bash
# Remove what install.sh installed.
set -euo pipefail
sudo rm -f /usr/share/applications/download-manager.desktop \
  /usr/share/icons/hicolor/256x256/apps/download-manager.png \
  /usr/local/bin/download-manager
sudo update-desktop-database /usr/share/applications 2>/dev/null || true
pip3 uninstall -y download-manager 2>/dev/null || true
echo "Uninstalled."
