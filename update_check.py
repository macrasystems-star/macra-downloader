#!/usr/bin/env python3
"""Check GitHub for a newer Macra Downloader release (at most once a day).

The only data sent is a normal HTTPS request to GitHub's public API (GitHub
sees the computer's IP address and the app version in the User-Agent).
It can be turned off in the Help menu. Nothing is installed without the user
pressing a button.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import download_store

REPO = 'macrasystems-star/macra-downloader'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPO}/releases/latest'
CHECK_EVERY = 24 * 3600
SETTINGS_FILE = download_store.APP_DIR / 'settings.json'
#: The Windows installer and the checksum list, as release.yml names them.
WINDOWS_ASSET = 'MacraDownloader-Setup-x64.exe'
SUMS_ASSET = 'SHA256SUMS.txt'


# ----- small settings file (shared with the window) ------------------------
def load_settings(path=None):
    try:
        data = json.loads(Path(path or SETTINGS_FILE).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings, path=None):
    path = Path(path or SETTINGS_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(settings, indent=1), encoding='utf-8')
        os.replace(str(tmp), str(path))
    except OSError:
        pass


# ----- versions -------------------------------------------------------------
def parse_version(text):
    """'v1.10.2' -> (1, 10, 2); None if it is not a plain release number."""
    m = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?', (text or '').strip())
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def due(settings, now=None):
    if settings.get('check_updates', True) is False:
        return False
    now = time.time() if now is None else now
    last = settings.get('last_update_check', 0)
    return not isinstance(last, (int, float)) or now - last >= CHECK_EVERY or now < last


# ----- GitHub ---------------------------------------------------------------
def fetch_latest(current_version, timeout=15):
    """The newest release: {'version', 'page', 'assets': {name: url}}. Raises on network errors."""
    import requests
    r = requests.get(API_LATEST, timeout=timeout, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'MacraDownloader/{current_version}'})
    r.raise_for_status()
    data = r.json()
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('latest release is not a final release')
    return {'version': (data.get('tag_name') or '').lstrip('v'),
            'page': data.get('html_url') or RELEASES_PAGE,
            'assets': {a.get('name'): a.get('browser_download_url')
                       for a in data.get('assets') or [] if a.get('name')}}


def expected_sha256(sums_text, name):
    """The checksum for *name* from a `sha256sum` listing, or None."""
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip('*') == name and re.fullmatch(r'[0-9a-fA-F]{64}', parts[0]):
            return parts[0].lower()
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()
