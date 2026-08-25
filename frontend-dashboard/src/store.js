import { create } from "zustand";

const API = "/api/v1";

async function getJSON(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// Which district this dashboard is looking at. Persisted so a reload — or a
// demo laptop waking from sleep — comes back where the operator left it rather
// than snapping to Ganjam.
const stored = (() => {
  try {
    return Number(localStorage.getItem("setu.districtId")) || 1;
  } catch {
    return 1;
  }
})();

export const useStore = create((set, get) => ({
  districts: [],
  districtId: stored,
  incidents: [],
  resources: [],
  shelters: [],
  alerts: [],
  assignments: [],
  quarantine: [],
  metrics: null,
  // Fixed. The dashboard always shows SETU's optimized plan.
  //
  // This used to be a user-facing toggle, and it was removed because it asked
  // the wrong question: an operator mid-cyclone should not be choosing a
  // scheduling algorithm. The solver still computes BOTH plans every cycle and
  // persists them — that has not changed — but the nearest-first baseline is
  // now shown as a comparison on the metric strip rather than as a mode. The
  // evidence survives; the confusing control does not.
  strategy: "optimized",
  selectedIncidentId: null,
  incidentDetail: null,
  connection: "connecting",
  capStale: false,
  // §Accountability — the citizen's reference code is the only handle they have
  // on their own report. Until an operator can search by it, "I sent 8KPN, has
  // anyone come?" is unanswerable.
  lookup: null,
  lookupError: null,

  setConnection: (connection) => set({ connection }),

  // Full resync. Called on first paint and on every WebSocket reconnect,
  // because the laptop will sleep and a silently stale map is worse than none.
  resync: async () => {
    const { strategy, districtId } = get();
    // Every read carries the district. The endpoints have always accepted it —
    // the dashboard just never sent one, so it was pinned to the configured
    // default no matter what was seeded.
    const d = `district_id=${districtId}`;
    const [
      districts,
      incidents,
      resources,
      shelters,
      alerts,
      assignments,
      metrics,
      quarantine,
    ] = await Promise.all([
      getJSON("/districts"),
      getJSON(`/incidents?${d}`),
      getJSON(`/resources?${d}`),
      getJSON(`/shelters?${d}`),
      getJSON(`/alerts/active?${d}`),
      getJSON(`/assignments?strategy=${strategy}&${d}`),
      getJSON(`/metrics?${d}`),
      getJSON(`/quarantine?${d}`),
    ]);
    set({
      districts,
      incidents,
      resources,
      shelters,
      alerts,
      assignments,
      metrics,
      quarantine,
    });
  },

  /** Switch district. Clears the current picture first so the operator never
   *  sees Ganjam's incidents captioned with Balasore's name. */
  setDistrict: async (districtId) => {
    if (districtId === get().districtId) return;
    try {
      localStorage.setItem("setu.districtId", String(districtId));
    } catch {
      /* private window — the switch still works, it just will not persist */
    }
    set({
      districtId,
      incidents: [],
      resources: [],
      shelters: [],
      alerts: [],
      assignments: [],
      quarantine: [],
      metrics: null,
      selectedIncidentId: null,
      incidentDetail: null,
      lookup: null,
      lookupError: null,
    });
    await get().resync();
  },

  selectIncident: async (id) => {
    set({ selectedIncidentId: id, incidentDetail: null });
    if (id == null) return;
    set({ incidentDetail: await getJSON(`/incidents/${id}`) });
  },

  commitAssignment: async (assignmentId) => {
    await fetch(`${API}/assignments/${assignmentId}/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "deoc-operator" }),
    });
    await get().resync();
    const id = get().selectedIncidentId;
    if (id != null) await get().selectIncident(id);
  },

  resolveIncident: async (id) => {
    await fetch(`${API}/incidents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "resolved", actor: "deoc-operator" }),
    });
    await get().resync();
  },

  releaseQuarantined: async (reportId) => {
    await fetch(`${API}/quarantine/${reportId}/release`, { method: "POST" });
    await get().resync();
  },

  /** Resolve a citizen reference code, then select the incident it belongs to
   *  so the map and detail panel land on it. */
  lookupReference: async (code) => {
    const ref = (code || "").trim().toUpperCase();
    if (!ref) return;
    set({ lookup: null, lookupError: null });
    try {
      const res = await fetch(`${API}/lookup/${encodeURIComponent(ref)}`);
      if (res.status === 404) {
        set({ lookupError: `No report with reference ${ref}` });
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      set({ lookup: data });
      if (data.incident_id != null)
        await get().selectIncident(data.incident_id);
    } catch {
      set({ lookupError: "Lookup failed" });
    }
  },

  clearLookup: () => set({ lookup: null, lookupError: null }),

  // Server events arrive as §7.3 envelopes. Cheap ones patch state in place;
  // anything that changes the plan triggers a resync.
  applyEvent: async (evt) => {
    switch (evt.type) {
      case "reoptimized":
      case "assignment.committed":
      case "incident.updated":
      case "resource.moved":
        await get().resync();
        break;
      case "alert.new":
        set({
          alerts: await getJSON(
            `/alerts/active?district_id=${get().districtId}`,
          ),
        });
        break;
      case "report.created": {
        // Deliberately not a full resync — 200 reports in 90 seconds would mean
        // 200 refetches. But doing NOTHING was wrong too: a single report
        // submitted during a demo left the queue empty until the next solver
        // cycle, which reads as "my report vanished".
        //
        // Refresh just the incident list, and only for this district, and only
        // when the board is quiet enough for it to be affordable. During a
        // burst the 'reoptimized' event takes over.
        const { districtId, incidents } = get();
        if (evt.payload?.district_id && evt.payload.district_id !== districtId)
          break;
        if (incidents.length > 40) break;
        set({
          incidents: await getJSON(`/incidents?district_id=${districtId}`),
        });
        break;
      }
      default:
        breakb;
    }
  },
}));
