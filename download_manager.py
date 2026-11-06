#!/usr/bin/env python3
"""Simple multi-threaded file download manager.

Features:
- Downloads a file using HTTP Range requests split into N pieces.
- Shows a live overall progress bar (tqdm).
- Combines the pieces into the final output file.
- Falls back to a single connection when the server does not support
  byte ranges or does not advertise Content-Length.

Usage:
    python download_manager.py URL [-o OUTPUT] [-n NUM_CONNECTIONS]

Dependencies: requests, tqdm
"""

import argparse
import os
import sys
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
from tqdm import tqdm


CHUNK_SIZE = 8192


class DownloadAborted(Exception):
    """Raised when a download is paused/canceled via *cancel_check*.

    Part files are left on disk so a later run can resume from them.
    """

def get_file_size(url: str, session: requests.Session) -> int:
    """Return the size of the remote file in bytes using a HEAD request.
    Returns None if the server does not provide a Content-Length.
    """
    resp = session.head(url, allow_redirects=True)
    resp.raise_for_status()
    size = resp.headers.get('Content-Length')
    return int(size) if size is not None else None


def download_range(url: str, start: int, end: int, part_path: Path,
                    prog, lock: threading.Lock, session: requests.Session,
                    progress_callback=None, cancel_check=None):
    """Download a byte range [start, end] (inclusive) to part_path.

    Resume-aware: if ``part_path`` already holds some or all of the range
    (e.g. from an earlier, interrupted run days ago), the missing tail is
    appended rather than re-downloaded. A part that is already complete on
    disk is skipped entirely, with no network request.

    If ``cancel_check`` returns True mid-stream, the part is left as-is and
    DownloadAborted is raised so the whole download can pause/resume later.

    Updates the shared tqdm progress bar as chunks are written.
    """
    expected = end - start + 1
    existing = part_path.stat().st_size if part_path.exists() else 0
    if existing >= expected:
        return  # this part is already fully on disk

    headers = {'Range': f'bytes={start + existing}-{end}'}
    mode = 'ab' if existing > 0 else 'wb'
    with session.get(url, headers=headers, stream=True) as r:
        r.raise_for_status()
        with open(part_path, mode) as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if cancel_check is not None and cancel_check():
                    raise DownloadAborted('paused by user')
                if not chunk:
                    continue
                f.write(chunk)
                with lock:
                    if prog is not None:
                        prog.update(len(chunk))
                    if progress_callback:
                        progress_callback(len(chunk))


def _download_unknown_size(url: str, output_path: Path, session: requests.Session,
                            progress_callback=None, cancel_check=None):
    """Single-stream download for servers that do not report Content-Length.
    Writes directly to output_path without range splitting.
    Supports cooperative pause via *cancel_check* (partial file is kept).
    """
    with session.get(url, stream=True) as r:
        r.raise_for_status()
        with open(output_path, 'ab' if output_path.exists() else 'wb') as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if cancel_check is not None and cancel_check():
                    raise DownloadAborted('paused by user')
                if chunk:
                    f.write(chunk)
                    if progress_callback:
                        progress_callback(len(chunk))


def combine_parts(part_files, output_path: Path):
    """Concatenate part files into the final output file and delete parts."""
    with open(output_path, 'wb') as out_f:
        for part in part_files:
            with open(part, 'rb') as pf:
                while True:
                    data = pf.read(CHUNK_SIZE)
                    if not data:
                        break
                    out_f.write(data)
    # Cleanup temporary parts
    for part in part_files:
        try:
            os.remove(part)
        except OSError:
            pass


def download_file(url: str, output_path, connections: int = 32,
                  show_progress: bool = True, progress_callback=None,
                  cancel_check=None) -> Path:
    """Download `url` into `output_path` using up to `connections` pieces.

    Returns the resulting Path. Raises RuntimeError on download failure and
    DownloadAborted when *cancel_check* fires (part files are kept so the
    download can resume later).
    If `progress_callback` is supplied it is invoked with the number of
    bytes written since the last call (thread-safe; may be called from
    multiple worker threads).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    try:
        total_size = get_file_size(url, session)
    except Exception as e:
        raise RuntimeError(f'Failed to obtain file size: {e}') from e

    # Determine whether the server supports byte ranges.
    accept_ranges = ''
    try:
        accept_ranges = session.head(url, allow_redirects=True).headers.get('Accept-Ranges', '')
    except Exception:
        pass

    if total_size is None:
        # Server did not provide Content-Length; stream with a single connection.
        sys.stderr.write('Server did not report file size; using a single connection.\n')
        _download_unknown_size(url, output_path, session, progress_callback,
                               cancel_check)
        return output_path

    if 'bytes' not in accept_ranges.lower():
        sys.stderr.write('Server does not support byte ranges; '
                         'using a single connection.\n')
        connections = 1

    # Compute byte ranges for each piece.
    part_size = math.ceil(total_size / connections)
    ranges = []
    for i in range(connections):
        start = i * part_size
        end = min(start + part_size - 1, total_size - 1)
        if start > end:
            break
        ranges.append((start, end))

    lock = threading.Lock()
    prog = tqdm(total=total_size, unit='B', unit_scale=True,
                desc='Downloading', dynamic_ncols=True, disable=not show_progress)

    part_files = []
    try:
        with ThreadPoolExecutor(max_workers=connections) as executor:
            futures = []
            for idx, (start, end) in enumerate(ranges):
                part_path = Path(f'{output_path}.part{idx}')
                part_files.append(part_path)
                futures.append(executor.submit(
                    download_range, url, start, end, part_path,
                    prog, lock, session, progress_callback, cancel_check))
            for future in as_completed(futures):
                try:
                    future.result()
                except DownloadAborted:
                    # Cancel the rest; keep .part files for resume.
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as exc:
                    raise RuntimeError(f'Error downloading a part: {exc}') from exc
        combine_parts(part_files, output_path)
    finally:
        prog.close()

    return output_path


def main():
    parser = argparse.ArgumentParser(description='Multi-threaded file download manager')
    parser.add_argument('url', help='URL / magnet / .torrent path of the file to download')
    parser.add_argument('-o', '--output', help='Output file name (default: derived from URL)', default=None)
    parser.add_argument('-n', '--connections', type=int,
                        help='Number of parallel connections (pieces)',
                        default=32)
    parser.add_argument('--no-progress', action='store_true', help='Disable the progress bar')
    args = parser.parse_args()

    url = args.url
    # Torrent / magnet passthrough on the CLI (needs libtorrent installed).
    try:
        from torrent_downloader import is_torrent_source, download_torrent
        if is_torrent_source(url):
            output_name = args.output or None
            dest_dir = str(Path(output_name).parent) if output_name else '.'
            final = download_torrent(url, dest_dir or '.')
            print(f'Download completed: {final}')
            return
    except RuntimeError as e:
        sys.stderr.write(f'Error: {e}\n')
        sys.exit(1)
    except ImportError:
        pass
    output_name = args.output or os.path.basename(url.split('?')[0]) or 'downloaded_file'
    output_path = Path(output_name)

    try:
        download_file(url, output_path, connections=args.connections,
                      show_progress=not args.no_progress)
    except RuntimeError as e:
        sys.stderr.write(f'Error: {e}\n')
        sys.exit(1)

    print(f'Download completed: {output_path}')


if __name__ == '__main__':
    main()
