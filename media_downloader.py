#!/usr/bin/env python3
"""Media-site download engine (YouTube and friends) built on yt-dlp.

The main range-based downloader in download_manager.py only works for URLs
that point at a single, linearly-seekable file. Video sites like YouTube do
not expose the movie that way: they serve adaptive DASH segments (separate
video + audio tracks), need format selection and usually audio/video merging.

This module delegates those URLs to **yt-dlp** (bundled with the app), which
handles hundreds of sites. It keeps the app's own interface: a callback is
fed downloaded byte counts (so the existing progress gauge / speed / ETA keep
working), a callback reports the total size when yt-dlp learns it, and the
final output path is returned.

ffmpeg is used by yt-dlp to merge the best video + best audio into one MP4. If
ffmpeg is not on PATH, the download falls back to the best *single* file
format that needs no merging, so the app still works (many videos resolve to a
plain MP4/WebM either way).

yt-dlp is imported lazily so the rest of the app keeps working (and the
offline unit tests keep passing) even if it is missing.
"""

import os
import re
import shutil
from urllib.parse import urlparse

#: Known media hosts (YouTube and other popular yt-dlp sites). A matching URL
#: is routed through yt-dlp instead of the raw range downloader.
MEDIA_HOSTS = (
    'youtube.com', 'youtu.be', 'youtube-nocookie.com',
    'vimeo.com', 'dailymotion.com', 'twitch.tv', 'kick.com',
    'soundcloud.com', 'bandcamp.com', 'tiktok.com', 'twitter.com',
    'x.com', 'instagram.com', 'facebook.com', 'reddit.com',
)
#: Hosts that are aggressively bot/popup-blocked; only treat as media if they
#: are explicitly recognised, otherwise keep raw-download fallback.
_STRICT = ('twitter.com', 'x.com', 'instagram.com', 'facebook.com')

_TITLE_SAFE = re.compile(r'[\\/:*?"<>|]+')


def is_ytdlp_url(url):
    """Return True if *url* looks like a media page for yt-dlp to handle."""
    try:
        host = (urlparse(url).netloc or '').lower()
    except Exception:
        return False
    host = host.split(':', 1)[0]
    if not host:
        return False
    if any(h in host for h in _STRICT):
        return host in _STRICT
    return any(h in host for h in MEDIA_HOSTS)


def _sanitize(name, fallback='video'):
    name = _TITLE_SAFE.sub(' ', name or '').strip()
    return name[:180] or fallback


def _base_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'noprogress': False,
        'retries': 3,
    }
    # YouTube needs a JavaScript runtime; yt-dlp only enables deno by default, so also offer Node.js (22+)
    # and QuickJS (`qjs`) when installed. yt-dlp skips any it does not support.
    runtimes = {name: {} for name, exe in (('node', 'node'), ('quickjs', 'qjs')) if shutil.which(exe)}
    if runtimes:
        opts['js_runtimes'] = runtimes
    return opts


def inspect(url):
    """Fetch page metadata (no download) and return a small info dict.

    Returns a dict with keys: title, ext, filesize_approx, webpage_url.
    Raises RuntimeError on failure (e.g. network / unsupported site).
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError(
            'yt-dlp is not installed; cannot download media links.')
    opts = dict(_base_opts())
    opts.update({'skip_download': True, 'format': 'bestvideo+bestaudio/best'})
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f'Cannot read media page: {exc}') from exc

    # Some playlists / multi-video entries: resolve to the first entry.
    if info.get('entries'):
        info = (info['entries'][0] or {})
    title = _sanitize(info.get('title'))
    # Prefer the filename yt-dlp would write.
    filename = (info.get('_filename') or '').strip() or ''
    ext = os.path.splitext(filename)[1].lstrip('.') if filename else ''
    if not ext:
        ext = 'mp4'
    return {
        'title': title,
        'ext': ext,
        'page': info.get('webpage_url') or url,
    }


def _ffmpeg_available():
    return shutil.which('ffmpeg') is not None


def download_media(url, dest, preferred_name, progress_callback=None,
                   total_callback=None, finished_callback=None,
                   cancel_check=None):
    """Download a media page into *dest*.

    Parameters mirror the app's existing worker interface:
      progress_callback(n) - feeds downloaded delta bytes for smoothing
      total_callback(n)    - tells the UI the total size once known
      finished_callback(path) - called with the final file once complete
      cancel_check()       - return True to abort the download

    Returns the final file Path.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError(
            'yt-dlp is not installed; cannot download media links.')

    dest = os.fspath(dest)
    os.makedirs(dest, exist_ok=True)

    # Determine the output file name from page metadata so we can give the UI
    # a sensible filename and locate the merged result afterwards.
    try:
        meta = inspect(url)
        stem = preferred_name or meta['title']
    except Exception:
        meta = {'title': _sanitize(preferred_name), 'ext': 'mp4'}
        stem = _sanitize(preferred_name)

    use_ffmpeg = _ffmpeg_available()
    fmt = ('bestvideo+bestaudio/best' if use_ffmpeg
           else 'best')
    outtmpl = os.path.join(dest, f'{_sanitize(stem)}.%(ext)s')

    state = {'last_offset': 0, 'finished': []}

    def _hook(d):
        if d.get('status') in ('downloading', 'finished'):
            total = (d.get('total_bytes')
                     or d.get('total_bytes_estimate'))
            if total and total_callback:
                total_callback(int(total))
            downloaded = int(d.get('downloaded_bytes') or 0)
            offset = state['last_offset']
            if downloaded > offset:
                if progress_callback:
                    progress_callback(downloaded - offset)
                state['last_offset'] = downloaded
        if d.get('status') == 'finished':
            fname = d.get('filename')
            if fname:
                state['finished'].append(fname)
        if cancel_check and cancel_check():
            raise DownloadAborted('canceled by user')

    opts = dict(_base_opts())
    opts.update({
        'format': fmt,
        'outtmpl': outtmpl,
        'progress_hooks': [_hook],
        'merge_output_format': 'mp4' if use_ffmpeg else None,
        'noplaylist': True,
    })

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except DownloadAborted:
        raise RuntimeError('canceled by user') from None
    except Exception as exc:  # noqa: BLE001
        # A clear error helps a lot here (blocked page, age gate, geo, etc.)
        msg = str(exc).strip()
        raise RuntimeError(f'Media download failed: {msg or exc!r}') from exc

    # Locate the final merged/downloaded file.
    candidate = os.path.join(dest, f'{_sanitize(stem)}.mp4')
    if os.path.exists(candidate):
        finish = candidate
    elif state['finished']:
        finish = state['finished'][-1]
    else:
        stem_base = _sanitize(stem)
        matches = [os.path.join(dest, f)
                   for f in os.listdir(dest)
                   if f == stem_base or f.startswith(stem_base + '.')]
        finish = (max(matches, key=os.path.getmtime)
                  if matches else None)
    if finish is None or not os.path.exists(finish):
        raise RuntimeError('Media download finished but output file not found')

    if finished_callback:
        finished_callback(finish)
    return os.path.abspath(finish)


class DownloadAborted(Exception):
    """Internal marker used to cancel an in-flight yt-dlp download."""