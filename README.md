# Macra Downloader

A free, open-source graphical download manager for **Windows and Linux** by
Macra Systems, built on **Python 3 + PySide6 (Qt 6)**.
It multi‑threads each download with HTTP Range requests, shows a live per‑download progress
bar (with speed and ETA) in a Windows‑look task‑style window, and writes the final file to a
folder you choose. It also downloads **YouTube** (and ~1000 other sites) via `yt‑dlp`
and **torrents/magnets** via `libtorrent`.

## Download

Get the latest version from the project's **Releases** page:

- **Windows 10/11 (64-bit):** `MacraDownloader-Setup-x64.exe`, a normal installer
  with Start-menu shortcut and uninstaller.
- **Linux (x86-64):** `macra-downloader-linux-x64`, a single self-contained
  executable that needs no Python install.

> **Windows SmartScreen:** the installer is not code-signed yet, so Windows may
> show *"Windows protected your PC"*. Click **More info → Run anyway**. You can
> check the download against the `SHA256SUMS.txt` file published with each release.

## ⚠ Use responsibly

Macra Downloader is a general-purpose tool. Only download content you own, that
is freely licensed, or that you otherwise have permission to download. You are
responsible for following copyright law and the terms of service of the
websites you use. Some sites, including some video platforms, do not allow downloading
in their terms. The app does **not** bypass DRM or any other copy protection.
See [DISCLAIMER.txt](DISCLAIMER.txt).

## Quick start

```bash
# Run it (double‑click in your file manager, or from a terminal):
./dist/download-manager

# Or point it at the source directly:
python3 download_manager_gui.py
```

Give the app a URL, choose a save folder, pick the number of parallel connections
(1–32, **default 32**), press **Add Download**, and watch the progress bar,
speed, total size and **ETA** column fill in as your file downloads.

Paste a **magnet link**, a bare **btih hash**, a **.torrent file path**, or an
http(s) URL ending in **.torrent** and it is routed through the libtorrent
engine instead (same progress/speed/ETA table):
`pip install libtorrent` or `sudo apt install python3-libtorrent`.

URLs to **video sites** (~1000 sites supported by yt-dlp) are detected
automatically and routed through yt-dlp; the app still shows a single live
progress bar, speed and ETA for them. yt-dlp needs **ffmpeg** on the system to
merge the best video + audio into one MP4
(Linux: `sudo apt install ffmpeg`; Windows: `winget install ffmpeg`). Without ffmpeg the app falls back to the best
single-file format automatically.

**Extras**
- **ETA column** — an estimated-time-left countdown updates live while a
  download is running, with smoothed (calm, non-jumpy) speed & ETA readouts.
- **Delete File** — right-click a finished download → *Delete File* to remove it
  straight from the app (with a confirmation prompt).
- **Database + resume** — every download is recorded in a local SQLite database
  (Linux: `~/.local/share/download_manager/downloads.db`,
  Windows: `%LOCALAPPDATA%\MacraDownloader\downloads.db`). Interrupted plain-file
  downloads keep their partial `.part` files on disk, so the next time you open
  the app they are **picked up and resumed automatically** — even days later.

> Note: the xcb (X11) rendering path needs `libxcb-cursor0` on some distros.
> `sudo apt install libxcb-cursor0` if the packaged app won't show a window.

## What's in the box

| File                     | Purpose                                                        |
|--------------------------|----------------------------------------------------------------|
| `download_manager.py`    | Headless core: range splitting, streaming, resume, combining   |
| `download_manager_gui.py`| Windows‑style Qt GUI (entry point for the executable)          |
| `media_downloader.py`    | yt-dlp engine for YouTube & friends (title, progress, merge)  |
| `torrent_downloader.py`  | libtorrent engine: magnets, .torrent files/URLs, hashes       |
| `download_store.py`      | SQLite store: persists downloads so they can resume later      |
| `test_download_manager.py`| Self‑contained unittest suite using a local Range HTTP server |
| `requirements.txt`       | Python deps needed to run the source directly                  |
| `run.sh`                 | Convenience launcher that prefers the built binary             |
| `pyproject.toml`         | pip-installable package (`pip install .`, `download-manager`) |
| `download-manager.desktop`| Linux desktop entry: magnet + .torrent handler                |
| `install.sh`             | System install: pip pkg + desktop entry + MIME registration   |
| `uninstall.sh`           | Removes what install.sh installed                              |

## Install it (Linux Mint / Ubuntu)

```bash
./install.sh
# launches as: download-manager
# handles: download-manager "magnet:?xt=..." / "https://.../x.torrent"
```

This registers the app for `magnet:` links and `.torrent` files, puts a
launcher in your app menu, and adds the `download-manager` command to PATH.

## Automatic URL capture

1. **Clipboard monitor (built in, on by default)** — copy any http(s),
   magnet, or .torrent link anywhere and the app fills the URL box and
   starts the download. Toggle with the *Monitor clipboard for URLs*
   checkbox.
2. **Command-line / .desktop hook** — the app accepts one URL argument:
   `download-manager "<URL-or-magnet>"`, so any browser extension or
   script can hand links to it.
3. **Full browser interception** needs a small browser extension,
   because browsers sandbox pages and won't let a desktop app sniff every
   link. Use any "open in external download manager" extension pointed at
   `download-manager "%U"`. The desktop file already advertises
   `MimeType=application/x-bittorrent;x-scheme-handler/magnet;` so magnets
   and .torrent files open in the app once installed.

## Building releases

Pushing a version tag (for example `git tag v1.2.1 && git push origin v1.2.1`)
runs [.github/workflows/release.yml](.github/workflows/release.yml), which
builds the Windows installer on a GitHub-hosted Windows machine with
PyInstaller + Inno Setup, builds the Linux executable, and publishes both, plus
`SHA256SUMS.txt`, as a GitHub Release. The website in [website/](website/) is
deployed to GitHub Pages by [.github/workflows/pages.yml](.github/workflows/pages.yml).

To build the Windows installer by hand on a Windows PC, see
[packaging/windows/build.ps1](packaging/windows/build.ps1).

### Build the Linux single-file executable yourself

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name download-manager \
    --hidden-import=download_manager \
    --collect-all yt_dlp \
    download_manager_gui.py
# result: dist/download-manager
```

## Run the tests

```bash
python3 -m unittest test_download_manager -v
```

Tests are fully offline: they spin up an embedded HTTP server that understands
`Range`, `HEAD` and `Accept-Ranges`, and verify multi‑connection downloads,
non‑range fallback, unknown‑size fallback, part‑file cleanup, and resume from
pre-existing `.part` files.
## License

Macra Downloader is © 2026 Macra Systems and released under the
[MIT License](LICENSE). It bundles open-source components under their own
licences (Qt/PySide6 under LGPL-3.0, yt-dlp, libtorrent, requests, and others).
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
