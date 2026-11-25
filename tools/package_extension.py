#!/usr/bin/env python3
"""Zip browser_extension/ for the Chrome Web Store, Edge Add-ons and Firefox Add-ons.

The stores give the extension their own ID, so the "key" field (which fixes the
ID of the copy MacraOS installs) is left out. The version must match the app.

    python3 tools/package_extension.py OUT.zip
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'browser_extension'


def main(out):
    manifest = json.loads((SRC / 'manifest.json').read_text())
    app = re.search(r"^APP_VERSION = '(.+)'", (ROOT / 'download_manager_gui.py').read_text(), re.M).group(1)
    if manifest['version'] != app:
        sys.exit(f'browser_extension/manifest.json version {manifest["version"]} != app version {app}')
    manifest.pop('key', None)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=2) + '\n')
        for f in sorted(SRC.rglob('*')):
            if f.is_file() and f.name != 'manifest.json':
                z.write(f, f.relative_to(SRC).as_posix())
    print(f'wrote {out}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'macra-downloader-extension.zip')
