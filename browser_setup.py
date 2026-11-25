#!/usr/bin/env python3
"""Register the browser helper (native_host.py) with Chrome, Edge, Brave, Chromium and Firefox.

A browser only lets the Macra Downloader extension start the helper when a
"host manifest" names it. MacraOS and the Windows installer register it for
everyone; this module does it for the current user, for the portable Linux
build and as a repair from the app's Help menu.

    python3 browser_setup.py --print-manifest chrome|firefox PATH   (packaging)
"""

import json
import os
import sys
from pathlib import Path

HOST_NAME = 'com.macrasystems.downloader'
DESCRIPTION = 'Macra Downloader browser helper'
#: Chrome-family extension IDs allowed to use the helper: the copy MacraOS installs
#: (signed with Macra Systems' extension key). Add the Chrome Web Store and Edge
#: Add-ons IDs here once the extension is published there.
CHROMIUM_EXTENSION_IDS = ['oanikjbpkabmkhpllihbcibckeigmjnl']
FIREFOX_EXTENSION_ID = 'downloader@macrasystems.com'

#: Per-user manifest folders on Linux (each browser looks only in its own).
LINUX_USER_DIRS = {
    'Google Chrome': '~/.config/google-chrome/NativeMessagingHosts',
    'Chromium': '~/.config/chromium/NativeMessagingHosts',
    'Brave': '~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts',
    'Microsoft Edge': '~/.config/microsoft-edge/NativeMessagingHosts',
    'Vivaldi': '~/.config/vivaldi/NativeMessagingHosts',
    'Firefox': '~/.mozilla/native-messaging-hosts',
}
#: Folders the MacraOS package fills for all users (read by Chrome, Chromium, Brave, Firefox).
LINUX_SYSTEM_DIRS = ['/etc/opt/chrome/native-messaging-hosts', '/etc/chromium/native-messaging-hosts',
                     '/usr/lib/mozilla/native-messaging-hosts']
#: Windows registry keys under HKEY_CURRENT_USER (the installer writes the same ones).
WINDOWS_KEYS = {
    'Google Chrome': r'Software\Google\Chrome\NativeMessagingHosts',
    'Microsoft Edge': r'Software\Microsoft\Edge\NativeMessagingHosts',
    'Brave': r'Software\BraveSoftware\Brave-Browser\NativeMessagingHosts',
    'Chromium': r'Software\Chromium\NativeMessagingHosts',
    'Firefox': r'Software\Mozilla\NativeMessagingHosts',
}


def manifest(kind, helper_path):
    """The host manifest for 'chrome' (all Chromium browsers) or 'firefox'."""
    data = {'name': HOST_NAME, 'description': DESCRIPTION, 'path': str(helper_path), 'type': 'stdio'}
    if kind == 'firefox':
        data['allowed_extensions'] = [FIREFOX_EXTENSION_ID]
    else:
        data['allowed_origins'] = [f'chrome-extension://{i}/' for i in CHROMIUM_EXTENSION_IDS]
    return data


def helper_command():
    """The program browsers should start: the console helper next to the Windows
    app, the portable Linux binary itself, or native_host.py from source."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).resolve()
        host = exe.with_name('MacraDownloaderHost.exe')
        return host if host.is_file() else exe
    return Path(__file__).resolve().with_name('native_host.py')


def system_registered():
    """True when MacraOS (or another package) registered the helper for all users."""
    return any(Path(d, HOST_NAME + '.json').is_file() for d in LINUX_SYSTEM_DIRS)


def register_user():
    """Register the helper for the current user. Returns the browsers it was set up for."""
    helper = helper_command()
    done = []
    if sys.platform == 'win32':
        import winreg
        folder = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'MacraDownloader'
        folder.mkdir(parents=True, exist_ok=True)
        files = {}
        for kind in ('chrome', 'firefox'):
            files[kind] = folder / f'{HOST_NAME}.{kind}.json'
            files[kind].write_text(json.dumps(manifest(kind, helper), indent=2), encoding='utf-8')
        for browser, key in WINDOWS_KEYS.items():
            path = files['firefox' if browser == 'Firefox' else 'chrome']
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + '\\' + HOST_NAME) as k:
                winreg.SetValueEx(k, '', 0, winreg.REG_SZ, str(path))
            done.append(browser)
        return done
    if not os.access(helper, os.X_OK):
        try:
            helper.chmod(helper.stat().st_mode | 0o111)
        except OSError:
            pass
    for browser, folder in LINUX_USER_DIRS.items():
        folder = Path(os.path.expanduser(folder))
        # Only browsers that are installed for this user (their profile folder exists).
        if not folder.parent.is_dir():
            continue
        folder.mkdir(exist_ok=True)
        kind = 'firefox' if browser == 'Firefox' else 'chrome'
        (folder / f'{HOST_NAME}.json').write_text(json.dumps(manifest(kind, helper), indent=2))
        done.append(browser)
    return done


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--print-manifest':
        print(json.dumps(manifest(sys.argv[2], sys.argv[3]), indent=2))
    else:
        print('Set up for:', ', '.join(register_user()) or 'no browser found')
