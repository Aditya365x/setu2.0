/* SETU service worker.
 *
 * Two jobs: keep the app loadable with no network, and flush the outbox the
 * moment a tower comes back. Nothing else — a service worker that tries to
 * cache API responses will happily serve a stale disaster.
 *
 * ── Why this is not a plain cache-first shell ──────────────────────────────
 *
 * It used to be, and that was a bug with teeth. Cache-first on `index.html`
 * means the cached copy is served forever, and that copy names a content-
 * hashed JS bundle which is also cached — so the app could never update. A
 * phone that opened SETU once kept that build permanently, even with a perfect
 * connection and a freshly deployed fix sitting on the server.
 *
 * The split below is the standard correct pattern, and each half is chosen for
 * a specific reason:
 *
 *   navigations  -> NETWORK FIRST. The HTML is the version pointer. Fetching
 *                   it fresh whenever a network exists is what lets a new
 *                   build take over at all. Falls back to cache when offline,
 *                   which is the case that actually matters here.
 *
 *   /assets/*    -> CACHE FIRST. Vite fingerprints these by content, so a
 *                   given URL is immutable. Cache-first is both safe and the
 *                   reason a cold, offline start is instant.
 *
 *   everything   -> NETWORK FIRST with cache fallback. Icons, manifest.
 *   else            Small, and staleness costs more than a round trip.
 */

// Bump on any change to this file's caching strategy. Routine app updates do
// NOT need a bump — network-first navigation handles those on its own, which
// is precisely the point of the split above.
const CACHE = 'setu-shell-v2'
const SHELL = ['/', '/index.html', '/manifest.webmanifest']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  // Take over immediately rather than waiting for every tab to close. A
  // citizen mid-emergency is not going to close their tabs to get a fix.
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  )
})

/** Immutable, content-hashed build output. Safe to serve from cache forever. */
function isHashedAsset(url) {
  return url.pathname.startsWith('/assets/')
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)

  // Never cache the API. A cached incident list is worse than none.
  if (url.pathname.startsWith('/api/')) return

  // The basemap is read with HTTP Range requests, and the Cache API rejects
  // 206 responses outright — every put() would throw and be swallowed, for no
  // benefit. Let the browser's own HTTP cache handle it and stay out of the way.
  if (url.pathname.startsWith('/basemap/') || request.headers.has('range')) return

  // Cross-origin (nothing today, but a CDN font tomorrow) — leave it alone.
  if (url.origin !== self.location.origin) return

  // ── navigations: network first ──────────────────────────────────────────
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE).then((c) => c.put('/index.html', copy)).catch(() => {})
          return response
        })
        // Offline: the whole reason this service worker exists.
        .catch(() => caches.match('/index.html').then((r) => r || caches.match('/'))),
    )
    return
  }

  // ── hashed assets: cache first ──────────────────────────────────────────
  if (isHashedAsset(url)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const copy = response.clone()
            caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {})
            return response
          }),
      ),
    )
    return
  }

  // ── everything else: network first, cache as backup ─────────────────────
  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone()
        caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {})
        return response
      })
      .catch(() => caches.match(request)),
  )
})

// Background Sync: the browser wakes us when connectivity returns, even if the
// user has closed the tab.
self.addEventListener('sync', (event) => {
  if (event.tag === 'flush-outbox') {
    event.waitUntil(
      self.clients.matchAll({ includeUncontrolled: true }).then((clients) => {
        clients.forEach((client) => client.postMessage({ type: 'flush-outbox' }))
      }),
    )
  }
})

// Lets the page force an update without the user clearing site data by hand.
self.addEventListener('message', (event) => {
  if (event.data?.type === 'skip-waiting') self.skipWaiting()
})
