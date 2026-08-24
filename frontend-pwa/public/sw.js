/* SETU service worker.
 *
 * Two jobs: keep the app loadable with no network, and flush the outbox the
 * moment a tower comes back. Nothing else — a service worker that tries to
 * cache API responses will happily serve a stale disaster.
 */

const CACHE = 'setu-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  // Never cache the API. A cached incident list is worse than none.
  if (request.method !== 'GET' || new URL(request.url).pathname.startsWith('/api/')) return

  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request)
          .then((response) => {
            const copy = response.clone()
            caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {})
            return response
          })
          .catch(() => caches.match('/index.html')),
    ),
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
