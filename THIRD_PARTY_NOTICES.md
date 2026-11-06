# Third-party notices

Macra Downloader is © 2026 Macra Systems and released under the MIT License
(see `LICENSE`). The packaged Windows and Linux builds bundle the following
open-source components, each under its own licence. Their copyright notices
and licence texts are reproduced or linked below, as those licences require.

| Component | Used for | Licence | Source |
|---|---|---|---|
| Qt 6 / PySide6 (Qt for Python) | User interface | LGPL-3.0 | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| yt-dlp | Video-site downloads | Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| libtorrent (Rasterbar) | BitTorrent / magnet downloads | BSD-3-Clause | https://github.com/arvidn/libtorrent |
| OpenSSL (shipped inside libtorrent / Python) | TLS encryption | Apache-2.0 | https://www.openssl.org/source/ |
| requests | HTTP client | Apache-2.0 | https://github.com/psf/requests |
| urllib3, idna, charset-normalizer, certifi (requests deps) | HTTP / TLS support | MIT, BSD-3-Clause, MIT, MPL-2.0 | https://pypi.org/ |
| tqdm | Progress output (command-line mode) | MIT and MPL-2.0 | https://github.com/tqdm/tqdm |
| CPython runtime | Runs the app | PSF License | https://www.python.org/ |
| PyInstaller bootloader | Packaging | GPL-2.0 with bootloader exception (allows any licence for the packaged app) | https://github.com/pyinstaller/pyinstaller |

## Qt / PySide6 (LGPL-3.0)

The user interface uses Qt and PySide6 under the GNU Lesser General Public
License version 3. You may obtain their complete source code from the link
above. The licence text is at https://www.gnu.org/licenses/lgpl-3.0.txt.

The Windows installer ships the Qt and PySide6 libraries as **separate,
unmodified DLL/PYD files** in the installation folder (`_internal\PySide6`),
so you can replace them with your own compatible build, as the LGPL
allows. The full source code of Macra Downloader itself is also published
under the MIT License, so you can rebuild the whole application against a
modified Qt.

## Not bundled

**FFmpeg** is *not* included. If FFmpeg is installed on your system and on
your `PATH`, yt-dlp uses it to merge separate video and audio streams. Without
it the app downloads the best single-file format instead.

## Full licence texts

The Windows installer and the release archives include the licence files
that ship with each Python package, in the `_internal\*.dist-info` folders.
