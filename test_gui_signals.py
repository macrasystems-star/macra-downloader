#!/usr/bin/env python3
"""Tests for the Qt signal wiring in the GUI worker (byte-count overflow).

These tests exist because of a real bug: ``DownloadWorker.progress`` was
declared as ``Signal(int)``, which Qt implements as a **32-bit** C ``int``.
The Windows 11 ISO is 9,047,330,816 bytes (~8.4 GB), so as soon as the
cumulative counter crossed 2**31 the value handed to the UI slot wrapped
around to a negative number. The slot does ``max(downloaded, value)``, so the
displayed progress froze at 2,147,475,456 bytes (2 GiB) forever while the
download itself kept running normally -- exactly the "stuck at 2.0GB/8.4GB
but speed still 1.6 MB/s" symptom.

Run with (needs PySide6, so use the project venv):

    ./venv/bin/python3 -m unittest test_gui_signals -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# PySide6 needs a QCoreApplication before signals can be emitted.
from PySide6.QtCore import QCoreApplication  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from download_manager_gui import DownloadWorker  # noqa: E402

#: The real file that exposed the bug: Windows 11 Client x64 en-us 26300.9457.
WIN11_ISO_BYTES = 9047330816
#: 32-bit signed int ceiling. The last chunk-aligned value below it is
#: 2**31 - 8192 == 2147475456, i.e. precisely where the counter got stuck.
INT32_MAX = 2 ** 31 - 1
FROZEN_VALUE = 2 ** 31 - 8192


class ProgressSignalWidthTestCase(unittest.TestCase):
    """The progress signal must survive byte counts far beyond 2 GiB."""

    def _emit_and_collect(self, value):
        worker = DownloadWorker('http://example.com/f.bin', '/tmp', 'f.bin', 1)
        seen = []
        worker.progress.connect(seen.append)
        try:
            worker.progress.emit(value)
        except Exception as exc:  # OverflowError etc. must not escape
            self.fail(f'progress.emit({value}) raised {exc!r}')
        return seen

    def _progress_arg_type(self):
        """Qt's C++ signature for the progress signal, e.g. 'int' or 'PyObject'."""
        mo = DownloadWorker.staticMetaObject
        for k in range(mo.methodOffset(), mo.methodCount()):
            m = mo.method(k)
            if m.methodType() != m.MethodType.Signal:
                continue
            sig = bytes(m.methodSignature()).decode('utf-8', 'replace')
            if sig.startswith('progress('):
                return sig[len('progress('):-1]
        self.fail('no progress signal found on DownloadWorker')

    def test_signal_is_not_declared_as_32bit_int(self):
        """Guard against a regression to `Signal(int)`.

        A 32-bit signature shows up as `int` in Qt's meta-object; `object`
        shows up as `PyObject`. Only the latter survives byte counts past 2 GiB.
        """
        self.assertNotEqual(
            'int', self._progress_arg_type(),
            'progress is a 32-bit int signal: byte counts above 2 GiB wrap '
            'negative and the displayed progress freezes')
        self.assertEqual('PyObject', self._progress_arg_type())

    def test_emit_windows11_iso_size_arrives_unchanged(self):
        """A full 8.4 GB ISO size must reach the slot exactly."""
        seen = self._emit_and_collect(WIN11_ISO_BYTES)
        self.assertEqual(len(seen), 1, 'signal did not fire')
        self.assertEqual(seen[0], WIN11_ISO_BYTES,
                         f'byte count was mangled in transit: got {seen[0]!r}')

    def test_emit_just_past_32bit_boundary_stays_positive(self):
        """The value right after the old wrap point must stay positive."""
        for value in (INT32_MAX, INT32_MAX + 1, FROZEN_VALUE + 8192,
                      FROZEN_VALUE + 2 ** 40):
            with self.subTest(value=value):
                seen = self._emit_and_collect(value)
                self.assertEqual(seen[0], value)
                self.assertGreater(seen[0], 0)

    def test_progress_monotonic_past_2gib(self):
        """Reproduce the frozen-counter bug end to end.

        Walks a fake 8.4 GB download forward in 8 KiB chunks, feeding every
        cumulative total through the real signal into a copy of the UI slot's
        `max()` logic. Before the fix this settled on 2147475456 and stopped.
        """
        worker = DownloadWorker('http://example.com/f.bin', '/tmp', 'f.bin', 1)
        displayed = {'downloaded': 0}

        def on_progress(total_bytes):
            # Mirror of MainWindow._on_progress's accounting.
            displayed['downloaded'] = max(displayed['downloaded'], total_bytes)

        worker.progress.connect(on_progress)

        chunk = 8192
        total = 0
        try:
            while total < WIN11_ISO_BYTES:
                total = min(total + chunk, WIN11_ISO_BYTES)
                worker.progress.emit(total)
        except Exception as exc:  # noqa: BLE001
            self.fail(f'emit failed at {total} bytes: {exc!r}')

        self.assertEqual(displayed['downloaded'], WIN11_ISO_BYTES)
        self.assertNotEqual(
            displayed['downloaded'], FROZEN_VALUE,
            'progress froze at the 2 GiB boundary -- 32-bit signal overflow')

    def test_speed_signal_still_float(self):
        """Speed is a rate, not a byte count; float is correct and fine."""
        worker = DownloadWorker('http://example.com/f.bin', '/tmp', 'f.bin', 1)
        seen = []
        worker.speed.connect(seen.append)
        worker.speed.emit(1600000.0)
        self.assertEqual(seen, [1600000.0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
