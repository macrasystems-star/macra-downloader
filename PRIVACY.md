# Macra Downloader privacy policy

Effective date: 25 November 2026

Macra Downloader is a free, open-source download manager published by Macra
Systems. This policy explains what happens to your information when you use
the app. In short: **Macra Systems does not collect, receive or store any
personal data from the app.**

## What Macra Systems collects

Nothing. The app has no accounts, no analytics, no usage statistics, no crash
reporting and no advertising. It never sends information to Macra Systems. You
can confirm this in the published source code.

## Update check

Once a day the app asks GitHub's public API whether a newer version has been
released. GitHub receives the request like any visit (your IP address, and the
app's name and version in the request) under the
[GitHub Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).
Nothing about you or your downloads is included. You can turn this off in
*Help → Check for Updates Automatically*.

## Browser extension

The optional Macra Downloader browser extension sends each download you start
in your browser to the Macra Downloader app on the same computer: the link, the
file name, the page you came from, your browser's user agent and the site's
cookies for that link (so downloads that need you to be logged in work). This
goes only to the app on your computer, through your browser's "native messaging"
helper, and is never sent to Macra Systems or anyone else. The cookies are kept
in memory while the download runs and are not saved in the download history.
Downloads from private or incognito windows are never sent. The extension stores
only one setting (whether the hand-over is on) in your browser.

## What stays on your computer

To show your download list and resume unfinished downloads, the app keeps a
small local database on your computer containing, for each download, the link,
the file name, the destination folder, its size, its progress and its status.

- Windows: `%LOCALAPPDATA%\MacraDownloader\downloads.db`
- Linux: `~/.local/share/download_manager/downloads.db`

It also keeps a small settings file next to it (`settings.json`: whether to
check for updates, when it last checked, and a version you chose to skip).

This file never leaves your computer. You can delete individual entries in the
app, or delete the file to erase the whole history. Uninstalling the app on
Windows keeps this file so a reinstall keeps your history. Delete the
`MacraDownloader` folder yourself if you want it gone.

## Clipboard monitoring

When the "Monitor clipboard for URLs" option is on, the app checks text you copy
to see whether it is a download link, and fills it in for you. This happens only
on your computer, and copied text is never stored or sent anywhere unless it is a
link the app then downloads. You can turn the option off at any time.

## Websites you download from

When you download a file or a video, the app connects directly to the website or
server that hosts it. That website receives the information every download
request includes, such as your IP address and the address of the file, and its
own privacy policy applies. Macra Systems is not involved in that connection.

## Torrents

BitTorrent works by connecting directly to other people sharing the same file.
When you download a torrent or magnet link:

- your IP address is visible to the other peers and to the trackers listed in
  the torrent, as with any BitTorrent program;
- the app looks for peers using the distributed hash table (DHT) and on your
  local network (Local Service Discovery);
- the app asks your router to open a port for incoming connections (UPnP and
  NAT-PMP) so downloads run faster.

If you do not want this, do not use the torrent feature.

## Downloading the app

The installer and app files are hosted on GitHub. When you download them, GitHub
processes your request under the
[GitHub Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).
The macrasystems.com website has its own privacy policy for visits to that site.

## Children

The app does not collect personal data from anyone, including children.

## Changes to this policy

If the app ever starts handling data differently, this policy will be updated
before that version is released, and the effective date above will change. Past
versions are kept in the project's public history on GitHub.

## Contact

Questions about this policy can be raised as an issue at
https://github.com/macrasystems-star/macra-downloader/issues
