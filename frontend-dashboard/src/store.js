import { create } from 'zustand'

const API = '/api/v1'

async function getJSON(path) {
  const res = await fetch(`${API}${path}`)
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return res.json()
}

export const useStore = create((set, get) => ({
  incidents: [],
  resources: [],
  shelters: [],
  alerts: [],
  assignments: [],
  quarantine: [],
  metrics: null,
  // The toggle. Flipping it re-reads a plan the solver already computed and
  // persisted — it does not re-solve, so the delta on screen is a real
  // comparison rather than a fresh roll of the dice.
  strategy: 'optimized',
  selectedIncidentId: null,
  incidentDetail: null,
  connection: 'connecting',
  capStale: false,
  // §Accountability — the citizen's reference code is the only handle they have
  // on their own report. Until an operator can search by it, "I sent 8KPN, has
  // anyone come?" is unanswerable.
  lookup: null,
  lookupError: null,

  setConnection: (connection) => set({ connection }),
  setStrategy: async (strategy) => {
    set({ strategy })
    set({ assignments: await getJSON(`/assignments?strategy=${strategy}`) })
  },

  // Full resync. Called on first paint and on every WebSocket reconnect,
  // because the laptop will sleep and a silently stale map is worse than none.
  resync: async () => {
    const strategy = get().strategy
    const [incidents, resources, shelters, alerts, assignments, metrics, quarantine] =
      await Promise.all([
        getJSON('/incidents'),
        getJSON('/resources'),
        getJSON('/shelters'),
        getJSON('/alerts/active'),
        getJSON(`/assignments?strategy=${strategy}`),
        getJSON('/metrics'),
        getJSON('/quarantine'),
      ])
    set({ incidents, resources, shelters, alerts, assignments, metrics, quarantine })
  },

  selectIncident: async (id) => {
    set({ selectedIncidentId: id, incidentDetail: null })
    if (id == null) return
    set({ incidentDetail: await getJSON(`/incidents/${id}`) })
  },

  commitAssignment: async (assignmentId) => {
    await fetch(`${API}/assignments/${assignmentId}/commit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: 'deoc-operator' }),
    })
    await get().resync()
    const id = get().selectedIncidentId
    if (id != null) await get().selectIncident(id)
  },

  resolveIncident: async (id) => {
    await fetch(`${API}/incidents/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'resolved', actor: 'deoc-operator' }),
    })
    await get().resync()
  },

  releaseQuarantined: async (reportId) => {
    await fetch(`${API}/quarantine/${reportId}/release`, { method: 'POST' })
    await get().resync()
  },

  /** Resolve a citizen reference code, then select the incident it belongs to
   *  so the map and detail panel land on it. */
  lookupReference: async (code) => {
    const ref = (code || '').trim().toUpperCase()
    if (!ref) return
    set({ lookup: null, lookupError: null })
    try {
      const res = await fetch(`${API}/lookup/${encodeURIComponent(ref)}`)
      if (res.status === 404) {
        set({ lookupError: `No report with reference ${ref}` })
        return
      }
      if (!res.ok) throw new Error(String(res.status))
      const data = await res.json()
      set({ lookup: data })
      if (data.incident_id != null) await get().selectIncident(data.incident_id)
    } catch {
      set({ lookupError: 'Lookup failed' })
    }
  },

  clearLookup: () => set({ lookup: null, lookupError: null }),

  // Server events arrive as §7.3 envelopes. Cheap ones patch state in place;
  // anything that changes the plan triggers a resync.
  applyEvent: async (evt) => {
    switch (evt.type) {
      case 'reoptimized':
      case 'assignment.committed':
      case 'incident.updated':
      case 'resource.moved':
        await get().resync()
        break
      case 'alert.new':
        set({ alerts: await getJSON('/alerts/active') })
        break
      case 'report.created':
        // Deliberately not a resync: 200 reports in 90 seconds would mean 200
        // full refetches. The optimizer's 'reoptimized' event covers it.
        break
      default:
        break
    }
  },
}))
