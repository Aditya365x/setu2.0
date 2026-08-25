/**
 * Where the backend lives.
 *
 * Locally the frontends sit behind nginx, which proxies `/api/` to the api
 * container — so a relative URL is correct and this resolves to "". That is the
 * default, and it means `docker compose up` behaves exactly as it always has.
 *
 * Deployed, the frontends are static files on a CDN and the backend is a
 * container somewhere else entirely. There is no nginx in front to proxy
 * anything, so the origin has to be baked in at build time via
 * `VITE_API_ORIGIN` (e.g. https://setu-api.up.railway.app).
 *
 * Two things this deliberately does NOT do:
 *
 *   - It does not use Vercel rewrites. Rewrites cannot take an environment
 *     variable, so the backend URL would have to be committed to vercel.json,
 *     and they do not reliably carry a WebSocket upgrade — which would leave
 *     the dashboard frozen after first paint with no error.
 *
 *   - It does not touch /basemap/. Those are static assets deployed alongside
 *     the frontend and read with HTTP range requests; they must stay
 *     same-origin or the ranges break.
 */

const RAW = (import.meta.env.VITE_API_ORIGIN || '').trim()

/** Normalised origin with no trailing slash. Empty means "same origin". */
export const API_ORIGIN = RAW.replace(/\/+$/, '')

/** Absolute or relative URL for an API path. Pass paths starting with "/". */
export function apiUrl(path) {
  return `${API_ORIGIN}${path}`
}

/**
 * WebSocket URL for a path.
 *
 * Derives the scheme from the API origin when one is configured, and from the
 * page otherwise. Getting this wrong is a specific and silent failure: a page
 * served over https that opens ws:// is blocked as mixed content, and the only
 * symptom is a dashboard that never updates.
 */
export function wsUrl(path) {
  if (API_ORIGIN) {
    return `${API_ORIGIN.replace(/^http/, 'ws')}${path}`
  }
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}${path}`
}
