#!/usr/bin/env python3
"""Browser helper ("native messaging host") for the Macra Downloader extension.

Chrome, Edge, Brave, Chromium and Firefox start this program when the
extension wants to hand over a download. They talk over stdin/stdout: each
message is a 4-byte length (native byte order) followed by that many bytes of
UTF-8 JSON. The browser only starts it for the extension IDs listed in the
host manifest (``com.macrasystems.downloader.json``).

Messages from the extension:
    {"type": "ping"}                      -> {"ok": true, "version": "1.5.0"}
    {"type": "download", "url": ..., "filename": ..., "referrer": ...,
     "user_agent": ..., "cookies": [...]}  -> {"ok": true} or {"ok": false, "error": ...}

Standard library only, so it starts fast and needs no Qt.
"""

import json
import struct
import sys

import browser_handoff

VERSION = '1.5.0'
#: Browsers allow up to 1 MB from the helper and 64 MB to it; we never need more than this.
MAX_MESSAGE = 1024 * 1024


def read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None                          # the browser closed the connection
    (length,) = struct.unpack('=I', header)
    if length > MAX_MESSAGE:
        raise ValueError('message too large')
    data = stream.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode('utf-8'))


def write_message(stream, message):
    data = json.dumps(message).encode('utf-8')
    stream.write(struct.pack('=I', len(data)))
    stream.write(data)
    stream.flush()


def handle(message):
    if not isinstance(message, dict):
        return {'ok': False, 'error': 'bad message'}
    kind = message.get('type')
    if kind == 'ping':
        return {'ok': True, 'version': VERSION}
    if kind == 'download':
        try:
            req = browser_handoff.clean_request(dict(message, action='download'))
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        ok, err = browser_handoff.deliver(req)
        return {'ok': True} if ok else {'ok': False, 'error': err}
    return {'ok': False, 'error': 'unknown message type'}


def main():
    if sys.platform == 'win32':
        import msvcrt
        import os
        for f in (sys.stdin, sys.stdout):     # no CR/LF translation of the binary framing
            msvcrt.setmode(f.fileno(), os.O_BINARY)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            message = read_message(stdin)
        except ValueError as exc:
            write_message(stdout, {'ok': False, 'error': str(exc)})
            return 1
        if message is None:
            return 0
        write_message(stdout, handle(message))


if __name__ == '__main__':
    sys.exit(main())
