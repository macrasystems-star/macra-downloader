// Macra Downloader browser extension (Chrome, Edge, Brave, Chromium, Firefox).
//
// When the browser starts a download, this hands it to Macra Downloader through
// the browser helper ("native messaging host" com.macrasystems.downloader), with
// the site's cookies, the referring page and the browser's user agent, so links
// that need a login keep working. The browser's own copy is cancelled.
// If Macra Downloader cannot take it, the browser downloads the file as usual.
//
// Not handed over: private/incognito windows, blob:/data: links (made by the
// page itself), and downloads this extension starts as a fallback.
// Click the toolbar button to turn the hand-over off or on.

const api = globalThis.browser ?? globalThis.chrome;
const IS_FIREFOX = typeof globalThis.browser !== 'undefined' && !!globalThis.browser.runtime.getBrowserInfo;
const HOST = 'com.macrasystems.downloader';
const MENU_ID = 'macra-download-link';
// Firefox does not say which extension started a download, so our own fallback
// downloads are remembered by URL for a minute.
const FALLBACK_MS = 60 * 1000;

const handling = new Set();          // download ids being handed over now

async function isEnabled() {
  const { enabled } = await api.storage.local.get({ enabled: true });
  return enabled;
}

async function showState(enabled, error) {
  await api.action.setBadgeText({ text: error ? '!' : (enabled ? '' : 'off') });
  await api.action.setBadgeBackgroundColor({ color: error ? '#c0392b' : '#777777' });
  await api.action.setTitle({
    title: error
      ? `Macra Downloader could not take the last download: ${error}\nThe browser downloaded it instead.`
      : enabled
        ? 'Macra Downloader is handling downloads (click to turn off)'
        : 'Downloads stay in the browser (click to send them to Macra Downloader)',
  });
}

async function rememberFallback(url) {
  const { fallbacks = {} } = await api.storage.session.get('fallbacks').catch(() => ({}));
  const now = Date.now();
  for (const [u, t] of Object.entries(fallbacks)) if (now - t > FALLBACK_MS) delete fallbacks[u];
  fallbacks[url] = now;
  await api.storage.session.set({ fallbacks }).catch(() => {});
}

async function isOwnFallback(item) {
  if (item.byExtensionId && item.byExtensionId === api.runtime.id) return true;
  const { fallbacks = {} } = await api.storage.session.get('fallbacks').catch(() => ({}));
  const t = fallbacks[item.finalUrl || item.url] || fallbacks[item.url];
  return !!t && Date.now() - t < FALLBACK_MS;
}

async function eligible(item) {
  return item.state === 'in_progress'
    && !item.incognito
    && /^https?:\/\//i.test(item.finalUrl || item.url)
    // Sometimes (downloads started by a page script) Chrome reports the page's own address
    // instead of the file's. Handing that over would fetch the page, so keep it in the browser.
    && !(item.referrer && (item.finalUrl || item.url) === item.referrer)
    && (await isEnabled())
    && !(await isOwnFallback(item));
}

async function cookiesFor(urls) {
  const found = new Map();
  for (const url of urls) {
    if (!url || !/^https?:\/\//i.test(url)) continue;
    let list = [];
    try {
      // firstPartyDomain: null = also when Firefox's first-party isolation is on
      list = await api.cookies.getAll(IS_FIREFOX ? { url, firstPartyDomain: null } : { url });
    } catch (e) {
      try { list = await api.cookies.getAll({ url }); } catch (e2) { list = []; }
    }
    for (const c of list) {
      found.set(`${c.domain}|${c.path}|${c.name}`,
                { name: c.name, value: c.value, domain: c.domain, path: c.path, secure: c.secure });
    }
  }
  return [...found.values()];
}

async function sendToDownloader(message) {
  try {
    const reply = await api.runtime.sendNativeMessage(HOST, message);
    return reply && reply.ok ? null : ((reply && reply.error) || 'no answer');
  } catch (e) {
    return String(e && e.message || e).includes('not found')
      ? 'Macra Downloader is not installed (or its browser helper is not set up)'
      : String(e && e.message || e);
  }
}

async function takeOver(item) {
  if (handling.has(item.id)) return;
  handling.add(item.id);
  try {
    if (!(await eligible(item))) return;
    try { await api.downloads.cancel(item.id); } catch (e) { return; }
    const [now] = await api.downloads.search({ id: item.id });
    if (now && now.state === 'complete') return;          // it finished first: keep the browser's copy
    api.downloads.erase({ id: item.id }).catch(() => {});
    // Chrome's `url` can be the page that started the download; `finalUrl` is the file itself.
    const url = item.finalUrl || item.url;
    const error = await sendToDownloader({
      type: 'download',
      url,
      filename: item.filename || '',
      referrer: item.referrer || '',
      user_agent: navigator.userAgent,
      cookies: await cookiesFor([item.url, item.finalUrl]),
    });
    if (error) {
      await rememberFallback(url);
      await api.downloads.download({ url }).catch(() => {});
    }
    await showState(true, error);
  } finally {
    handling.delete(item.id);
  }
}

// Chrome knows the file name a moment after the download starts (from the
// server's answer, or after "Save as"); hand over then, so the name is right.
// Firefox already has it here.
api.downloads.onCreated.addListener(async (item) => {
  if (item.filename) await takeOver(item);
});

api.downloads.onChanged.addListener(async (delta) => {
  if (!delta.filename || !delta.filename.current) return;
  const [item] = await api.downloads.search({ id: delta.id });
  if (item) await takeOver(item);
});

api.action.onClicked.addListener(async () => {
  const enabled = !(await isEnabled());
  await api.storage.local.set({ enabled });
  await showState(enabled, null);
});

function makeMenu() {
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({
      id: MENU_ID,
      title: 'Download with Macra Downloader',
      contexts: ['link', 'video', 'audio', 'image'],
    });
  });
}
api.runtime.onInstalled.addListener(makeMenu);
api.runtime.onStartup.addListener(async () => showState(await isEnabled(), null));

api.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  const url = info.linkUrl || info.srcUrl;
  if (!url || !/^(https?|ftp|magnet):/i.test(url)) return;
  const error = await sendToDownloader({
    type: 'download',
    url,
    referrer: info.pageUrl || '',
    user_agent: navigator.userAgent,
    cookies: tab && tab.incognito ? [] : await cookiesFor([url]),
  });
  await showState(await isEnabled(), error);
});
