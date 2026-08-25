/**
 * Route view: where you are, where the shelter is, and how to get there.
 *
 * Two layers of answer, because they fail at different times:
 *
 *   1. OFFLINE — the map itself. Roads, rivers and place names come from the
 *      same PMTiles archive the operator's dashboard uses, served from this
 *      origin, so it renders with no network at all. You get orientation:
 *      which way, how far, what is between you and it. During a cyclone that
 *      is frequently all anybody gets.
 *
 *   2. ONLINE — turn-by-turn, handed off to whatever maps app the phone
 *      already has. We do not attempt to route: real routing needs a road
 *      graph and live closures, and a made-up route in a flood is worse than
 *      no route.
 *
 * This whole module is loaded on demand. MapLibre is ~220 kB gzipped against a
 * 57 kB app, and someone reporting an emergency should never pay for it. The
 * cost is only incurred by a tap on the map icon.
 *
 * ── maplibre-gl is pinned to 4.7.1, deliberately ───────────────────────────
 *
 * `npm install maplibre-gl` pulled 6.6.0 here while the dashboard ran 4.7.1,
 * and the two are not interchangeable: `addProtocol` changed signature after
 * v4, so `new Protocol().tile` threw during setup. Every module loaded, the
 * network log showed no request for the archive at all, and the only visible
 * symptom was "Offline map unavailable" — which reads like a missing file
 * rather than an API mismatch.
 *
 * `protomaps-themes-base@4` also targets the v4 style spec. Upgrading MapLibre
 * means upgrading the theme AND regenerating the archive against a v5 basemap
 * build. Change all three together or none.
 */

import { useEffect, useRef, useState } from 'react'
import 'maplibre-gl/dist/maplibre-gl.css'
import { basemapUrl } from './api'

const BASEMAP_URL = `pmtiles://${new URL(basemapUrl('/basemap/east_coast.pmtiles'), window.location.href).href}`
const GLYPHS_URL = basemapUrl('/basemap/fonts/{fontstack}/{range}.pbf')
const GROUND = '#0d1117'

/** Great-circle distance and initial bearing — enough to orient someone. */
function bearingDeg(from, to) {
  const toRad = (d) => (d * Math.PI) / 180
  const dLng = toRad(to.lng - from.lng)
  const y = Math.sin(dLng) * Math.cos(toRad(to.lat))
  const x =
    Math.cos(toRad(from.lat)) * Math.sin(toRad(to.lat)) -
    Math.sin(toRad(from.lat)) * Math.cos(toRad(to.lat)) * Math.cos(dLng)
  return (Math.atan2(y, x) * 180) / Math.PI
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]

/**
 * Hand off to the phone's maps app for turn-by-turn.
 *
 * The Google Maps universal URL is used rather than a `geo:` URI because it
 * behaves on both platforms: it opens the installed app where there is one and
 * falls back to the web otherwise. `geo:` is Android-only and silently does
 * nothing on iOS, which is the worst possible outcome here.
 */
export function directionsUrl(origin, dest) {
  const d = `${dest.lat},${dest.lng}`
  const o = origin ? `${origin.lat},${origin.lng}` : ''
  return (
    'https://www.google.com/maps/dir/?api=1' +
    (o ? `&origin=${encodeURIComponent(o)}` : '') +
    `&destination=${encodeURIComponent(d)}&travelmode=walking`
  )
}

export default function ShelterMap({ shelter, origin, onClose, t }) {
  const container = useRef(null)
  const map = useRef(null)
  const [failed, setFailed] = useState(false)
  const [online, setOnline] = useState(navigator.onLine)

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let cleanup = () => {}

    ;(async () => {
      // Dynamic import: this is what keeps MapLibre out of the main bundle.
      const [{ default: maplibregl }, { Protocol }, { default: themeLayers }] =
        await Promise.all([
          import('maplibre-gl'),
          import('pmtiles'),
          import('protomaps-themes-base'),
        ])
      if (cancelled || !container.current) return

      maplibregl.addProtocol('pmtiles', new Protocol().tile)

      // Probe before building. A broken archive throws inside the pmtiles
      // protocol handler, which aborts style loading — and then the shelter and
      // "you are here" markers never get added either. Losing the basemap is
      // survivable; losing the two pins is not.
      let hasBasemap = true
      try {
        const res = await fetch(basemapUrl('/basemap/east_coast.pmtiles'), {
          headers: { Range: 'bytes=0-16383' },
        })
        if (res.status !== 206) throw new Error(String(res.status))
      } catch {
        hasBasemap = false
        setFailed(true)
      }
      if (cancelled) return

      const layers = hasBasemap
        ? themeLayers('protomaps', 'dark').map((l) =>
            l.type === 'background'
              ? { ...l, paint: { ...l.paint, 'background-color': GROUND } }
              : l,
          )
        : [{ id: 'ground', type: 'background', paint: { 'background-color': GROUND } }]

      const m = new maplibregl.Map({
        container: container.current,
        style: {
          version: 8,
          glyphs: GLYPHS_URL,
          sources: hasBasemap
            ? {
                protomaps: {
                  type: 'vector',
                  url: BASEMAP_URL,
                  attribution: '© OpenStreetMap contributors · Protomaps',
                },
              }
            : {},
          layers,
        },
        center: [shelter.lng, shelter.lat],
        zoom: 13,
        attributionControl: false,
      })
      map.current = m
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')

      m.on('load', () => {
        if (cancelled) return

        if (origin) {
          // A straight line, drawn dashed on purpose. It is a bearing, not a
          // route — showing it solid would imply a road that may not exist, or
          // may be under water.
          m.addSource('route', {
            type: 'geojson',
            data: {
              type: 'Feature',
              geometry: {
                type: 'LineString',
                coordinates: [
                  [origin.lng, origin.lat],
                  [shelter.lng, shelter.lat],
                ],
              },
            },
          })
          m.addLayer({
            id: 'route',
            type: 'line',
            source: 'route',
            paint: {
              'line-color': '#22d3ee',
              'line-width': 3,
              'line-dasharray': [2, 2],
              'line-opacity': 0.85,
            },
          })
          new maplibregl.Marker({ color: '#2f80ed' })
            .setLngLat([origin.lng, origin.lat])
            .setPopup(new maplibregl.Popup().setText(t.youAreHere))
            .addTo(m)
        }

        new maplibregl.Marker({ color: '#22c55e' })
          .setLngLat([shelter.lng, shelter.lat])
          .setPopup(new maplibregl.Popup().setText(shelter.name))
          .addTo(m)

        if (origin) {
          const b = new maplibregl.LngLatBounds(
            [origin.lng, origin.lat],
            [origin.lng, origin.lat],
          ).extend([shelter.lng, shelter.lat])
          m.fitBounds(b, { padding: 70, maxZoom: 15, duration: 0 })
        }
      })

      cleanup = () => m.remove()
    })().catch(() => setFailed(true))

    return () => {
      cancelled = true
      cleanup()
    }
  }, [shelter, origin, t])

  const heading = origin ? compass(bearingDeg(origin, shelter)) : null

  return (
    <div className="smap">
      <header className="subhead smap__head">
        <button className="subhead__back" onClick={onClose}>
          ‹ {t.back}
        </button>
        <h1 className="subhead__title">{shelter.name}</h1>
      </header>

      <div className="smap__facts">
        <span>
          <strong>{shelter.distance_km} km</strong>
          {heading ? ` ${heading}` : ''}
        </span>
        <span className="muted">
          {t.shelterBeds(shelter.available)}
        </span>
      </div>

      <div ref={container} className="smap__canvas" />

      {failed && <p className="fallback__error">{t.mapUnavailable}</p>}

      <div className="smap__actions">
        <a
          className="smap__go"
          href={directionsUrl(origin, shelter)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t.openInMaps}
        </a>
        {/* Turn-by-turn is somebody else's app and needs the network. Say that
            before the tap, not after it opens a blank page. */}
        {!online && <p className="muted smap__note">{t.directionsNeedNetwork}</p>}
      </div>
    </div>
  )
}
