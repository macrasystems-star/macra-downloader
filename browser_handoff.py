#!/usr/bin/env python3
"""Hand downloads from web browsers to the running Macra Downloader window.

Standard library only: the browser helper (native_host.py) imports this
without Qt.

How it works
------------
The open Macra Downloader window listens on 127.0.0.1, on a random port. The
port and a random secret are written to a file only this user can read
(``handoff.json`` in the user's runtime/data folder). A sender reads that file,
connects, and sends one line of JSON that includes the secret; anything without
the right secret is refused, so other users of the computer cannot add downloads.

A request looks like::

    {"token": "...", "action": "download",
     "url": "https://example.com/file.zip",
     "filename": "file.zip",              # optional; may be a full path
     "referrer": "https://example.com/",  # optional
     "user_agent": "Mozilla/5.0 ...",     # optional
     "cookies": [{"name": "a", "value": "b", "domain": "example.com",
                  "path": "/", "secure": true}]}   # optional

or ``{"token": "...", "action": "show"}`` to bring the window to the front.
The answer is one line: ``{"ok": true}`` or ``{"ok": false, "error": "..."}``.

Cookies are kept in memory only (never written to the download history).
"""

import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

#: Largest request accepted (cookies included).
MAX_REQUEST = 256 * 1024
#: Request fields passed on to the window, with their types.
_FIELDS = {'url': str, 'filename': str, 'referrer': str, 'user_agent': str,
           'cookies': list}
_COOKIE_FIELDS = {'name': str, 'value': str, 'domain': str, 'path': str,
                  'secure': bool}


def info_path():
    """Where the window publishes its port and secret (per user)."""
    override = os.environ.get('MACRA_DOWNLOADER_HANDOFF')
    if override:
        return Path(override)
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'MacraDownloader' / 'handoff.json'
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / 'macra-downloader-handoff.json'
    return Path.home() / '.local' / 'share' / 'download_manager' / 'handoff.json'


def clean_request(req):
    """Keep only known fields with the right types; raise ValueError if unusable."""
    if not isinstance(req, dict):
        raise ValueError('request must be a JSON object')
    action = req.get('action', 'download')
    if action == 'show':
        return {'action': 'show'}
    if action != 'download':
        raise ValueError('unknown action')
    out = {'action': 'download'}
    for key, kind in _FIELDS.items():
        value = req.get(key)
        if isinstance(value, kind):
            out[key] = value
    url = out.get('url', '').strip()
    local_torrent = os.path.isabs(url) and url.lower().endswith('.torrent')
    if not (url.lower().startswith(('http://', 'https://', 'ftp://', 'magnet:')) or local_torrent) \
            or len(url) > 8192 or any(c in url for c in '\r\n\x00'):
        raise ValueError('not a downloadable link')
    out['url'] = url
    cookies = []
    for c in out.get('cookies', [])[:300]:
        if not isinstance(c, dict):
            continue
        cc = {k: c[k] for k, kind in _COOKIE_FIELDS.items() if isinstance(c.get(k), kind)}
        if cc.get('name') and 'value' in cc and cc.get('domain'):
            cookies.append(cc)
    out['cookies'] = cookies
    for key in ('filename', 'referrer', 'user_agent'):
        if key in out and (len(out[key]) > 4096 or any(c in out[key] for c in '\r\n\x00')):
            del out[key]
    return out


# ---------------------------------------------------------------------------
# Receiving side (the Macra Downloader window)
# ---------------------------------------------------------------------------
class HandoffServer:
    """Accepts requests on 127.0.0.1 and passes each cleaned request to *callback*.

    *callback* runs on a background thread; the GUI forwards it to the UI
    thread with a queued Qt signal.
    """

    def __init__(self, callback, path=None):
        self._callback = callback
        self._path = Path(path) if path else info_path()
        self._token = secrets.token_urlsafe(32)
        self._sock = None
        self._thread = None
        self._closed = False

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        sock.listen(8)
        self._sock = sock
        port = sock.getsockname()[1]
        self._write_info({'port': port, 'token': self._token, 'pid': os.getpid()})
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return port

    def _write_info(self, data):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + f'.{os.getpid()}.tmp')
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(str(tmp), str(self._path))

    def stop(self):
        self._closed = True
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        # Remove the file only if it is still ours (another window may have taken over).
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
            if data.get('token') == self._token:
                self._path.unlink()
        except (OSError, ValueError):
            pass

    def _serve(self):
        while not self._closed:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            conn.settimeout(10)
            try:
                line = _read_line(conn)
                req = json.loads(line)
                token = req.get('token') if isinstance(req, dict) else None
                if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
                    reply = {'ok': False, 'error': 'not allowed'}
                else:
                    self._callback(clean_request(req))
                    reply = {'ok': True}
            except (OSError, ValueError) as exc:
                reply = {'ok': False, 'error': str(exc) or 'bad request'}
            except Exception as exc:  # noqa: BLE001 - never let a request kill the listener
                reply = {'ok': False, 'error': repr(exc)}
            try:
                conn.sendall((json.dumps(reply) + '\n').encode())
            except OSError:
                pass


def _read_line(conn):
    buf = b''
    while b'\n' not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_REQUEST:
            raise ValueError('request too large')
    return buf.split(b'\n', 1)[0].decode('utf-8')


# ---------------------------------------------------------------------------
# Sending side (browser helper, Macra Browser, a second app launch)
# ---------------------------------------------------------------------------
def send(request, path=None, timeout=10):
    """Send *request* to the running window. Returns (ok, error_text)."""
    try:
        info = json.loads((Path(path) if path else info_path()).read_text(encoding='utf-8'))
        port, token = int(info['port']), str(info['token'])
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'Macra Downloader is not running'
    msg = dict(request, token=token)
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
            s.sendall((json.dumps(msg) + '\n').encode())
            reply = json.loads(_read_line(s) or '{}')
    except (OSError, ValueError) as exc:
        return False, f'Macra Downloader is not running ({exc})'
    if reply.get('ok'):
        return True, ''
    return False, reply.get('error') or 'refused'


def app_command():
    """The command that starts the Macra Downloader window."""
    override = os.environ.get('MACRA_DOWNLOADER_APP')
    if override:
        return [override]
    if getattr(sys, 'frozen', False):
        here = Path(sys.executable).resolve().parent
        for name in ('MacraDownloader.exe', 'macra-downloader', 'download-manager'):
            if (here / name).is_file() and (here / name).resolve() != Path(sys.executable).resolve():
                return [str(here / name)]
        return [sys.executable]           # the Linux one-file build is the app itself
    gui = Path(__file__).resolve().parent / 'download_manager_gui.py'
    return [sys.executable, '-s', str(gui)]


def _launch_app():
    cmd = app_command()
    env = dict(os.environ)
    if getattr(sys, 'frozen', False) and 'LD_LIBRARY_PATH_ORIG' in env:
        env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH_ORIG']
    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
              'stderr': subprocess.DEVNULL, 'env': env, 'close_fds': True}
    if sys.platform == 'win32':
        # Browsers run their helper inside a Windows job that is ended with it:
        # break away so the window stays open.
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
            return
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, **kwargs)
            return
    subprocess.Popen(cmd, start_new_session=True, **kwargs)


def deliver(request, wait=20.0):
    """Send *request*, starting Macra Downloader first if it is not running.

    Returns (ok, error_text).
    """
    ok, err = send(request)
    if ok:
        return ok, err
    if err not in ('',) and not err.startswith('Macra Downloader is not running'):
        return ok, err                       # running, but it refused this request
    try:
        _launch_app()
    except OSError as exc:
        return False, f'cannot start Macra Downloader: {exc}'
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.25)
        ok, err = send(request)
        if ok or not err.startswith('Macra Downloader is not running'):
            return ok, err
    return False, 'Macra Downloader did not start in time'


def main(argv=None):
    """``browser_handoff.py`` reads one request (JSON) from stdin and delivers it.

    Exit status 0 = Macra Downloader accepted it. Used by Macra Browser, so
    cookies never appear on a command line.
    """
    try:
        req = clean_request(json.loads(sys.stdin.read(MAX_REQUEST)))
    except ValueError as exc:
        print(f'macra-downloader: {exc}', file=sys.stderr)
        return 2
    ok, err = deliver(req)
    if not ok:
        print(f'macra-downloader: {err}', file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
