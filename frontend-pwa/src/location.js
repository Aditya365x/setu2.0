/**
 * Getting a location out of a phone that does not want to give you one.
 *
 * The failure modes here are not edge cases — they are the normal case for this
 * product. The people most likely to be reporting are on a degraded network, in
 * bad weather, possibly indoors, possibly on a borrowed phone with permissions
 * they have never touched. A report with a 3 km uncertainty circle is worth
 * enormously more than no report at all, so nothing in this module is allowed
 * to be a dead end.
 */

export const GEO_STATE = {
  LOCATING: 'locating',
  LOCKED: 'locked',
  INSECURE: 'insecure',
  DENIED: 'denied',
  UNAVAILABLE: 'unavailable',
  TIMEOUT: 'timeout',
  UNSUPPORTED: 'unsupported',
}

/**
 * The single most common reason geolocation "just doesn't work": browsers only
 * expose it in a secure context. localhost is exempt, so it works on the dev
 * laptop and then fails silently the moment a phone hits http://192.168.x.x.
 *
 * Detect it up front and say so plainly, rather than letting the user think
 * they denied a permission they were never asked for.
 */
export function isInsecureContext() {
  if (window.isSecureContext) return false
  const host = window.location.hostname
  return !(host === 'localhost' || host === '127.0.0.1' || host === '[::1]')
}

export function watchLocation(onFix, onFail) {
  if (isInsecureContext()) {
    onFail(GEO_STATE.INSECURE)
    return () => {}
  }
  if (!navigator.geolocation) {
    onFail(GEO_STATE.UNSUPPORTED)
    return () => {}
  }

  const id = navigator.geolocation.watchPosition(
    (pos) =>
      onFix({
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
        source: 'gps',
      }),
    (err) => {
      if (err.code === err.PERMISSION_DENIED) onFail(GEO_STATE.DENIED)
      else if (err.code === err.TIMEOUT) onFail(GEO_STATE.TIMEOUT)
      else onFail(GEO_STATE.UNAVAILABLE)
    },
    // A coarse fix now beats a precise fix in ninety seconds. maximumAge lets
    // us accept a recent cached position instead of waiting on a cold start.
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
  )
  return () => navigator.geolocation.clearWatch(id)
}

/** Raised when the PIN code belongs to another district entirely. Carries the
 *  server's message so the UI can say which district this deployment covers,
 *  rather than showing a generic failure. */
export class OutOfDistrictError extends Error {
  constructor(detail) {
    super(detail?.message || 'That PIN code is not in this district.')
    this.name = 'OutOfDistrictError'
    this.detail = detail
  }
}

/** Manual fallback: a six-digit pincode, resolved server-side against the same
 *  table the SMS channel uses.
 *
 *  An unseeded pincode that still belongs to this district resolves to the
 *  district centroid with its true 25 km accuracy stated. A pincode from
 *  another district throws — placing that pin here would put it hundreds of
 *  kilometres from the person who typed it, which is worse than saying no. */
export async function geocodePincode(pincode) {
  const res = await fetch(`/api/v1/geocode/pincode/${encodeURIComponent(pincode)}`)
  if (!res.ok) {
    let detail = null
    try {
      detail = (await res.json())?.detail
    } catch {
      /* non-JSON error body; fall through to the generic message */
    }
    if (res.status === 404 && detail?.error === 'pincode_out_of_district') {
      throw new OutOfDistrictError(detail)
    }
    throw new Error('geocode failed')
  }
  const json = await res.json()
  return {
    lat: json.lat,
    lng: json.lng,
    accuracy: json.accuracy_m,
    name: json.name,
    source: json.source,
  }
}

/** §12 — nearest shelters to a point. Full shelters are returned too, so
 *  somebody can see the nearest is full and walk to the second instead of
 *  arriving and being turned away. */
export async function nearbyShelters(lat, lng, limit = 5) {
  const res = await fetch(
    `/api/v1/shelters/nearby?lat=${lat}&lng=${lng}&limit=${limit}`,
  )
  if (!res.ok) throw new Error('shelters failed')
  return res.json()
}

/** §13 — official alerts covering this exact point, not the whole district.
 *  Warning somebody about weather 60 km away teaches them to ignore the
 *  banner, which is the one thing a warning system cannot afford. */
export async function alertsForLocation(lat, lng) {
  const res = await fetch(`/api/v1/alerts/for-location?lat=${lat}&lng=${lng}`)
  if (!res.ok) throw new Error('alerts failed')
  return res.json()
}

/** §11 — status of one report by its reference code. */
export async function trackReport(reference) {
  const res = await fetch(`/api/v1/reports/${encodeURIComponent(reference)}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error('track failed')
  return res.json()
}

/** §21 — the reporter moved. Sends a corrected position for an existing
 *  report so a boat is not dispatched to where somebody used to be. */
export async function updateReportLocation(reference, lat, lng, accuracy) {
  const res = await fetch(
    `/api/v1/reports/${encodeURIComponent(reference)}/location`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lng, accuracy_m: accuracy }),
    },
  )
  if (!res.ok) throw new Error('location update failed')
  return res.json()
}
