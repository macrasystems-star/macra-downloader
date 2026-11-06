#!/usr/bin/env python3
"""Persistent download history + resume state (SQLite, standard library only).

The app otherwise lives entirely in memory: close the window and every
in-progress download is forgotten. This module gives the download manager a
small on-disk database so it can remember what it was doing and resume it
later, even after the computer has been off for days.

Database location:
    ~/.local/share/download_manager/downloads.db

Each row is one download record (url, destination folder, file name, parallel
connection count, total/downloaded bytes, status).
"""

import os
import sqlite3
import sys
import time
from pathlib import Path


def _default_app_dir():
    """Per-user data folder: %LOCALAPPDATA% on Windows, XDG on Linux."""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'MacraDownloader'
    return Path.home() / '.local' / 'share' / 'download_manager'


#: Default app data folder + database file (overridable for tests).
#: (Windows: %LOCALAPPDATA%\MacraDownloader\downloads.db)
APP_DIR = _default_app_dir()
DB_PATH = APP_DIR / 'downloads.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    dest        TEXT    NOT NULL,
    filename    TEXT    NOT NULL,
    connections INTEGER NOT NULL DEFAULT 32,
    total       INTEGER,
    downloaded  INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'Queued',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
'''

# Statuses that mean "still needs finishing" and should be auto-resumed on
# the next app launch. 'Canceled'/'Paused' are deliberately excluded: the user
# explicitly stopped those, so they stay put (with progress kept) until the
# user presses Resume/Retry. 'Failed' IS included so failures resume too.
RESUMABLE = ('Queued', 'Pending', 'Downloading', 'Failed')


class DownloadStore:
    """Thin wrapper around the SQLite downloads table."""

    def __init__(self, db_path=None):
        self._path = Path(db_path) if db_path else DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def insert(self, url, dest, filename, connections,
               total=None, downloaded=0, status='Queued'):
        """Add a download record and return its new row id."""
        now = time.time()
        cur = self._conn.execute(
            'INSERT INTO downloads'
            ' (url, dest, filename, connections, total, downloaded, status,'
            '  created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (url, str(dest), filename, int(connections), total,
             int(downloaded), status, now, now))
        self._conn.commit()
        return cur.lastrowid

    def update(self, row_id, **fields):
        """Update a subset of safe columns for the given row id."""
        allowed = {'connections', 'total', 'downloaded', 'status'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates['updated_at'] = time.time()
        sets = ', '.join(f'"{k}"=?' for k in updates)
        self._conn.execute(
            f'UPDATE downloads SET {sets} WHERE id=?',
            (*updates.values(), row_id))
        self._conn.commit()

    def get(self, row_id):
        cur = self._conn.execute('SELECT * FROM downloads WHERE id=?',
                                 (row_id,))
        return cur.fetchone()

    def all(self):
        cur = self._conn.execute(
            'SELECT * FROM downloads ORDER BY created_at')
        return cur.fetchall()

    def delete(self, row_id):
        self._conn.execute('DELETE FROM downloads WHERE id=?', (row_id,))
        self._conn.commit()

    def close(self):
        self._conn.close()


if __name__ == '__main__':
    # Tiny self-check so `python download_store.py` proves the plumbing.
    store = DownloadStore(':memory:')
    rid = store.insert('https://example.com/a.bin', '/tmp', 'a.bin', 32)
    assert store.get(rid)['filename'] == 'a.bin'
    store.update(rid, status='Complete', downloaded=100)
    assert store.get(rid)['status'] == 'Complete'
    store.delete(rid)
    assert store.get(rid) is None
    store.close()
    print('download_store self-check OK')