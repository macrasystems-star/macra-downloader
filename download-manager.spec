# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets/icon-256.png', 'assets'), ('LICENSE', '.'),
         ('THIRD_PARTY_NOTICES.md', '.'), ('DISCLAIMER.txt', '.'), ('PRIVACY.md', '.')]
binaries = []
hiddenimports = ['download_manager', 'media_downloader',
                 'torrent_downloader', 'download_store']
tmp_ret = collect_all('yt_dlp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
try:
    tmp_lt = collect_all('libtorrent')
    datas += tmp_lt[0]; binaries += tmp_lt[1]; hiddenimports += tmp_lt[2]
except Exception:
    pass


a = Analysis(
    ['download_manager_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='download-manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
