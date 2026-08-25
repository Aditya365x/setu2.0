/**
 * Where the backend and the basemap live.
 *
 * Locally nginx proxies both: `/api/` to the api container and `/basemap/` to
 * the dashboard container, so relative URLs are correct and these resolve to
 * "". `docker compose up` is unchanged.
 *
 * Deployed, this app is static files on a CDN with no nginx in front, so both
 * origins are baked in at build time:
 *
 *   VITE_API_ORIGIN      the backend      (https://setu-api.up.railway.app)
 *   VITE_BASEMAP_ORIGIN  the dashboard    (https://setu-deoc.vercel.app)
 *
 * The basemap is separate from the API on purpose. It is a 48 MB PMTiles
 * archive deployed alongside the dashboard; shipping a second copy in this
 * bundle would double the repo for a byte-identical file, and putting it in the
 * backend image would bloat every API deploy. Cross-origin range requests need
 * `Access-Control-Allow-Origin` on those assets — see the dashboard's
 * vercel.json, which sets it for /basemap/ only.
 */

const norm = (v) => (v || '').trim().replace(/\/+$/, '')

export const API_ORIGIN = norm(import.meta.env.VITE_API_ORIGIN)
export const BASEMAP_ORIGIN = norm(import.meta.env.VITE_BASEMAP_ORIGIN)

export const apiUrl = (path) => `${API_ORIGIN}${path}`
export const basemapUrl = (path) => `${BASEMAP_ORIGIN}${path}`
