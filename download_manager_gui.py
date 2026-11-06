#!/usr/bin/env python3
"""Graphical Download Manager -- Windows-styled desktop app (PySide6/Qt).

A Windows-like main window with a URL entry field, save-folder picker,
selectable parallel-connection count (default 32), an "Add Download" button,
and a table of downloads (name, live progress bar, transfer rate, total size,
estimated time left, status). Downloads execute in background worker threads
using the core logic from download_manager.py.

Beyond the basics it:
- shows a live **ETA** (estimated time left) for each active download,
- lets you **delete a finished file** straight from the context menu,
- **persists every download** in a SQLite database (see download_store.py) and
  auto-resumes interrupted downloads from their leftover part files on the
  next launch.

Run directly (requires a display):
    python3 download_manager_gui.py

This module is also the entry point packaged into the single-file
executable built with PyInstaller.
"""

import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGridLayout, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QStatusBar, QStyleFactory, QTableWidget, QVBoxLayout, QWidget,
)

from download_manager import download_file
import media_downloader
import torrent_downloader
from download_store import DownloadStore, RESUMABLE

#: Public product identity (single place to rename the app).
APP_NAME = 'Macra Downloader'
APP_VERSION = '1.2.1'
APP_PUBLISHER = 'Macra Systems'
APP_HOMEPAGE = ''  # set to the project website once it is live

#: Characters Windows forbids in file names (also unsafe on most filesystems).
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
#: Device names Windows reserves regardless of extension.
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL',
                   *(f'COM{i}' for i in range(1, 10)),
                   *(f'LPT{i}' for i in range(1, 10))}


def _safe_filename(name, fallback='downloaded_file'):
    """Decode %-escapes and strip characters that are illegal in file names."""
    from urllib.parse import unquote
    name = _UNSAFE_NAME.sub('_', unquote(name or '')).strip().rstrip('. ')
    if not name:
        return fallback
    if os.path.splitext(name)[0].upper() in _RESERVED_NAMES:
        name = '_' + name
    return name[:200]


def _is_torrent_input(url: str) -> bool:
    """True for magnets / hashes / .torrent paths handled by libtorrent."""
    try:
        return torrent_downloader.is_torrent_source(url)
    except Exception:
        return False



# --------------------------------------------------------------------------
# Speed/ETA smoothing
# ---------------------------------------------------------------------------
#: How many seconds of history the speed average is computed over. A wider
#: window makes the speed (and therefore the ETA) feel calmer / more gradual.
SPEED_WINDOW = 2.5
#: Don't record a new sample more often than this (downsampling).
MIN_SAMPLE_GAP = 0.15
#: Exponential-moving-average weight applied to a fresh speed reading. Higher
#: values make the displayed speed follow the real speed more quickly.
SPEED_ALPHA = 0.55
#: Minimum seconds between redraws of the Speed / ETA cells, so they tick at a
#: readable pace instead of flickering on every received chunk.
DISPLAY_TICK = 0.5


# --------------------------------------------------------------------------
# Worker that runs a download in a background thread and reports progress to
# the UI thread via queued Qt signals.
# ---------------------------------------------------------------------------
class DownloadWorker(QObject):
    # NOTE: this must stay `object`, not `int`. Qt maps a bare `int` signal
    # signature to a 32-bit C `int`, so any cumulative byte count above
    # 2**31 - 1 (2 GiB) silently wraps around to a negative number on its way
    # to the UI thread. Because the slot keeps `max(downloaded, value)`, the
    # displayed counter then freezes forever at 2147475456 bytes (the last
    # whole 8 KiB chunk below the limit) even though the download keeps
    # running fine. Windows 11 ISOs are ~8.4 GB, so this hit immediately.
    # `object` carries the Python int through untouched (64-bit safe).
    progress = Signal(object)    # cumulative bytes downloaded so far
    speed = Signal(float)        # instantaneous bytes/second
    finished_ok = Signal(str, str)  # (output path, filename)
    failed = Signal(str, str)    # (reason, filename)
    paused = Signal()            # worker was paused/canceled by the user

    def __init__(self, url, dest, filename, connections):
        super().__init__()
        self.url = url
        self.dest = Path(dest)
        self.filename = filename
        self.connections = connections
        self._lock = threading.Lock()
        self._running = True
        self._paused = False     # pause flag: aborts network loops cooperatively
        self._current = 0
        self._speed_smooth = 0.0
        self._samples = deque()   # (monotonic_time, cumulative_bytes) history
        self._abs_baseline_set = False  # first _set_current() call seen?
        self.total = None

    def stop(self):
        with self._lock:
            self._running = False

    def pause(self):
        """Request a cooperative pause (DownloadAborted -> Paused status)."""
        with self._lock:
            self._paused = True
            self._running = False

    def _cancel_check(self):
        with self._lock:
            return not self._running or self._paused

    def _was_paused(self):
        with self._lock:
            return self._paused

    def _head_size(self):
        import requests
        with requests.Session() as s:
            h = s.head(self.url, allow_redirects=True, timeout=15)
            h.raise_for_status()
            cl = h.headers.get('Content-Length')
            return int(cl) if cl else None

    def _existing_bytes(self, output):
        """Sum of bytes already on disk in leftover part files (for resume)."""
        total = 0
        try:
            for part in output.parent.glob(f'{output.name}.part*'):
                try:
                    total += part.stat().st_size
                except OSError:
                    pass
        except OSError:
            pass
        return total

    def run(self):
        from download_manager import DownloadAborted
        output = self.dest / self.filename
        try:
            # Torrents/magnets go through the libtorrent engine.
            if _is_torrent_input(self.url):
                self._run_torrent(output)
                return
            # Media sites (YouTube etc.) are handled by the yt-dlp engine.
            if media_downloader.is_ytdlp_url(self.url):
                self._run_media(output)
                return
            self.total = self._head_size()
            # Account for any bytes already downloaded in a previous session so
            # the gauge / ETA reflect true progress (resume support).
            existing = self._existing_bytes(output)
            with self._lock:
                self._current = existing
                self._samples.clear()
                self._speed_smooth = 0.0
            if existing:
                self.progress.emit(self._current)
            if not self._running:
                self._emit_paused_or_canceled()
                return
            download_file(self.url, output, connections=self.connections,
                          show_progress=False, progress_callback=self._progress,
                          cancel_check=self._cancel_check)
            if not self._running:
                self._emit_paused_or_canceled()
                return
            self.finished_ok.emit(str(output), self.filename)
        except DownloadAborted:
            self._emit_paused_or_canceled()
        except Exception as exc:  # noqa: BLE001
            if self._was_paused() or not self._running:
                self._emit_paused_or_canceled()
            else:
                self.failed.emit(repr(exc), self.filename)

    def _emit_paused_or_canceled(self):
        """Distinguish Pause (resume later) from Cancel on worker teardown."""
        if self._was_paused():
            self.paused.emit()
        else:
            self.failed.emit('canceled by user', self.filename)

    def _set_total(self, n):
        with self._lock:
            self.total = int(n)

    def _set_current(self, n):
        """Replace (not add to) the downloaded-bytes counter.

        Used by engines (torrent) that report an authoritative absolute
        byte count rather than a per-chunk delta -- notably on resume,
        where the previous session's persisted count must be corrected to
        whatever the engine itself now reports as verified on disk,
        instead of having new deltas pile on top of a stale/duplicated
        baseline.

        The very first call after a (re)start may jump by a large amount
        (a resume's already-verified bytes, reported all at once); that
        jump must not register as a burst of "speed", so the sample
        window is re-baselined only for that first call. Every call after
        that behaves like normal windowed speed smoothing.
        """
        now = time.monotonic()
        with self._lock:
            first = not self._abs_baseline_set
            self._abs_baseline_set = True
            backward = int(n) < self._current
            self._current = int(n)
            if first or backward:
                self._samples.clear()
            if (not self._samples
                    or now - self._samples[-1][0] >= MIN_SAMPLE_GAP):
                self._samples.append((now, self._current))
            cutoff = now - SPEED_WINDOW
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            spd = self._speed_smooth
            if len(self._samples) >= 2:
                t0, b0 = self._samples[0]
                t1, b1 = self._samples[-1]
                dt = t1 - t0
                if dt > 0:
                    windowed = (b1 - b0) / dt
                    self._speed_smooth = (
                        self._speed_smooth * (1.0 - SPEED_ALPHA)
                        + windowed * SPEED_ALPHA)
                    spd = self._speed_smooth
            if first:
                spd = 0.0
        self.progress.emit(self._current)
        self.speed.emit(spd)

    def _run_torrent(self, output):
        """Download a magnet/.torrent through the libtorrent engine."""
        if not self._running:
            self._emit_paused_or_canceled()
            return
        try:
            disp = torrent_downloader.magnet_name(self.url, self.filename)
        except Exception:
            disp = self.filename
        with self._lock:
            self.total = self.total or None
        self.progress.emit(self._current)

        def total_cb(n):
            self._set_total(n)

        def progress_cb(n):
            if self._running:
                self._set_current(n)

        try:
            final = torrent_downloader.download_torrent(
                self.url, str(self.dest),
                progress_callback=progress_cb,
                total_callback=total_cb,
                cancel_check=self._cancel_check)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or repr(exc)
            if self._was_paused() or 'cancel' in msg.lower():
                self._emit_paused_or_canceled()
            elif self._running:
                self.failed.emit(repr(exc), disp)
            else:
                self._emit_paused_or_canceled()
            return
        if not self._running:
            self._emit_paused_or_canceled()
            return
        self.finished_ok.emit(final, os.path.basename(final))

    def _run_media(self, output):
        """Download a media URL (YouTube etc.) through the yt-dlp engine."""
        def total_cb(n):
            self._set_total(n)

        def progress_cb(n):
            if self._running:
                self._progress(n)

        try:
            final = media_downloader.download_media(
                self.url, self.dest, None,          # use the real page title
                progress_callback=progress_cb,
                total_callback=total_cb,
                cancel_check=self._cancel_check)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or repr(exc)
            if self._was_paused() or 'cancel' in msg.lower():
                self._emit_paused_or_canceled()
            elif self._running:
                self.failed.emit(repr(exc), self.filename)
            else:
                self._emit_paused_or_canceled()
            return
        if not self._running:
            self._emit_paused_or_canceled()
            return
        self.finished_ok.emit(final, os.path.basename(final))

    def _progress(self, n):
        now = time.monotonic()
        with self._lock:
            self._current += n
            # Downsample: only record a sample every MIN_SAMPLE_GAP seconds.
            if (not self._samples
                    or now - self._samples[-1][0] >= MIN_SAMPLE_GAP):
                self._samples.append((now, self._current))
            # Keep only the last SPEED_WINDOW seconds of history.
            cutoff = now - SPEED_WINDOW
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            # Average speed across the whole window (start -> end of history).
            spd = self._speed_smooth
            if len(self._samples) >= 2:
                t0, b0 = self._samples[0]
                t1, b1 = self._samples[-1]
                dt = t1 - t0
                if dt > 0:
                    windowed = (b1 - b0) / dt
                    # EMA smooths out short bursts / dips on the display.
                    self._speed_smooth = (
                        self._speed_smooth * (1.0 - SPEED_ALPHA)
                        + windowed * SPEED_ALPHA)
                    spd = self._speed_smooth
        self.progress.emit(self._current)
        self.speed.emit(spd)


class DownloadItem:
    """Per-download state plus the widgets shown in the table row."""

    def __init__(self, name, url, dest, connections):
        self.name = name
        self.url = url
        self.dest = Path(dest)
        self.connections = connections
        self.total = None
        self.downloaded = 0
        self.speed = 0.0
        self.status = 'Queued'
        self.worker = None
        self.thread = None         # background thread running worker.run()
        self.db_id = None          # row id in the persistent download store
        self.file_path = None      # final combined file path once complete
        self.name_label = None
        self.bar = None
        self.speed_label = None
        self.size_label = None
        self.status_label = None
        self.eta_label = None
        self._last_display = 0.0   # throttle for the Speed / ETA cells
        self._last_db_save = 0.0   # throttle for DB progress persistence



# ---------------------------------------------------------------------------
# Main window - a Windows-like download manager.
# ---------------------------------------------------------------------------
class DownloadWindow(QMainWindow):
    WINDOWS_BLUE = '#0078d7'

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(880, 560)
        self._items = []
        self._default_dest = str(Path.home() / 'Downloads')
        self._clip_enabled = True
        self._last_clip = ''
        self.store = DownloadStore()
        self._build_ui()
        self._apply_style()
        self._restore_sessions()
        self._install_clipboard_monitor()
        # Accept a URL/magnet passed on the command line (browser hook /
        # .desktop file DropAction):  download-manager "magnet:?..." .
        try:
            import sys as _sys
            for _a in _sys.argv[1:]:
                _a = _a.strip().strip('"').strip("'")
                if _looks_like_url(_a):
                    self.url_edit.setText(_a)
                    self._add_download()
                    break
        except Exception:
            pass
        # One-second tick keeps the ETA countdown live even between progress
        # callbacks.
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._tick_eta)
        self._eta_timer.start()
        self.statusBar().showMessage('Ready')

    # ----- UI construction ------------------------------------------------
    def _build_ui(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu('&File')
        add_act = QAction('&Add Download...', self)
        add_act.setShortcut('Ctrl+D')
        add_act.triggered.connect(self._add_download)
        file_menu.addAction(add_act)

        clear_act = QAction('C&lear Finished', self)
        clear_act.triggered.connect(self._clear_finished)
        file_menu.addAction(clear_act)
        file_menu.addSeparator()

        exit_act = QAction('E&xit', self)
        exit_act.setShortcut(QKeySequence.StandardKey.Quit)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        help_menu = menubar.addMenu('&Help')
        about_act = QAction('&About', self)
        about_act.triggered.connect(self._about)
        help_menu.addAction(about_act)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(10)

        # ---- form row ----
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)

        form.addWidget(QLabel('URL:'), 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            'https://example.com/file.zip  |  magnet:?xt=...  |  /path/to.torrent')
        self.url_edit.returnPressed.connect(self._add_download)
        form.addWidget(self.url_edit, 0, 1)

        torrent_btn = QPushButton('Open Torrent...')
        torrent_btn.setToolTip('Pick a .torrent file on this computer and start downloading it')
        torrent_btn.clicked.connect(self._open_torrent)
        form.addWidget(torrent_btn, 0, 2)

        form.addWidget(QLabel('Save to:'), 1, 0)
        self.dest_edit = QLineEdit(self._default_dest)
        form.addWidget(self.dest_edit, 1, 1)

        folder_btn = QPushButton('Change...')
        folder_btn.setToolTip('Choose the folder downloads are saved to')
        folder_btn.clicked.connect(self._browse)
        form.addWidget(folder_btn, 1, 2)

        form.addWidget(QLabel('Connections:'), 2, 0)
        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setValue(32)
        form.addWidget(self.conn_spin, 2, 1)

        self.clip_check = QCheckBox('Monitor clipboard for URLs')
        self.clip_check.setChecked(True)
        self.clip_check.toggled.connect(self._toggle_clip)
        form.addWidget(self.clip_check, 2, 2)

        add_btn = QPushButton('Add Download')
        add_btn.setDefault(True)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_download)
        self._add_btn = add_btn             # coloured in _apply_style (light or dark scheme)
        form.addWidget(add_btn, 0, 3, 3, 1, Qt.AlignVCenter)

        root.addLayout(form)

        # ---- table ----
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ['Name', 'Progress', 'Speed', 'Size', 'ETA', 'Status'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        root.addWidget(self.table, 1)

        # status bar
        self.statusBar()

    def _apply_style(self):
        # A dark desktop style: follow it, built from that theme's own colours. Otherwise the Windows-like light look.
        colours = _theme_colours()
        self._scheme = _dark_scheme(colours) if colours and _luma(colours['theme_bg_color']) < 0.4 else None
        s = self._scheme
        if s:
            QApplication.instance().setStyle('fusion')
            QApplication.instance().setPalette(_dark_palette(s))
            self.setStyleSheet(_DARK_QSS.format(**s))
            accent, hover, text = s['accent'], s['accent_hover'], s['accent_fg']
        else:
            for name in ('windowsvista', 'windows11', 'windows', 'fusion'):
                if name in QStyleFactory.keys():
                    QApplication.instance().setStyle(name)
                    break
            self.setStyleSheet(_WINDOWS_QSS)
            accent, hover, text = self.WINDOWS_BLUE, '#106ebe', 'white'
        self._add_btn.setStyleSheet(
            'QPushButton{background:%s;color:%s;font-weight:bold;'
            'padding:7px 18px;border:none;border-radius:3px;}'
            'QPushButton:hover{background:%s;}' % (accent, text, hover))


    # ----- download actions ------------------------------------------------
    def _browse(self):
        start = self.dest_edit.text() or self._default_dest
        chosen = QFileDialog.getExistingDirectory(self, 'Select Download Folder', start)
        if chosen:
            self.dest_edit.setText(chosen)

    def _open_torrent(self):
        """Pick a .torrent file from this computer and start downloading it straight away."""
        start = getattr(self, '_last_torrent_dir', None) or str(Path.home() / 'Downloads')
        if not os.path.isdir(start):
            start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open Torrent File', start, 'Torrent files (*.torrent);;All files (*)')
        if not path:
            return
        self._last_torrent_dir = os.path.dirname(path)
        if not _is_torrent_input(path):
            self.statusBar().showMessage('That is not a .torrent file.', 5000)
            return
        self.url_edit.setText(path)
        self._add_download()

    # ----- clipboard URL capture -----------------------------------------
    def _install_clipboard_monitor(self):
        try:
            cb = QApplication.instance().clipboard()
            cb.dataChanged.connect(self._on_clipboard)
        except Exception:
            pass
        # 1.5s poller covers managers that bypass dataChanged.
        try:
            self._clip_timer = QTimer(self)
            self._clip_timer.setInterval(1500)
            self._clip_timer.timeout.connect(self._poll_clipboard)
            self._clip_timer.start()
        except Exception:
            pass

    def _toggle_clip(self, on):
        self._clip_enabled = bool(on)

    def _on_clipboard(self):
        self._poll_clipboard()

    def _poll_clipboard(self):
        if not getattr(self, '_clip_enabled', True):
            return
        try:
            cb = QApplication.instance().clipboard()
            text = (cb.text() or '').strip()
        except Exception:
            return
        if not text or text == self._last_clip:
            return
        # Only react to a fresh single-line URL/magnet/torrent.
        first = text.splitlines()[0].strip() if text else ''
        if '\n' in text.strip() or ' ' in first.strip():
            # Multi-line or spaced text is not a URL; remember to avoid loops.
            self._last_clip = text
            return
        if len(first) > 2048:
            self._last_clip = text
            return
        if not _looks_like_url(first):
            return
        if (self.url_edit.text().strip() == first
                or any(i.url == first and i.status == 'Downloading'
                       for i in self._items)):
            self._last_clip = text
            return
        self._last_clip = text
        self.url_edit.setText(first)
        self.statusBar().showMessage('URL captured from clipboard.', 3000)
        # Auto-start only when the window is visible & clipboard option on.
        if self.isVisible():
            self._add_download()

    def _add_download(self):
        url = self.url_edit.text().strip()
        # Also accept a dropped .torrent file path pasted with file:// prefix.
        if url.startswith('file://'):
            try:
                from urllib.parse import unquote as _uq
                from urllib.request import url2pathname as _u2p
                url = _u2p(_uq(url[7:]))
                self.url_edit.setText(url)
            except Exception:
                pass
        if not url:
            self.statusBar().showMessage('Enter a URL to download.', 3000)
            return
        dest = self.dest_edit.text().strip() or self._default_dest
        dest_path = Path(dest)
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.statusBar().showMessage(f'Cannot create folder: {exc}', 5000)
            return

        name = self._unique_name(url, dest_path)
        try:
            conns = int(self.conn_spin.value())
        except Exception:
            conns = 32
        item = DownloadItem(name, url, dest_path, conns)
        try:
            item.db_id = self.store.insert(
                url, dest_path, name, conns, status='Queued')
        except Exception:
            item.db_id = None  # non-fatal: in-memory mode if DB fails
        self._items.append(item)
        self._make_row(item)
        self.url_edit.clear()
        self._launch_worker(item)

    def _unique_name(self, url, dest):
        # Torrents resolve their real name from metadata; use a display name.
        if _is_torrent_input(url):
            base = torrent_downloader.magnet_name(url)
            cand = Path(dest) / base
            if not cand.exists():
                return base
            i = 1
            while (Path(dest) / f'{base} ({i})').exists():
                i += 1
            return f'{base} ({i})'
        base = url.split('?', 1)[0].rstrip('/')
        stem = _safe_filename(os.path.basename(base))
        cand = Path(dest) / stem
        if not cand.exists():
            return stem
        root, ext = os.path.splitext(stem)
        i = 1
        while (Path(dest) / f'{root} ({i}){ext}').exists():
            i += 1
        return f'{root} ({i}){ext}'

    def _make_row(self, item):
        row = self.table.rowCount()
        self.table.insertRow(row)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFormat('')
        bar.setTextVisible(False)
        self.table.setCellWidget(row, 0, self._cell(item.name, item.url))
        self.table.setCellWidget(row, 1, bar)
        self.table.setCellWidget(row, 2, QLabel('0 B/s'))
        self.table.setCellWidget(row, 3, QLabel('--'))
        self.table.setCellWidget(row, 4, QLabel('--'))
        self.table.setCellWidget(row, 5, QLabel('Queued'))
        item.name_label = self.table.cellWidget(row, 0)
        item.bar = bar
        item.speed_label = self.table.cellWidget(row, 2)
        item.size_label = self.table.cellWidget(row, 3)
        item.eta_label = self.table.cellWidget(row, 4)
        item.status_label = self.table.cellWidget(row, 5)

    def _cell(self, name, url):
        lab = QLabel(name)
        lab.setToolTip(url)
        lab.setCursor(Qt.PointingHandCursor)
        return lab

    def _launch_worker(self, item):
        worker = DownloadWorker(item.url, item.dest, item.name, item.connections)
        # Resume from already-downloaded bytes shown in the row (DB value).
        try:
            worker._current = int(item.downloaded or 0)
        except Exception:
            pass
        item.worker = worker
        item.status = 'Downloading'
        item.speed = 0.0
        if item.db_id is not None:
            try:
                self.store.update(item.db_id, status='Downloading',
                                  downloaded=item.downloaded,
                                  total=item.total)
            except Exception:
                pass
        self._refresh(item)
        worker.progress.connect(lambda b, it=item: self._on_progress(it, b))
        worker.speed.connect(lambda s, it=item: self._on_speed(it, s))
        worker.finished_ok.connect(
            lambda p, n, it=item: self._on_finished(it, p, n))
        worker.failed.connect(lambda r, n, it=item: self._on_failed(it, r, n))
        worker.paused.connect(lambda it=item: self._on_paused(it))
        thread = threading.Thread(target=worker.run, daemon=True)
        item.thread = thread
        thread.start()


    # ----- progress / status updates ---------------------------------------
    def _on_progress(self, item, total_bytes):
        item.downloaded = max(item.downloaded, total_bytes)
        if item.worker is not None and item.worker.total:
            item.total = item.worker.total
        # Persist progress periodically (max ~every 2s) so pause/restart and
        # crash all resume from close to the real byte count.
        now = time.monotonic()
        if item.db_id is not None and now - item._last_db_save >= 2.0:
            item._last_db_save = now
            try:
                self.store.update(item.db_id, downloaded=item.downloaded,
                                  total=item.total)
            except Exception:
                pass
        self._refresh(item)

    def _on_speed(self, item, spd):
        item.speed = spd

    def _on_finished(self, item, path, name):
        item.status = 'Complete'
        item.file_path = Path(path)
        # For media downloads the real file name (from the page title) may
        # differ from the URL-derived name we started with: reflect it.
        item.name = item.file_path.name
        if item.name_label is not None:
            item.name_label.setText(item.name)
        if item.worker is not None and item.worker.total:
            item.downloaded = item.worker.total
        if item.db_id is not None:
            self.store.update(item.db_id, status='Complete',
                              total=item.downloaded, downloaded=item.downloaded)
        self._refresh(item)
        self.statusBar().showMessage(f'Finished: {name}', 5000)

    def _on_failed(self, item, reason, name):
        # A pause arrives via the dedicated paused signal; anything landing
        # here with a cancel-flavoured reason from an older worker is also
        # treated as a pause so the row stays resumable.
        low = (reason or '').lower()
        if 'cancel' in low or 'paus' in low:
            self._on_paused(item)
            return
        item.status = 'Failed'
        item.speed = 0.0
        if item.db_id is not None and item.worker is not None:
            self.store.update(item.db_id, status='Failed',
                              total=item.worker.total,
                              downloaded=item.downloaded)
        self._refresh(item)
        self.statusBar().showMessage(f'Failed: {name} ({reason})', 8000)

    def _on_paused(self, item):
        item.status = 'Paused'
        item.speed = 0.0
        if item.db_id is not None:
            try:
                total = item.worker.total if item.worker is not None else item.total
                self.store.update(item.db_id, status='Paused',
                                  total=total, downloaded=item.downloaded)
            except Exception:
                pass
        self._refresh(item)
        self.statusBar().showMessage(f'Paused: {item.name}', 3000)

    def _pause_item(self, item):
        if item.worker is not None and item.status == 'Downloading':
            item.worker.pause()
            self.statusBar().showMessage(f'Pausing: {item.name}...', 3000)

    def _resume_item(self, item):
        if item.status in ('Paused', 'Canceled', 'Failed', 'Queued', 'Pending'):
            self.statusBar().showMessage(f'Resuming: {item.name}...', 3000)
            self._launch_worker(item)

    def _tick_eta(self):
        """Second ticker: keep ETA countdowns live for active downloads."""
        for item in self._items:
            if item.status == 'Downloading':
                self._refresh(item)

    def _restore_sessions(self):
        """Reload every record from the download database.

        The full queue is shown on every launch (queue, active, complete,
        failed, paused, canceled). Only genuinely interrupted rows
        (Queued/Pending/Downloading/Failed from a crash or kill) auto-resume;
        rows the user explicitly Paused/Canceled stay put with their progress
        so they can be resumed on demand. Completed rows are verified against
        the file on disk (missing files show as 'Missing').
        """
        try:
            records = self.store.all()
        except Exception:
            return
        for rec in records:
            item = DownloadItem(rec['filename'], rec['url'], rec['dest'],
                                rec['connections'] or 32)
            item.db_id = rec['id']
            item.total = rec['total']
            item.downloaded = rec['downloaded'] or 0
            item.file_path = Path(rec['dest']) / rec['filename']
            self._items.append(item)
            self._make_row(item)
            status = rec['status'] or 'Queued'
            if status in RESUMABLE:
                self._refresh(item)
                self._launch_worker(item)
            elif status in ('Paused', 'Canceled'):
                item.status = status
                self._refresh(item)
            else:
                item.status = 'Complete'
                if not item.file_path.exists():
                    item.status = 'Missing'
                    if item.db_id is not None:
                        try:
                            self.store.update(item.db_id, status='Missing')
                        except Exception:
                            pass
                self._refresh(item)

    def _refresh(self, item):
        total = item.total
        done = item.downloaded
        if total:
            pct = min(100, round(done * 100.0 / total))
        else:
            pct = -1  # unknown total

        if item.bar is not None:
            if pct >= 0:
                item.bar.setRange(0, 100)
                item.bar.setValue(pct)
                item.bar.setFormat(f'{pct}%')
                item.bar.setTextVisible(True)
            elif item.status == 'Downloading':
                # The Qt "busy" marquee (range 0-0) animates on its own
                # timer regardless of app state, so it must only be shown
                # while a download is actually active -- otherwise a
                # Paused/Queued row with an unknown size looks like it is
                # still searching/working.
                item.bar.setRange(0, 0)
                item.bar.setFormat('')
                item.bar.setTextVisible(False)
            else:
                item.bar.setRange(0, 1)
                item.bar.setValue(0)
                item.bar.setFormat(item.status)
                item.bar.setTextVisible(True)

        # Speed and ETA are smoothed in the worker; also throttle their redraw
        # so the numbers tick ~once per second instead of on every chunk.
        # While throttled we keep the last drawn value (no blank flash).
        now = time.monotonic()
        is_dl = item.status == 'Downloading'
        can_show = is_dl and now - item._last_display >= DISPLAY_TICK
        if can_show:
            item._last_display = now
        if item.speed_label is not None:
            if can_show:
                item.speed_label.setText(_fmt_rate(item.speed))
            elif not is_dl:
                item.speed_label.setText('')
        if item.size_label is not None:
            item.size_label.setText(
                _fmt_size(done) if not total
                else f'{_fmt_size(done)} / {_fmt_size(total)}')
        if item.eta_label is not None:
            if can_show:
                item.eta_label.setText(_fmt_eta(_eta_seconds(item)))
            elif not is_dl:
                item.eta_label.setText('--')
        if item.status_label is not None:
            item.status_label.setText(item.status)
            s = getattr(self, '_scheme', None)
            if s:                               # brighter variants that read well on a dark background
                color = {'Downloading': s['accent_text'], 'Complete': '#4cc38a',
                         'Failed': '#ff6b6b', 'Canceled': s['muted'],
                         'Paused': '#ffb454', 'Missing': s['muted'],
                         'Queued': s['muted'], 'Pending': s['muted']}.get(
                             item.status, s['fg'])
            else:
                color = {'Downloading': '#106ebe', 'Complete': '#1a7f37',
                         'Failed': '#c0392b', 'Canceled': '#8a8a8a',
                         'Paused': '#b26a00', 'Missing': '#8a8a8a',
                         'Queued': '#666666', 'Pending': '#666666'}.get(
                             item.status, '#333')
            item.status_label.setStyleSheet(f'color:{color};')

    def _table_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        menu = QMenu(self)
        pause_act = resume_act = retry_act = None
        cancel = delete_file = None
        if item.status == 'Downloading':
            pause_act = menu.addAction('Pause')
            cancel = menu.addAction('Cancel Download')
        elif item.status in ('Paused', 'Queued', 'Pending'):
            resume_act = menu.addAction('Resume')
        elif item.status in ('Failed', 'Canceled', 'Missing'):
            retry_act = menu.addAction('Retry')
        if item.file_path is not None and item.file_path.exists():
            delete_file = menu.addAction('Delete File')
        menu.addSeparator()
        remove_act = menu.addAction('Remove Download')
        open_folder = menu.addAction('Open Folder')
        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is pause_act:
            self._pause_item(item)
        elif chosen is resume_act or chosen is retry_act:
            self._resume_item(item)
        elif chosen is cancel and item.worker is not None:
            item.worker.stop()
            item.status = 'Canceled'
            item.speed = 0.0
            if item.db_id is not None:
                try:
                    self.store.update(item.db_id, status='Canceled',
                                      downloaded=item.downloaded,
                                      total=(item.worker.total
                                             if item.worker else item.total))
                except Exception:
                    pass
            self._refresh(item)
            self.statusBar().showMessage(f'Canceled: {item.name}', 3000)
        elif chosen is delete_file:
            self._delete_file(item)
        elif chosen is remove_act:
            self._remove_download(item)
        elif chosen is open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.dest)))

    def _remove_download(self, item):
        """Stop the download (if active) and remove it completely.

        Unlike Cancel (which keeps the row so it can be retried later) this
        drops the row and its database record for good, and deletes
        whatever has already been written to disk -- leftover ``.partN``
        pieces included -- so a download never has to finish before it can
        be removed.
        """
        still_running = item.status == 'Downloading'
        verb = 'Stop and remove' if still_running else 'Remove'
        ret = QMessageBox.question(
            self, 'Remove Download',
            f'{verb} "{item.name}"?\n\n'
            'Any data already downloaded (including partial pieces) will '
            'be deleted.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        worker = item.worker
        thread = item.thread
        if worker is not None:
            worker.stop()
        if item.db_id is not None:
            try:
                self.store.delete(item.db_id)
            except Exception:
                pass
        dest = item.dest
        name = item.name
        file_path = item.file_path
        self._remove_row(item)
        self.statusBar().showMessage(f'Removing: {name}...', 3000)
        threading.Thread(
            target=self._cleanup_download_files,
            args=(thread, dest, name, file_path), daemon=True).start()

    @staticmethod
    def _cleanup_download_files(thread, dest, name, file_path):
        """Background cleanup for a removed download.

        Waits for the worker thread to actually exit (so it has let go of
        any part file it had open) before deleting the final file (if any)
        plus every leftover file in *dest* whose name matches or is
        prefixed by the download's display name -- e.g. the
        ``name.part0``, ``name.part1``, ... pieces left by the range
        downloader, or a partially-written single-stream file.
        """
        if thread is not None:
            thread.join(timeout=5)
        targets = set()
        if file_path is not None:
            targets.add(Path(file_path))
        try:
            dest = Path(dest)
            for entry in dest.iterdir():
                if entry.name == name or entry.name.startswith(f'{name}.'):
                    targets.add(entry)
        except OSError:
            pass
        for path in targets:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass

    def _delete_file(self, item):
        """Ask for confirmation, delete the finished file, and drop the row."""
        path = item.file_path
        if path is None or not path.exists():
            self.statusBar().showMessage('File not found.', 3000)
            return
        ret = QMessageBox.question(
            self, 'Delete File',
            f'Delete "{path.name}" from disk?\n\n{path}',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            os.remove(path)
        except OSError as exc:
            self.statusBar().showMessage(f'Cannot delete: {exc}', 5000)
            return
        if item.db_id is not None:
            self.store.delete(item.db_id)
        self._remove_row(item)
        self.statusBar().showMessage(f'Deleted: {item.name}', 4000)

    def _remove_row(self, item):
        try:
            idx = self._items.index(item)
        except ValueError:
            return
        self.table.removeRow(idx)
        del self._items[idx]

    def _clear_finished(self):
        # "Clear finished" only hides Completed rows from the table; their DB
        # records are kept so history survives a restart. Use Delete File /
        # Remove to drop a record permanently.
        keep = [i for i in self._items if i.status in ('Queued', 'Downloading')]
        rows_to_remove = [i for i, it in enumerate(self._items)
                          if it not in keep]
        for r in sorted(rows_to_remove, reverse=True):
            self.table.removeRow(r)
            del self._items[r]

    def _about(self):
        site = (f'<p><a href="{APP_HOMEPAGE}">{APP_HOMEPAGE}</a></p>'
                if APP_HOMEPAGE else '')
        QMessageBox.about(self, f'About {APP_NAME}', (
            f'<h3>{APP_NAME} {APP_VERSION}</h3>'
            f'<p>A free, open-source download manager for HTTP(S), '
            f'online video and BitTorrent.</p>'
            f'<p>&copy; 2026 {APP_PUBLISHER}. Released under the MIT License.</p>'
            f'{site}'
            '<p><b>Use responsibly.</b> Only download content you own, that is '
            'freely licensed, or that you otherwise have permission to '
            'download. You are responsible for complying with copyright law '
            'and the terms of service of the websites you use. This software '
            'does not bypass DRM or other copy protection.</p>'
            '<p>Built with Qt / PySide6 (LGPLv3), yt-dlp (Unlicense), '
            'libtorrent (BSD), requests (Apache 2.0) and tqdm (MIT/MPL-2.0). '
            'See THIRD_PARTY_NOTICES for details.</p>'))

    def closeEvent(self, event):
        # Mark anything still downloading as interrupted (not lost): it will
        # auto-resume on the next launch. Paused/Canceled rows are untouched.
        # Progress + totals are flushed so the next session shows the queue
        # exactly as left.
        for item in self._items:
            if item.worker is not None and item.status == 'Downloading':
                item.worker.stop()
        for item in self._items:
            if item.db_id is not None:
                try:
                    if item.status == 'Downloading':
                        total = (item.worker.total if item.worker is not None
                                 else item.total)
                        self.store.update(item.db_id, status='Downloading',
                                          downloaded=item.downloaded,
                                          total=total)
                    elif item.status in ('Paused', 'Canceled', 'Failed',
                                         'Complete', 'Missing'):
                        self.store.update(item.db_id, status=item.status,
                                          downloaded=item.downloaded,
                                          total=item.total)
                except Exception:
                    pass
        try:
            self.store.close()
        except Exception:
            pass
        event.accept()



_WINDOWS_QSS = """
QMainWindow, QDialog { background: #f3f3f3; }
QWidget { color: #1b1b1b; font-family: 'Segoe UI', 'Ubuntu', sans-serif; font-size: 13px; }
QLineEdit, QSpinBox {
    background: white; border: 1px solid #b7b7b7; border-radius: 3px;
    padding: 4px 6px;
}
QLineEdit:focus, QSpinBox:focus { border: 1px solid #0078d7; }
QPushButton { background: #e1e1e1; border: 1px solid #adadad; border-radius: 3px; padding: 5px 12px; }
QPushButton:hover { background: #e5f1fb; border-color: #0078d7; }
QPushButton:pressed { background: #cce4f7; }
QPushButton:disabled { color: #9a9a9a; background:#efefef; }
QTableWidget { background: white; border: 1px solid #c5c5c5; }
QHeaderView::section {
    background: #efefef; color: #333; padding: 5px 6px; font-weight: 600;
    border: none; border-right: 1px solid #dadada; border-bottom: 1px solid #dadada;
}
QProgressBar { border: 1px solid #b7b7b7; border-radius: 2px; background: #f5f5f5; text-align: center; }
QProgressBar::chunk { background: #0078d7; border-radius: 1px; }
QMenuBar { background: #f3f3f3; border-bottom: 1px solid #dadada; }
QMenuBar::item { padding: 4px 8px; background: transparent; }
QMenuBar::item:selected { background: #e5f1ff; }
QMenu { background: #fbfbfb; border: 1px solid #c5c5c5; }
QMenu::item:selected { background: #cce4ff; }
QStatusBar { background: #f3f3f3; border-top: 1px solid #d9d9d9; }
"""

# Dark desktop styles: the look is built from the current GTK theme's own colours, so the app matches whatever dark
# style is active. Read by a small helper process: GTK and Qt are not mixed in one process.
_GTK_COLOURS = r'''
import json, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
ctx = Gtk.Window().get_style_context()
out = {}
for name in ("theme_bg_color", "theme_fg_color", "theme_base_color", "theme_text_color",
             "theme_selected_bg_color", "theme_selected_fg_color", "borders"):
    ok, c = ctx.lookup_color(name)
    if ok:
        out[name] = "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
print(json.dumps(out))
'''


def _theme_colours():
    """The desktop's GTK theme colours, or None (no GTK, no display, a timeout or any error)."""
    import json
    import subprocess
    import sys
    # GTK theming is a Linux desktop concept. On Windows/macOS, running
    # `python3` would open the Microsoft Store stub or flash a console window.
    if not sys.platform.startswith('linux'):
        return None
    try:
        r = subprocess.run(['python3', '-c', _GTK_COLOURS], capture_output=True, text=True, timeout=5)
        c = json.loads(r.stdout)
        return c if 'theme_bg_color' in c and 'theme_fg_color' in c else None
    except Exception:
        return None


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _luma(h):
    r, g, b = _rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def _mix(a, b, t):
    """Blend colour a toward colour b by t (0..1)."""
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def _dark_scheme(c):
    bg, fg = c['theme_bg_color'], c['theme_fg_color']
    accent = c.get('theme_selected_bg_color', '#3daee9')
    return dict(
        bg=bg, fg=fg,
        base=c.get('theme_base_color', _mix(bg, '#000000', 0.25)),
        text=c.get('theme_text_color', fg),
        border=c.get('borders', _mix(bg, fg, 0.22)),
        button=_mix(bg, fg, 0.07), button_hover=_mix(bg, accent, 0.28), button_pressed=_mix(bg, accent, 0.45),
        header=_mix(bg, fg, 0.05), muted=_mix(fg, bg, 0.45),
        accent=accent, accent_fg=c.get('theme_selected_fg_color', '#ffffff'),
        accent_hover=_mix(accent, fg, 0.15), accent_text=_mix(accent, '#ffffff', 0.35))


def _dark_palette(s):
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    for role, key in ((QPalette.Window, 'bg'), (QPalette.WindowText, 'fg'), (QPalette.Base, 'base'),
                      (QPalette.AlternateBase, 'header'), (QPalette.Text, 'text'), (QPalette.Button, 'button'),
                      (QPalette.ButtonText, 'fg'), (QPalette.Highlight, 'accent'), (QPalette.HighlightedText, 'accent_fg'),
                      (QPalette.ToolTipBase, 'base'), (QPalette.ToolTipText, 'fg'), (QPalette.PlaceholderText, 'muted'),
                      (QPalette.Link, 'accent_text')):
        p.setColor(role, QColor(s[key]))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(s['muted']))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(s['muted']))
    return p


_DARK_QSS = """
QMainWindow, QDialog {{ background: {bg}; }}
QWidget {{ color: {fg}; font-family: 'Segoe UI', 'Ubuntu', sans-serif; font-size: 13px; }}
QLineEdit, QSpinBox {{
    background: {base}; color: {text}; border: 1px solid {border}; border-radius: 3px;
    padding: 4px 6px;
}}
QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {accent_text}; }}
QPushButton {{ background: {button}; border: 1px solid {border}; border-radius: 3px; padding: 5px 12px; }}
QPushButton:hover {{ background: {button_hover}; border-color: {accent_text}; }}
QPushButton:pressed {{ background: {button_pressed}; }}
QPushButton:disabled {{ color: {muted}; background: {bg}; }}
QTableWidget {{ background: {base}; border: 1px solid {border}; gridline-color: {border}; }}
QHeaderView::section {{
    background: {header}; color: {fg}; padding: 5px 6px; font-weight: 600;
    border: none; border-right: 1px solid {border}; border-bottom: 1px solid {border};
}}
QProgressBar {{ border: 1px solid {border}; border-radius: 2px; background: {base}; color: {fg}; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 1px; }}
QMenuBar {{ background: {bg}; border-bottom: 1px solid {border}; }}
QMenuBar::item {{ padding: 4px 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {button_hover}; }}
QMenu {{ background: {base}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {accent}; color: {accent_fg}; }}
QStatusBar {{ background: {bg}; border-top: 1px solid {border}; }}
QCheckBox::indicator {{ border: 1px solid {border}; background: {base}; width: 13px; height: 13px; border-radius: 2px; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent_text}; }}
QToolTip {{ background: {base}; color: {fg}; border: 1px solid {border}; }}
"""


def _fmt_size(n):
    n = max(0, int(n))
    for unit in ('B', 'kB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} PB'


def _fmt_rate(n):
    n = max(0, float(n))
    for unit in ('B/s', 'kB/s', 'MB/s', 'GB/s'):
        if n < 1024:
            return f'{n:.0f} {unit}' if unit == 'B/s' else f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} TB/s'


def _eta_seconds(item):
    """Remaining seconds for an active download, or None if incalculable."""
    total = item.total
    done = item.downloaded
    if not total or item.speed <= 0:
        return None
    remaining = total - done
    if remaining <= 0:
        return 0
    return remaining / item.speed


def _fmt_eta(seconds):
    """Human-friendly '1h 23m' / '5m 32s' / '12s' style ETA string."""
    if seconds is None:
        return '--'
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds}s'
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m {secs:02d}s'
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h {mins:02d}m'
    days, hrs = divmod(hours, 24)
    return f'{days}d {hrs:02d}h'


def _looks_like_url(text: str) -> bool:
    """True if *text* is something the app can download (clipboard sniff)."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip().strip('"').strip("'")
    if not t or len(t) > 2048 or '\n' in t or ' ' in t:
        return False
    low = t.lower()
    if low.startswith(('magnet:', 'http://', 'https://', 'ftp://', 'ftps://')):
        return True
    if low.endswith('.torrent'):
        return True
    if low.startswith('file://') and '.torrent' in low:
        return True
    try:
        if torrent_downloader.is_torrent_source(t):
            return True
    except Exception:
        pass
    # Local .torrent path without scheme.
    try:
        from pathlib import Path as _P
        if _P(t).suffix.lower() == '.torrent':
            return True
    except Exception:
        pass
    return False


def main():
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_PUBLISHER)
    # Window/taskbar icon: next to the source, or inside the PyInstaller bundle.
    try:
        from PySide6.QtGui import QIcon
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        icon = base / 'assets' / 'icon-256.png'
        if icon.exists():
            app.setWindowIcon(QIcon(str(icon)))
    except Exception:
        pass
    if sys.platform == 'win32':
        # Own taskbar group/icon instead of the generic python one.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'MacraSystems.MacraDownloader')
        except Exception:
            pass
    win = DownloadWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
