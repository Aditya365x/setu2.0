/**
 * The offline outbox.
 *
 * The single most important non-obvious requirement in the whole product: a
 * report must survive having no network at the moment of submission. That is
 * the entire point — the people who most need to be heard are the ones whose
 * tower just went down.
 *
 * So the ordering is non-negotiable: persist to IndexedDB FIRST, show the user
 * their report is safe, and only then attempt the network. Never the reverse.
 */

import { apiUrl } from './api'

const DB_NAME = 'setu'
const DB_VERSION = 1
const STORE = 'outbox'

/**
 * A client-side id that works on a plain-HTTP LAN address.
 *
 * `crypto.randomUUID()` is gated behind a secure context, exactly like
 * geolocation — so on http://192.168.x.x it is simply `undefined` and calling
 * it throws. `crypto.getRandomValues()` is NOT gated, so build the UUID from
 * that instead and keep the last-resort branch for ancient browsers.
 *
 * This id is the server's idempotency key, so it has to exist before anything
 * else happens: without it, replaying the outbox would duplicate reports.
 */
export function newId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID()

  if (globalThis.crypto?.getRandomValues) {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  return `r-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}


function open() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'client_report_uuid' })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function tx(mode, fn) {
  const db = await open()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode)
    const store = transaction.objectStore(STORE)
    const result = fn(store)
    transaction.oncomplete = () => resolve(result?.result ?? result)
    transaction.onerror = () => reject(transaction.error)
  })
}

export const put = (record) => tx('readwrite', (store) => store.put(record))
export const all = () => tx('readonly', (store) => store.getAll())
export const remove = (id) => tx('readwrite', (store) => store.delete(id))

export async function pending() {
  const records = await all()
  return records.filter((r) => !r.synced)
}

function toFormData(record) {
  const form = new FormData()
  form.append('lat', record.lat)
  form.append('lng', record.lng)
  form.append('hazard_type', record.hazard_type)
  form.append('severity_raw', record.severity_raw)
  // The server keys on this, so replaying the outbox can never create a
  // duplicate report however many times a flaky connection retries.
  form.append('client_report_uuid', record.client_report_uuid)
  if (record.description) form.append('description', record.description)
  if (record.gps_accuracy_m) form.append('gps_accuracy_m', Math.round(record.gps_accuracy_m))
  if (record.people_reported) form.append('people_reported', record.people_reported)
  if (record.phone) form.append('phone', record.phone)
  // §6 — who is affected. Only sent when true; the server defaults them false,
  // so an old queued record replayed after an upgrade still posts cleanly.
  for (const flag of ['has_children', 'has_elderly', 'has_injured', 'has_disabled']) {
    if (record[flag]) form.append(flag, 'true')
  }
  if (record.photo) form.append('photo', record.photo, 'report.jpg')
  return form
}

// fetch() has no default timeout. On a congested tower a request can hang for
// minutes, and the user is standing in water watching a spinner. Give up early
// and let the outbox retry — the report is already saved either way.
const SEND_TIMEOUT_MS = 12000

export async function send(record) {
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), SEND_TIMEOUT_MS)

  let res
  try {
    res = await fetch(apiUrl('/api/v1/ingest/report'), {
      method: 'POST',
      body: toFormData(record),
      signal: abort.signal,
    })
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) throw new Error(`ingest failed: ${res.status}`)
  const json = await res.json()
  await put({ ...record, synced: true, reference_code: json.reference_code, photo: undefined })
  return json
}

/** Drain everything queued. Safe to call repeatedly — idempotency is enforced
 *  server-side, so a partial failure just retries. */
export async function flush() {
  const queued = await pending()
  const results = []
  for (const record of queued) {
    try {
      results.push(await send(record))
    } catch {
      break // still offline; leave the rest queued rather than burning battery
    }
  }
  return results
}

/**
 * Downscale before upload. A 12 MP photo will stall a submission on a 2G
 * connection, and the photo is evidence, not art — 1280px at q0.7 is plenty to
 * confirm water depth or a collapsed wall.
 */
export function compressImage(file, maxEdge = 1280, quality = 0.7) {
  return new Promise((resolve) => {
    const image = new Image()
    const url = URL.createObjectURL(file)
    image.onload = () => {
      const scale = Math.min(1, maxEdge / Math.max(image.width, image.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(image.width * scale)
      canvas.height = Math.round(image.height * scale)
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      canvas.toBlob((blob) => resolve(blob || file), 'image/jpeg', quality)
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(file)
    }
    image.src = url
  })
}
