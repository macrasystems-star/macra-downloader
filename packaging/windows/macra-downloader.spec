# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows build of Macra Downloader.
#
# Builds a *one-folder* app (dist\MacraDownloader\) rather than a single exe:
#  - starts faster (nothing is unpacked to %TEMP% on every launch),
#  - triggers fewer antivirus false positives,
#  - keeps the Qt/PySide6 DLLs as separate, replaceable files, which is the
#    simplest way to meet the LGPL-3.0 terms Qt is licensed under.
# Inno Setup (installer.iss) then wraps that folder into the installer.
#
# Run from the repository root:  pyinstaller packaging\windows\macra-downloader.spec
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, '..', '..'))

datas = [
    (os.path.join(ROOT, 'assets', 'icon-256.png'), 'assets'),
    (os.path.join(ROOT, 'LICENSE'), '.'),
    (os.path.join(ROOT, 'THIRD_PARTY_NOTICES.md'), '.'),
    (os.path.join(ROOT, 'DISCLAIMER.txt'), '.'),
    (os.path.join(ROOT, 'PRIVACY.md'), '.'),
]
binaries = []
hiddenimports = ['download_manager', 'media_downloader',
                 'torrent_downloader', 'download_store', 'browser_handoff', 'browser_setup', 'update_check', 'native_host']

d, b, h = collect_all('yt_dlp')
datas += d; binaries += b; hiddenimports += h

# libtorrent is optional: the app still runs (without torrents) if it's missing.
try:
    d, b, h = collect_all('libtorrent')
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# Ship each bundled package's licence files (*.dist-info) with the app.
for pkg in ('PySide6_Essentials', 'shiboken6', 'yt-dlp', 'requests', 'urllib3',
            'idna', 'charset-normalizer', 'certifi', 'tqdm', 'libtorrent'):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(
    [os.path.join(ROOT, 'download_manager_gui.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy Qt modules the app never uses.
    excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MacraDownloader',
    icon=os.path.join(ROOT, 'assets', 'icon.ico'),
    version=os.path.join(SPECPATH, 'version_info.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed exes are a common antivirus false positive
    console=False,
    disable_windowed_traceback=False,
)

# The browser helper: a small console program (browsers talk to it over stdin/stdout).
# Standard library only, so it starts fast.
host_a = Analysis(
    [os.path.join(ROOT, 'native_host.py')],
    pathex=[ROOT],
    hiddenimports=['browser_handoff'],
    excludes=['tkinter', 'PySide6', 'shiboken6', 'yt_dlp', 'libtorrent', 'requests'],
    noarchive=False,
    optimize=0,
)
host_exe = EXE(
    PYZ(host_a.pure),
    host_a.scripts,
    [],
    exclude_binaries=True,
    name='MacraDownloaderHost',
    icon=os.path.join(ROOT, 'assets', 'icon.ico'),
    version=os.path.join(SPECPATH, 'version_info.txt'),
    debug=False,
    strip=False,
    upx=False,
    console=True,       # browsers start it without a window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    host_exe,
    host_a.binaries,
    host_a.datas,
    strip=False,
    upx=False,
    name='MacraDownloader',
)
