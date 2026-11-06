#!/usr/bin/env python3
"""Tests for the download manager using a local Range-capable HTTP server.

Runs with the standard-library `unittest`. No external services or pytest
required:

    python3 -m unittest test_download_manager -v
    # or simply:
    python3 test_download_manager.py
"""

import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_manager as dm


class _Handler(http.server.BaseHTTPRequestHandler):
    #: global binary payload served by the test server
    payload = b''
    no_range = False
    no_length = False

    def _respond(self, status, body, extra=None):
        self.send_response(status)
        if not self.no_length or status == 416:
            self.send_header('Content-Length', str(len(body)))
        if not self.no_range:
            self.send_header('Accept-Ranges', 'bytes')
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self._respond(200, _Handler.payload)

    def do_GET(self):
        body = _Handler.payload
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes='):
            spec = rng[len('bytes='):]
            start_str, _, end_str = spec.partition('-')
            start = int(start_str)
            end = int(end_str) if end_str else len(body) - 1
            end = min(end, len(body) - 1)
            if start > end or start >= len(body):
                self._respond(416, b'', {'Content-Range': f'bytes */{len(body)}'})
                return
            chunk = body[start:end + 1]
            self._respond(206, chunk,
                          {'Content-Range': f'bytes {start}-{end}/{len(body)}'})
            return
        self._respond(200, body)

    def log_message(self, *args):
        pass


class ServeHTTP:
    """Start one background HTTP server.

    no_range : drop Accept-Ranges (server without byte-range support).
    no_length: omit Content-Length (exercises the unknown-size path).
    """

    def __init__(self, payload=None, no_range=False, no_length=False):
        _Handler.payload = payload if payload is not None else (b'hello' * 1000)
        _Handler.no_range = no_range
        _Handler.no_length = no_length

        class Local(_Handler):
            pass

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Local)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/file.bin'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class DownloadManagerTestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='dm_test_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _serve(self, **kw):
        srv = ServeHTTP(**kw)
        self.addCleanup(srv.close)
        return srv

    def test_get_file_size(self):
        import requests
        payload = b'0123456789'
        srv = self._serve(payload=payload)
        self.assertEqual(dm.get_file_size(srv.url, requests.Session()),
                         len(payload))

    def test_combine_parts_concatenates_and_cleans_up(self):
        out = Path(self.tmpdir) / 'out.bin'
        p1 = Path(self.tmpdir) / 'p1'
        p2 = Path(self.tmpdir) / 'p2'
        p1.write_bytes(b'AAA')
        p2.write_bytes(b'BBB')
        dm.combine_parts([Path(p1), Path(p2)], out)
        self.assertEqual(out.read_bytes(), b'AAABBB')
        self.assertFalse(p1.exists())
        self.assertFalse(p2.exists())

    def test_multi_connection_range_download(self):
        payload = bytes(range(256)) * 512  # 128 KiB, divisible into pieces
        srv = self._serve(payload=payload)
        out = Path(self.tmpdir) / 'result.bin'
        result = dm.download_file(srv.url, out, connections=4,
                                  show_progress=False)
        self.assertEqual(result, out)
        self.assertEqual(out.read_bytes(), payload)

    def test_single_connection_without_range_support(self):
        payload = b'x' * 50000
        srv = self._serve(payload=payload, no_range=True)
        out = Path(self.tmpdir) / 'no_range.bin'
        dm.download_file(srv.url, out, connections=4, show_progress=False)
        self.assertEqual(out.read_bytes(), payload)

    def test_unknown_size_no_content_length(self):
        payload = b'streamy' * 3000
        srv = self._serve(payload=payload, no_length=True)
        out = Path(self.tmpdir) / 'unknown.bin'
        dm.download_file(srv.url, out, connections=4, show_progress=False)
        self.assertEqual(out.read_bytes(), payload)

    def test_no_stray_part_files_remain(self):
        payload = bytes(range(256)) * 512
        srv = self._serve(payload=payload)
        out = Path(self.tmpdir) / 'r.bin'
        dm.download_file(srv.url, out, connections=3, show_progress=False)
        leftovers = [p for p in Path(self.tmpdir).iterdir()
                     if '.part' in p.name]
        self.assertEqual(leftovers, [])

    def test_resume_reuses_existing_part_files(self):
        """A second run should reuse leftover .part files instead of
        re-downloading everything, and still produce a byte-exact file."""
        payload = bytes(range(256)) * 512  # 128 KiB
        total = len(payload)
        conns = 3
        srv = self._serve(payload=payload)
        out = Path(self.tmpdir) / 'resume.bin'

        # Pre-seed part files exactly along the same ranges download_file will
        # compute: part 0 complete, part 1 half done, part 2 missing.
        part_size = -(-total // conns)  # ceiling division
        ranges = []
        for i in range(conns):
            start = i * part_size
            end = min(start + part_size - 1, total - 1)
            if start > end:
                break
            ranges.append((start, end))
        p0, p1, p2 = (Path(f'{out}.part{i}') for i in range(3))
        p0.write_bytes(payload[ranges[0][0]:ranges[0][1] + 1])
        part1_half = (ranges[1][1] - ranges[1][0] + 1) // 2
        p1.write_bytes(payload[ranges[1][0]:ranges[1][0] + part1_half])
        # p2 deliberately absent

        dm.download_file(srv.url, out, connections=conns,
                         show_progress=False)

        self.assertEqual(out.read_bytes(), payload)
        leftovers = [p for p in Path(self.tmpdir).iterdir()
                     if '.part' in p.name]
        self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
