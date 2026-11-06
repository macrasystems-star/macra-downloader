#!/usr/bin/env python3
"""Torrent download engine built on libtorrent (magnet + .torrent).

Handles magnet: URIs, bare btih hashes, local .torrent files and
http(s) URLs pointing at a .torrent file.

libtorrent is imported lazily so the rest of the app (and offline
unit tests) keep working when it is missing.
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

_BTIH_HEX = re.compile(r'^[0-9a-fA-F]{40}$')
_BTIH_B32 = re.compile(r'^[A-Z2-7]{32}$', re.IGNORECASE)


def is_torrent_source(s: str) -> bool:
    """True if *s* looks like a torrent source (magnet/hash/.torrent)."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip().strip('"').strip("'")
    if not s:
        return False
    low = s.lower()
    if low.startswith('magnet:'):
        return True
    token = s.strip()
    if _BTIH_HEX.match(token) or _BTIH_B32.match(token):
        return True
    if low.endswith('.torrent'):
        return True
    try:
        path = urlparse(s).path or ''
        if path.lower().endswith('.torrent'):
            return True
    except Exception:
        pass
    return False


def magnet_name(source: str, fallback='torrent-download') -> str:
    """Best-effort display name for a magnet/torrent source (no network)."""
    try:
        if source.strip().lower().startswith('magnet:'):
            qs = parse_qs(urlparse(source).query)
            dn = (qs.get('dn') or [None])[0]
            if dn:
                safe = re.sub(r'[\\/:*?"<>|]+', ' ', unquote(dn)).strip()
                return safe[:180] or fallback
    except Exception:
        pass
    try:
        base = urlparse(source).path.rstrip('/').split('/')[-1]
        base = unquote(base)
        if base:
            if base.lower().endswith('.torrent'):
                base = base[:-8]
            safe = re.sub(r'[\\/:*?"<>|]+', ' ', base).strip()
            if safe:
                return safe[:180]
    except Exception:
        pass
    try:
        p = Path(source)
        if p.suffix.lower() == '.torrent':
            return p.stem[:180] or fallback
    except Exception:
        pass
    return fallback


def _require_libtorrent():
    try:
        import libtorrent as lt  # type: ignore
        return lt
    except ImportError as exc:
        raise RuntimeError(
            'Torrent support needs the libtorrent module, not installed. '
            'Install with: pip install libtorrent' +
            ('' if sys.platform != 'win32' else ' (Windows builds include it)') + ' / '
            'sudo apt install python3-libtorrent') from exc


def _fetch_torrent_url(url: str) -> str:
    import requests
    r = requests.get(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    fd, tmp = tempfile.mkstemp(suffix='.torrent', prefix='dm-')
    with os.fdopen(fd, 'wb') as f:
        f.write(r.content)
    return tmp


def download_torrent(source, dest, progress_callback=None,
                     total_callback=None, finished_callback=None,
                     cancel_check=None, timeout_metadata=180,
                     listen_port=6881):
    """Download a torrent/magnet into *dest* via libtorrent.

    *source*: magnet URI, bare btih hash, local .torrent path, or
    http(s) URL of a .torrent file. Returns absolute final path.
    """
    lt = _require_libtorrent()
    source = (source or '').strip().strip('"').strip("'")
    dest = os.fspath(dest)
    os.makedirs(dest, exist_ok=True)
    tmp_torrent = None
    try:
        ses = lt.session({
            'listen_interfaces': f'0.0.0.0:{listen_port}',
            'enable_dht': True, 'enable_lsd': True,
            'enable_natpmp': True, 'enable_upnp': True,
        })
        params = None
        low = source.lower()
        if low.startswith('magnet:'):
            params = lt.parse_magnet_uri(source)
            params.save_path = dest
        elif _BTIH_HEX.match(source.strip()) or _BTIH_B32.match(source.strip()):
            params = lt.parse_magnet_uri(
                f'magnet:?xt=urn:btih:{source.strip()}')
            params.save_path = dest
        elif low.startswith('http://') or low.startswith('https://'):
            if urlparse(source).path.lower().endswith('.torrent'):
                tmp_torrent = _fetch_torrent_url(source)
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(tmp_torrent)
                params.save_path = dest
            else:
                raise RuntimeError('URL is not a .torrent file or magnet.')
        else:
            p = os.path.expanduser(source)
            if not os.path.exists(p):
                raise RuntimeError(f'Torrent file not found: {source}')
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(p)
            params.save_path = dest
        handle = ses.add_torrent(params)
        t0 = time.time()
        while not handle.has_metadata():
            if cancel_check and cancel_check():
                try:
                    ses.remove_torrent(handle)
                except Exception:
                    pass
                raise RuntimeError('canceled by user')
            if time.time() - t0 > timeout_metadata:
                try:
                    ses.remove_torrent(handle)
                except Exception:
                    pass
                raise RuntimeError('Timed out waiting for torrent metadata.')
            time.sleep(0.5)
        name = handle.name()
        total = 0
        try:
            ti = handle.get_torrent_info()
            if ti is not None:
                total = int(ti.total_size() or 0)
        except Exception:
            pass
        if total and total_callback:
            try:
                total_callback(total)
            except Exception:
                pass
        # *progress_callback* is fed the ABSOLUTE number of bytes verified so
        # far (not a delta). This matters for resume: re-adding a magnet to a
        # save_path that already holds partial data makes libtorrent rehash
        # the file and report a large total_wanted_done on the very first
        # status check, even though none of it was downloaded in *this*
        # session. Treating that as a delta on top of an already-persisted
        # byte count double-counts every previously-downloaded byte; reporting
        # the absolute value instead lets the caller simply replace its
        # counter, which is correct whether this is a fresh start or a resume.
        last_reported = -1
        while not handle.is_seed():
            if cancel_check and cancel_check():
                try:
                    ses.remove_torrent(handle)
                except Exception:
                    pass
                raise RuntimeError('canceled by user')
            try:
                st = handle.status()
                wanted = int(st.total_wanted or total or 0)
                done = int(st.total_wanted_done or 0)
                if wanted and total_callback:
                    try:
                        total_callback(wanted)
                    except Exception:
                        pass
                    total = wanted
                if done != last_reported:
                    last_reported = done
                    if progress_callback:
                        try:
                            progress_callback(done)
                        except Exception:
                            pass
            except RuntimeError:
                raise
            except Exception:
                pass
            time.sleep(1.0)
        try:
            st = handle.status()
            done = int(st.total_wanted_done or 0)
            if progress_callback:
                progress_callback(done)
            if total_callback:
                total_callback(int(st.total_wanted or total or done))
        except Exception:
            pass
        final = os.path.join(dest, name) if name else dest
        try:
            ses.remove_torrent(handle)
        except Exception:
            pass
        final_abs = os.path.abspath(final)
        if finished_callback:
            try:
                finished_callback(final_abs)
            except Exception:
                pass
        return final_abs
    finally:
        if tmp_torrent:
            try:
                os.remove(tmp_torrent)
            except OSError:
                pass


class DownloadAborted(Exception):
    """Marker used to cancel an in-flight torrent download."""

