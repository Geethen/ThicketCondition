// Offline shell for the inspector.
//
// The app document must be NETWORK-FIRST. A cache-first document is
// indistinguishable from a broken deploy: whoever opened a URL first keeps that
// build forever, so coordinators and labellers silently run different versions
// of the app. Cache-first is kept only for the pinned cross-origin maplibre
// assets, whose URLs already carry their version.
//
// build.py rewrites the version below with the dataset id on every build, so a
// rebuilt draw also drops every stale entry from the previous one.
const CACHE = 'thicket-inspector-shell-356df8aaebf03f91';
const SHELL = ['./', './index.html', './manifest.webmanifest',
  'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css',
  'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js'];

// cache:'reload' because Pages serves index.html with max-age=600: a plain
// c.add() can install the very build this worker was shipped to replace.
self.addEventListener('install', e => e.waitUntil(
  caches.open(CACHE)
    .then(c => Promise.allSettled(SHELL.map(u => c.add(new Request(u, {cache: 'reload'})))))
    .then(() => self.skipWaiting())));

// Labellers work in the field. Never drop a working offline copy for an empty
// one: install() uses allSettled, so it still "succeeds" on a partial
// connection where index.html failed to cache. Only purge once the replacement
// is actually usable offline.
self.addEventListener('activate', e => e.waitUntil((async () => {
  const c = await caches.open(CACHE);
  if (await c.match('./index.html')) {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
  }
  await self.clients.claim();
})()));

// A bare network-first hangs on flaky rural connections until the browser's own
// (very long) timeout, where cache-first used to paint instantly. Fall back to
// cache quickly instead, so freshness never costs usability in the field.
const NET_TIMEOUT_MS = 4000;
const fromNetwork = req => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('network timeout')), NET_TIMEOUT_MS);
  fetch(req).then(r => { clearTimeout(timer); resolve(r); },
                  err => { clearTimeout(timer); reject(err); });
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const sameOrigin = new URL(req.url).origin === location.origin;

  // Everything we serve ourselves is either the app or generated data that is
  // rebuilt (index.html) or re-baked (gee_layers.json, whose Earth Engine tile
  // tokens expire weekly). All of it must revalidate; the cache is only an
  // offline fallback.
  if (sameOrigin || req.mode === 'navigate') {
    e.respondWith(
      fromNetwork(req)
        .then(r => {
          if (r && r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
          return r;
        })
        // Offline: this exact URL, then any cached copy of the shell regardless
        // of ?assignment=/?mode= — the query only picks which points to show.
        .catch(() => caches.match(req)
          .then(hit => hit || caches.match('./index.html', { ignoreSearch: true }))
          .then(hit => hit || caches.match('./', { ignoreSearch: true }))));
    return;
  }

  // Cross-origin: pinned library URLs and immutable basemap tiles. Cache-first
  // is safe here because the version is part of the URL.
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
});
