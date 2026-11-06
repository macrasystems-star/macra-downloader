#!/usr/bin/env bash
# Install Macra Downloader system-wide (Linux Mint / Ubuntu / Debian).
# - installs Python package (pip) so `download-manager` is on PATH
# - installs .desktop entry + icon
# - registers magnet: and .torrent MIME handlers
# - documents the optional browser extension step for automatic link capture
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Python package (pip)..."
if [[ -x venv/bin/pip ]]; then
  venv/bin/pip install -q ".[all]" 2>/dev/null || venv/bin/pip install -q .
  BIN="$(pwd)/venv/bin/download-manager"
  # Shim on PATH pointing at the venv entry point.
  sudo ln -sf "$BIN" /usr/local/bin/download-manager
else
  pip3 install --user .
fi

echo "==> Installing desktop entry + icon..."
ICON_SRC=""
for c in download-manager.png download-manager.svg icon.png icon.svg; do
  [[ -f "$c" ]] && ICON_SRC="$c" && break
done
if [[ -n "$ICON_SRC" ]]; then
  sudo install -Dm644 "$ICON_SRC" /usr/share/icons/hicolor/256x256/apps/download-manager.png
fi
sudo install -Dm644 download-manager.desktop /usr/share/applications/download-manager.desktop
sudo update-desktop-database /usr/share/applications 2>/dev/null || true

echo "==> Registering magnet + .torrent handler..."
xdg-mime default download-manager.desktop x-scheme-handler/magnet || true
xdg-mime default download-manager.desktop application/x-bittorrent || true

echo
echo "Done. Launch with: download-manager"
echo
echo "Browser auto-capture: browsers do not let a desktop app"
echo "sniff every link for security reasons. For full auto-capture install a"
echo "tiny extension (e.g. 'Open in Download Manager') that sends URLs to:"
echo "  download-manager \"<URL>\""
echo "Clipboard monitoring inside the app already picks up any copied"
echo "http(s)/magnet/.torrent link automatically."
