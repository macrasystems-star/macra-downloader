#!/usr/bin/env python3
"""Tests for browser integration (hand-off, browser helper, cookies) and the update check.

    QT_QPA_PLATFORM=offscreen python3 -m unittest test_browser_integration -v
"""

import http.server
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import browser_handoff  # noqa: E402
import browser_setup  # noqa: E402
import download_manager as dm  # noqa: E402
import native_host  # noqa: E402
import update_check  # noqa: E402

PAYLOAD = os.urandom(300_000)


class _CookieHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD only with the cookie 'session=ok'; /jump redirects to another host."""
    seen = []          # (path, Cookie header, Referer, User-Agent)
    other_port = None

    def _serve(self, head):
        _CookieHandler.seen.append((self.path, self.headers.get('Cookie'), self.headers.get('Referer'),
                                    self.headers.get('User-Agent')))
        if self.path.startswith('/jump'):
            self.send_response(302)
            self.send_header('Location', f'http://localhost:{_CookieHandler.other_port}/file.bin')
            self.end_headers()
            return
        if self.path.startswith('/secret') and 'session=ok' not in (self.headers.get('Cookie') or ''):
            self.send_response(403)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body = PAYLOAD
        rng = self.headers.get('Range')
        status = 200
        if rng:
            a, _, b = rng[6:].partition('-')
            a, b = int(a), int(b) if b else len(body) - 1
            body, status = body[a:b + 1], 206
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def log_message(self, *args):
        pass


def _server():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _CookieHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.info = Path(self.tmp.name) / 'handoff.json'
        self.got = []
        self.server = browser_handoff.HandoffServer(self.got.append, self.info)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def test_round_trip_and_private_file(self):
        ok, err = browser_handoff.send({'action': 'download', 'url': 'https://example.com/a.zip',
                                        'cookies': [{'name': 'a', 'value': 'b', 'domain': 'example.com'}],
                                        'evil': 'dropped'}, path=self.info)
        self.assertTrue(ok, err)
        self.assertEqual(self.got[0]['url'], 'https://example.com/a.zip')
        self.assertNotIn('evil', self.got[0])
        self.assertEqual(self.got[0]['cookies'][0]['value'], 'b')
        if sys.platform != 'win32':          # Windows protects it with the user's profile folder instead
            self.assertEqual(stat.S_IMODE(self.info.stat().st_mode), 0o600)

    def test_wrong_token_is_refused(self):
        port = json.loads(self.info.read_text())['port']
        with socket.create_connection(('127.0.0.1', port)) as s:
            s.sendall(b'{"token": "guess", "action": "download", "url": "https://x.org/a"}\n')
            reply = json.loads(s.makefile().readline())
        self.assertFalse(reply['ok'])
        self.assertEqual(self.got, [])

    def test_bad_requests(self):
        for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://a.org/x\r\nInjected: 1', ''):
            ok, _ = browser_handoff.send({'action': 'download', 'url': url}, path=self.info)
            self.assertFalse(ok, url)
        ok, _ = browser_handoff.send({'action': 'download', 'url': 'https://a.org/x', 'referrer': 'a\r\nb: c'},
                                     path=self.info)
        self.assertTrue(ok)
        self.assertNotIn('referrer', self.got[-1])
        self.assertEqual(self.got[-1]['url'], 'https://a.org/x')

    def test_stopped_window_is_reported(self):
        self.server.stop()
        ok, err = browser_handoff.send({'action': 'show'}, path=self.info)
        self.assertFalse(ok)
        self.assertIn('not running', err)


class NativeHostTests(unittest.TestCase):
    """Runs native_host.py as a browser would, with the 4-byte length framing."""

    def test_ping_and_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = Path(tmp) / 'handoff.json'
            got = []
            server = browser_handoff.HandoffServer(got.append, info)
            server.start()
            try:
                msgs = [{'type': 'ping'},
                        {'type': 'download', 'url': 'https://example.com/b.iso', 'filename': '/tmp/b.iso',
                         'referrer': 'https://example.com/', 'user_agent': 'UA', 'cookies': []}]
                data = b''.join(struct.pack('=I', len(m)) + m for m in (json.dumps(x).encode() for x in msgs))
                env = dict(os.environ, MACRA_DOWNLOADER_HANDOFF=str(info))
                out = subprocess.run([sys.executable, os.path.join(HERE, 'native_host.py'),
                                      'chrome-extension://oanikjbpkabmkhpllihbcibckeigmjnl/'],
                                     input=data, capture_output=True, env=env, timeout=30).stdout
            finally:
                server.stop()
        replies = []
        while out:
            (n,) = struct.unpack('=I', out[:4])
            replies.append(json.loads(out[4:4 + n]))
            out = out[4 + n:]
        self.assertEqual(replies[0], {'ok': True, 'version': native_host.VERSION})
        self.assertEqual(replies[1], {'ok': True})
        self.assertEqual(got[0]['filename'], '/tmp/b.iso')
        self.assertEqual(got[0]['user_agent'], 'UA')

    def test_manifests(self):
        chrome = browser_setup.manifest('chrome', '/usr/lib/x/native-host')
        self.assertEqual(chrome['name'], native_host_name())
        self.assertEqual(chrome['allowed_origins'], ['chrome-extension://oanikjbpkabmkhpllihbcibckeigmjnl/'])
        firefox = browser_setup.manifest('firefox', 'C:\\x\\host.exe')
        self.assertEqual(firefox['allowed_extensions'], ['downloader@macrasystems.com'])
        ext = json.loads(Path(HERE, 'browser_extension', 'manifest.json').read_text())
        self.assertEqual(ext['browser_specific_settings']['gecko']['id'], browser_setup.FIREFOX_EXTENSION_ID)
        for kind in ('chrome', 'firefox'):   # the files the Windows installer ships
            shipped = json.loads(Path(HERE, 'packaging', 'windows', f'{browser_setup.HOST_NAME}.{kind}.json').read_text())
            self.assertEqual(shipped, browser_setup.manifest(kind, 'MacraDownloaderHost.exe'))
        self.assertIn(f"'{browser_setup.HOST_NAME}'",
                      Path(HERE, 'browser_extension', 'background.js').read_text())


def native_host_name():
    return browser_setup.HOST_NAME


class CookieTests(unittest.TestCase):
    def setUp(self):
        _CookieHandler.seen = []
        self.a, self.b = _server(), _server()
        _CookieHandler.other_port = self.b.server_address[1]
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for s in (self.a, self.b):
            s.shutdown()
            s.server_close()
        self.tmp.cleanup()

    def test_cookies_unlock_download(self):
        url = f'http://127.0.0.1:{self.a.server_address[1]}/secret/file.bin'
        out = Path(self.tmp.name) / 'file.bin'
        with self.assertRaises(RuntimeError):
            dm.download_file(url, out, connections=4, show_progress=False)
        dm.download_file(url, out, connections=4, show_progress=False,
                         headers={'Referer': 'https://page/', 'User-Agent': 'TestUA'},
                         cookies=[{'name': 'session', 'value': 'ok', 'domain': '127.0.0.1', 'path': '/'}])
        self.assertEqual(out.read_bytes(), PAYLOAD)
        self.assertIn(('/secret/file.bin', 'session=ok', 'https://page/', 'TestUA'), _CookieHandler.seen)

    def test_cookies_do_not_follow_redirect_to_other_host(self):
        url = f'http://127.0.0.1:{self.a.server_address[1]}/jump'
        out = Path(self.tmp.name) / 'file.bin'
        dm.download_file(url, out, connections=2, show_progress=False,
                         cookies=[{'name': 'session', 'value': 'ok', 'domain': '127.0.0.1', 'path': '/'}])
        self.assertEqual(out.read_bytes(), PAYLOAD)
        on_other = [c for p, c, _r, _u in _CookieHandler.seen if p == '/file.bin']
        self.assertTrue(on_other)
        self.assertTrue(all(c is None for c in on_other), on_other)


class UpdateCheckTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(update_check.is_newer('v1.10.0', '1.9.3'))
        self.assertTrue(update_check.is_newer('1.5.1', '1.5.0'))
        self.assertFalse(update_check.is_newer('1.5.0', '1.5.0'))
        self.assertFalse(update_check.is_newer('v2.0.0-beta', '1.0.0'))
        self.assertFalse(update_check.is_newer('garbage', '1.0.0'))

    def test_due(self):
        now = 1_000_000.0
        self.assertTrue(update_check.due({}, now))
        self.assertFalse(update_check.due({'last_update_check': now - 3600}, now))
        self.assertTrue(update_check.due({'last_update_check': now - 25 * 3600}, now))
        self.assertTrue(update_check.due({'last_update_check': now + 9999}, now))      # clock went back
        self.assertFalse(update_check.due({'check_updates': False}, now))

    def test_checksums(self):
        sums = ('0' * 64 + '  macra-downloader-linux-x64\n' + 'Ab' * 32 + '  MacraDownloader-Setup-x64.exe\n')
        self.assertEqual(update_check.expected_sha256(sums, 'MacraDownloader-Setup-x64.exe'), 'ab' * 32)
        self.assertIsNone(update_check.expected_sha256(sums, 'other.exe'))


@unittest.skipUnless(os.environ.get('QT_QPA_PLATFORM') == 'offscreen', 'needs QT_QPA_PLATFORM=offscreen')
class WindowHandoffTest(unittest.TestCase):
    """A browser request reaches a real window and downloads with the browser's cookies and file name."""

    def test_window_receives_download(self):
        from PySide6.QtWidgets import QApplication
        import download_store
        import download_manager_gui as gui
        app = QApplication.instance() or QApplication([])
        srv = _server()
        tmp = tempfile.TemporaryDirectory()
        old = (download_store.DB_PATH, update_check.SETTINGS_FILE, os.environ.get('MACRA_DOWNLOADER_HANDOFF'))
        try:
            download_store.DB_PATH = Path(tmp.name) / 'downloads.db'
            update_check.SETTINGS_FILE = Path(tmp.name) / 'settings.json'
            update_check.save_settings({'check_updates': False})
            os.environ['MACRA_DOWNLOADER_HANDOFF'] = str(Path(tmp.name) / 'handoff.json')
            win = gui.DownloadWindow()
            win.clip_check.setChecked(False)
            target = Path(tmp.name) / 'chosen name.bin'
            ok, err = browser_handoff.send({
                'action': 'download', 'url': f'http://127.0.0.1:{srv.server_address[1]}/secret/x?id=7',
                'filename': str(target),
                'cookies': [{'name': 'session', 'value': 'ok', 'domain': '127.0.0.1', 'path': '/'}]})
            self.assertTrue(ok, err)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not (win._items and win._items[-1].status == 'Complete'):
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(win._items[-1].status, 'Complete')
            self.assertEqual(target.read_bytes(), PAYLOAD)
            row = download_store.DownloadStore().all()[-1]
            self.assertNotIn('session', json.dumps(dict(row)))      # cookies never stored
            # A second launch hands its link to this window instead of opening another one.
            self.assertTrue(gui._forward_to_running(['x', 'https://example.invalid/never.bin']))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and len(win._items) < 2:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(len(win._items), 2)
            for item in win._items:
                if item.worker is not None:
                    item.worker.stop()
            win.close()
            self.assertFalse(Path(os.environ['MACRA_DOWNLOADER_HANDOFF']).exists())
        finally:
            download_store.DB_PATH, update_check.SETTINGS_FILE = old[0], old[1]
            if old[2] is None:
                os.environ.pop('MACRA_DOWNLOADER_HANDOFF', None)
            else:
                os.environ['MACRA_DOWNLOADER_HANDOFF'] = old[2]
            srv.shutdown()
            srv.server_close()
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
