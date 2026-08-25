import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Protocol } from 'pmtiles'
import themeLayers from 'protomaps-themes-base'
import { useStore } from '../store'
import { apiUrl } from '../api'

// PMTiles serves a whole tile pyramid out of ONE file over HTTP range requests,
// so the basemap is a static asset next to the bundle — no tile server, no API
// key, no egress. Registered once, before any map is constructed.
maplibregl.addProtocol('pmtiles', new Protocol().tile)

const SEVERITY_BANDS = [
  [0, '#4b6584'],
  [40, '#f0a500'],
  [60, '#f97316'],
  [75, '#ef4444'],
  [90, '#b91c1c'],
]

const RESOURCE_COLOURS = {
  idle: '#7f8c9b',
  returning: '#7f8c9b',
  enroute: '#2f80ed',
  onsite: '#27ae60',
  offline: '#c0392b',
}

const EMPTY = { type: 'FeatureCollection', features: [] }

const fc = (features) => ({ type: 'FeatureCollection', features })
const point = (lng, lat, props) => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lng, lat] },
  properties: props,
})
const line = (from, to, props) => ({
  type: 'Feature',
  geometry: { type: 'LineString', coordinates: [from, to] },
  properties: props,
})

/**
 * A real basemap with no external dependency.
 *
 * The venue Wi-Fi will fail — assume it. A basemap fetched from the internet at
 * render time is the single most likely thing to leave a blank rectangle on
 * stage, so the district's tiles ship WITH the app: one 6.7 MB `.pmtiles`
 * archive (Protomaps, OpenStreetMap data) served by the same nginx that serves
 * this bundle, plus 1.2 MB of glyphs for Latin, Devanagari and Odia labels.
 *
 * Nothing here touches the network. Roads, rivers, coastline and place names
 * render with the Ethernet cable pulled out — which is the point, because
 * "where is that boat actually going" is unanswerable over a black rectangle.
 *
 * To regenerate for another district, see docs/BASEMAP.md.
 */
// MUST be absolute. The pmtiles protocol strips the `pmtiles://` prefix and
// hands the remainder to `new URL()` with no base, so a root-relative path
// throws `Invalid URL` *inside* the protocol handler — which aborts style
// loading, so `load` never fires and NONE of the operational layers below get
// added. The symptom is a totally black map, incidents included, which reads as
// "the map is broken" rather than "the basemap is missing". Resolved against
// the current origin so this works on localhost, the LAN IP and HTTPS alike.
const BASEMAP_URL = `pmtiles://${new URL('/basemap/east_coast.pmtiles', window.location.href).href}`
const GLYPHS_URL = '/basemap/fonts/{fontstack}/{range}.pbf'

// The theme's own background is a mid grey. Repaint it in the dashboard's
// ground colour so that (a) it matches the rest of the UI, and (b) a failed
// basemap degrades to precisely the dark ground this map had before there was
// one — the background layer is the only one of the 68 that takes no source,
// which is what makes that fallback automatic rather than something we handle.
const GROUND = '#0d1117'
const themedLayers = () =>
  themeLayers('protomaps', 'dark').map((layer) =>
    layer.type === 'background'
      ? { ...layer, paint: { ...layer.paint, 'background-color': GROUND } }
      : layer,
  )

const BASEMAP_STYLE = {
  version: 8,
  glyphs: GLYPHS_URL,
  sources: {
    protomaps: {
      type: 'vector',
      url: BASEMAP_URL,
      attribution: '© OpenStreetMap contributors · Protomaps',
    },
  },
  layers: themedLayers(),
}

/**
 * The operating picture with no basemap under it — the flat dark ground this
 * map had before one existed.
 */
const FLAT_STYLE = {
  version: 8,
  glyphs: GLYPHS_URL,
  sources: {},
  layers: [{ id: 'ground', type: 'background', paint: { 'background-color': GROUND } }],
}

/**
 * Decide which style to build the map with, BEFORE constructing it.
 *
 * Tempting to skip this and let the basemap fail at render time, but a broken
 * archive throws inside the pmtiles protocol handler, and that aborts style
 * loading — so `load` never fires and none of the incident layers are ever
 * added. A missing basemap would cost us the entire operating picture.
 *
 * One ranged request settles it. It is served from the same origin, is 16 kB,
 * and is `immutable`-cached, so it costs nothing on every run after the first.
 */
async function chooseStyle() {
  try {
    const res = await fetch('/basemap/east_coast.pmtiles', {
      headers: { Range: 'bytes=0-16383' },
    })
    // A 200 means the server ignored the range and would hand back the whole
    // archive; pmtiles needs real range support, so treat that as unusable.
    if (res.status !== 206) throw new Error(`expected 206, got ${res.status}`)
    return { style: BASEMAP_STYLE, basemap: true }
  } catch (err) {
    console.warn(
      'SETU: basemap unavailable — operating picture continues on flat ground.',
      err,
    )
    return { style: FLAT_STYLE, basemap: false }
  }
}

export default function MapView() {
  const container = useRef(null)
  const map = useRef(null)
  const ready = useRef(false)
  const basemapReady = useRef(false)

  const incidents = useStore((s) => s.incidents)
  const resources = useStore((s) => s.resources)
  const shelters = useStore((s) => s.shelters)
  const alerts = useStore((s) => s.alerts)
  const assignments = useStore((s) => s.assignments)
  const selectIncident = useStore((s) => s.selectIncident)

  // ── build once ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (map.current) return
    let cancelled = false

    // Probe first, then build. See chooseStyle(): constructing with a basemap
    // that turns out to be broken costs us every incident layer, not just the
    // map tiles.
    const start = async () => {
      const { style, basemap } = await chooseStyle()
      if (cancelled || map.current) return
      basemapReady.current = basemap

      map.current = new maplibregl.Map({
        container: container.current,
        style,
        center: [84.85, 19.55],
        zoom: 8.6,
        attributionControl: false,
      })
      map.current.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        'bottom-right',
      )
      // OSM requires attribution and it costs one line. Collapsed by default so
      // it does not eat the operating picture. Only meaningful when the OSM
      // basemap actually rendered.
      if (basemap) {
        map.current.addControl(
          new maplibregl.AttributionControl({ compact: true }),
          'bottom-left',
        )
      }

    map.current.on('load', async () => {
      const m = map.current
      const addSource = (id) => m.addSource(id, { type: 'geojson', data: EMPTY })
      ;['district', 'cap', 'incidents', 'resources', 'shelters', 'dispatch', 'evacuation']
        .forEach(addSource)

      // CAP alert polygon — dashed border, 10% fill, colour by severity.
      // A light tint, not a mask. This fill was opaque back when there was no
      // basemap and the district had to be distinguished from a black void;
      // over real tiles the same opacity would grey out the roads and rivers we
      // just paid 6.7 MB to ship. The boundary line carries the shape instead.
      m.addLayer({
        id: 'district-fill',
        type: 'fill',
        source: 'district',
        paint: { 'fill-color': '#2f80ed', 'fill-opacity': 0.06 },
      })
      m.addLayer({
        id: 'district-line',
        type: 'line',
        source: 'district',
        paint: { 'line-color': '#5b7fa8', 'line-width': 2, 'line-opacity': 0.9 },
      })
      m.addLayer({
        id: 'cap-fill',
        type: 'fill',
        source: 'cap',
        paint: {
          'fill-color': ['get', 'colour'],
          'fill-opacity': 0.1,
        },
      })
      m.addLayer({
        id: 'cap-line',
        type: 'line',
        source: 'cap',
        paint: {
          'line-color': ['get', 'colour'],
          'line-width': 2,
          'line-dasharray': [3, 2],
        },
      })

      // The severity heatmap. Weight is severity_score, NOT point count — a
      // count-weighted heatmap just maps phone density, which is the exact bias
      // this product exists to correct.
      m.addLayer({
        id: 'severity-heat',
        type: 'heatmap',
        source: 'incidents',
        maxzoom: 13,
        paint: {
          'heatmap-weight': ['interpolate', ['linear'], ['get', 'severity'], 0, 0, 100, 1],
          'heatmap-intensity': 1.1,
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 8, 24, 13, 60],
          'heatmap-opacity': 0.55,
          'heatmap-color': [
            'interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(0,0,0,0)',
            0.25, '#1d4ed8',
            0.5, '#f59e0b',
            0.75, '#f97316',
            1, '#ef4444',
          ],
        },
      })

      m.addLayer({
        id: 'evacuation-lines',
        type: 'line',
        source: 'evacuation',
        paint: {
          'line-color': '#14b8a6',
          'line-width': ['interpolate', ['linear'], ['get', 'people'], 1, 1, 200, 6],
          'line-opacity': 0.7,
        },
      })

      // Solid = committed, dashed = proposed. This is the visual that sells the
      // whole product, so it sits above everything except the pins themselves.
      m.addLayer({
        id: 'dispatch-lines',
        type: 'line',
        source: 'dispatch',
        paint: {
          'line-color': ['case', ['get', 'committed'], '#22d3ee', '#94a3b8'],
          'line-width': ['case', ['get', 'committed'], 2.6, 1.6],
          'line-dasharray': ['case', ['get', 'committed'], ['literal', [1]], ['literal', [2, 2]]],
          'line-opacity': 0.9,
        },
      })

      m.addLayer({
        id: 'shelter-pins',
        type: 'circle',
        source: 'shelters',
        paint: {
          'circle-radius': 5,
          'circle-color': ['get', 'colour'],
          'circle-stroke-width': 1.5,
          'circle-stroke-color': ['case', ['get', 'full'], '#ef4444', '#0f172a'],
        },
      })

      m.addLayer({
        id: 'resource-pins',
        type: 'circle',
        source: 'resources',
        paint: {
          'circle-radius': 4.5,
          'circle-color': ['get', 'colour'],
          'circle-stroke-width': 1,
          'circle-stroke-color': '#0f172a',
        },
      })

      // Radius scales with people affected; colour with the severity band.
      m.addLayer({
        id: 'incident-pins',
        type: 'circle',
        source: 'incidents',
        minzoom: 7,
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['get', 'people'], 0, 5, 10, 8, 40, 14, 120, 20,
          ],
          'circle-color': ['get', 'colour'],
          'circle-opacity': 0.85,
          'circle-stroke-width': ['case', ['get', 'unassigned'], 2.5, 1],
          'circle-stroke-color': ['case', ['get', 'unassigned'], '#fef08a', '#0f172a'],
        },
      })

      m.on('click', 'incident-pins', (e) => {
        const id = e.features?.[0]?.properties?.id
        if (id != null) selectIncident(Number(id))
      })
      m.on('mouseenter', 'incident-pins', () => (m.getCanvas().style.cursor = 'pointer'))
      m.on('mouseleave', 'incident-pins', () => (m.getCanvas().style.cursor = ''))

      // Frame the district once we know its actual shape.
      try {
        const res = await fetch(apiUrl(`/api/v1/district?district_id=${useStore.getState().districtId}`))
        if (res.ok) {
          const district = await res.json()
          m.getSource('district').setData({
            type: 'Feature',
            geometry: district.boundary_geojson,
            properties: {},
          })
          const coords = district.boundary_geojson.coordinates[0]
          const bounds = coords.reduce(
            (b, c) => b.extend(c),
            new maplibregl.LngLatBounds(coords[0], coords[0]),
          )
          m.fitBounds(bounds, { padding: 40, duration: 0 })
        }
      } catch {
        /* unseeded database — the map still renders, just unframed */
      }

      ready.current = true
    })
    }

    start()
    return () => {
      cancelled = true
    }
  }, [selectIncident])

  // ── reframe when the operator switches district ─────────────────────────
  // The boundary and the camera both belong to the district, so both have to
  // follow it. Without this the map keeps Ganjam's outline and viewport while
  // the panels fill with Visakhapatnam's incidents.
  const districtId = useStore((s) => s.districtId)
  useEffect(() => {
    if (!ready.current || !map.current) return
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(apiUrl(`/api/v1/district?district_id=${districtId}`))
        if (!res.ok || cancelled) return
        const district = await res.json()
        const m = map.current
        m.getSource('district')?.setData({
          type: 'Feature',
          geometry: district.boundary_geojson,
          properties: {},
        })
        const rings =
          district.boundary_geojson.type === 'MultiPolygon'
            ? district.boundary_geojson.coordinates.flat()
            : district.boundary_geojson.coordinates
        const coords = rings[0]
        const bounds = coords.reduce(
          (b, c) => b.extend(c),
          new maplibregl.LngLatBounds(coords[0], coords[0]),
        )
        m.fitBounds(bounds, { padding: 40, duration: 600 })
      } catch {
        /* unseeded or offline — the operational layers still render */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [districtId])

  // ── update via setData; never re-render the map component ────────────────
  useEffect(() => {
    if (!ready.current || !map.current) return
    const m = map.current

    const colourFor = (severity) =>
      SEVERITY_BANDS.reduce((acc, [min, colour]) => (severity >= min ? colour : acc), SEVERITY_BANDS[0][1])

    const assignedIncidentIds = new Set(assignments.map((a) => a.incident_id))

    m.getSource('incidents')?.setData(
      fc(
        incidents
          .filter((i) => i.status !== 'resolved' && i.status !== 'false_alarm')
          .map((i) =>
            point(i.lng, i.lat, {
              id: i.id,
              severity: i.severity_score,
              people: i.people_affected_est || 0,
              colour: colourFor(i.severity_score),
              unassigned: i.severity_score >= 70 && !assignedIncidentIds.has(i.id),
            }),
          ),
      ),
    )

    m.getSource('resources')?.setData(
      fc(
        resources.map((r) =>
          point(r.lng, r.lat, { id: r.id, colour: RESOURCE_COLOURS[r.status] || '#7f8c9b' }),
        ),
      ),
    )

    m.getSource('shelters')?.setData(
      fc(
        shelters.map((s) => {
          const ratio = s.capacity_total ? s.occupancy / s.capacity_total : 0
          return point(s.lng, s.lat, {
            id: s.id,
            full: s.status !== 'open' || ratio >= 1,
            colour: ratio > 0.85 ? '#ef4444' : ratio > 0.5 ? '#f59e0b' : '#38bdf8',
          })
        }),
      ),
    )

    m.getSource('dispatch')?.setData(
      fc(
        assignments
          .filter((a) => a.from_lat != null && a.to_lat != null)
          .map((a) =>
            line([a.from_lng, a.from_lat], [a.to_lng, a.to_lat], {
              committed: a.status === 'committed',
              eta: Math.round((a.eta_seconds || 0) / 60),
            }),
          ),
      ),
    )
  }, [incidents, resources, shelters, assignments])

  useEffect(() => {
    if (!ready.current || !map.current) return
    const capColour = { Extreme: '#ef4444', Severe: '#f97316', Moderate: '#f59e0b', Minor: '#38bdf8' }
    map.current.getSource('cap')?.setData(
      fc(
        alerts
          .filter((a) => a.area_geojson)
          .map((a) => ({
            type: 'Feature',
            geometry: a.area_geojson,
            properties: { colour: capColour[a.severity] || '#38bdf8', severity: a.severity },
          })),
      ),
    )
  }, [alerts])

  return <div ref={container} className="map" />
}
