import { useMemo, useState } from 'react'
import { useStore } from '../store'

/**
 * §31 Feed 2 — available resources.
 *
 * The incident queue answers "where are the problems". This answers "what have
 * I got left", and the two together are the whole operating question. Without
 * it an operator has to infer remaining capacity from unlabelled dots on a map.
 *
 * Grouped by type rather than listed flat, because the real question is never
 * "which unit" — it is "do I still have a boat".
 */

const TYPE_LABEL = {
  rescue_team: 'Rescue teams',
  boat: 'Boats',
  ambulance: 'Ambulances',
  medical_team: 'Medical teams',
  supply_truck: 'Supply trucks',
  heavy_equipment: 'Heavy equipment',
  volunteer_group: 'Volunteers',
}

const STATUS_TONE = {
  idle: 'free',
  returning: 'free',
  enroute: 'busy',
  onsite: 'busy',
  offline: 'offline',
}

// Free first: during a dispatch decision the committed units are context, not
// candidates.
const STATUS_ORDER = { idle: 0, returning: 1, enroute: 2, onsite: 3, offline: 4 }

export default function ResourcePanel() {
  const resources = useStore((s) => s.resources)
  const shelters = useStore((s) => s.shelters)
  const metrics = useStore((s) => s.metrics)
  const [tab, setTab] = useState('units')

  const grouped = useMemo(() => {
    const by = new Map()
    for (const r of resources) {
      if (!by.has(r.type)) by.set(r.type, [])
      by.get(r.type).push(r)
    }
    for (const list of by.values()) {
      list.sort(
        (a, b) =>
          (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) ||
          a.name.localeCompare(b.name),
      )
    }
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [resources])

  const openShelters = useMemo(
    () =>
      [...shelters]
        .filter((s) => s.status === 'open')
        .map((s) => ({ ...s, free: Math.max(0, s.capacity_total - (s.occupancy || 0)) }))
        .sort((a, b) => b.free - a.free),
    [shelters],
  )

  return (
    <aside className="resources">
      <div className="resources__head">
        <h2>Resources</h2>
        <div className="resources__tabs" role="group">
          <button
            className={tab === 'units' ? 'is-active' : ''}
            onClick={() => setTab('units')}
          >
            Units {metrics ? `(${metrics.units_free})` : ''}
          </button>
          <button
            className={tab === 'shelters' ? 'is-active' : ''}
            onClick={() => setTab('shelters')}
          >
            Shelters {metrics ? `(${metrics.shelters_open})` : ''}
          </button>
        </div>
      </div>

      {tab === 'units' && (
        <div className="resources__list">
          {grouped.map(([type, list]) => {
            const free = list.filter((r) => STATUS_TONE[r.status] === 'free').length
            return (
              <section key={type} className="rgroup">
                <header className="rgroup__head">
                  <span>{TYPE_LABEL[type] || type}</span>
                  {/* The number that actually matters, in the place the eye
                      lands: how many of this type can still be sent. */}
                  <span className={`rgroup__count${free === 0 ? ' is-none' : ''}`}>
                    {free}/{list.length}
                  </span>
                </header>
                <ul>
                  {list.map((r) => (
                    <li key={r.id} className={`runit runit--${STATUS_TONE[r.status]}`}>
                      <span className="runit__name">{r.name}</span>
                      <span className="runit__meta">
                        {/* Supply trucks are measured in stock, not seats — a
                            truck with an empty tank is not relief capacity. */}
                        {r.stock_water_l || r.stock_food_kg ? (
                          <span className="runit__cap">
                            {r.stock_water_l?.toLocaleString()}L ·{' '}
                            {r.stock_food_kg?.toLocaleString()}kg
                          </span>
                        ) : (
                          <span className="runit__cap">cap {r.capacity}</span>
                        )}
                        <span className="runit__status">{r.status}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )
          })}
          {resources.length === 0 && <p className="empty">No resources seeded.</p>}
        </div>
      )}

      {tab === 'shelters' && (
        <div className="resources__list">
          {metrics && (
            <div className="rgroup__summary">
              {metrics.shelter_capacity_available.toLocaleString()} beds free ·{' '}
              {metrics.shelters_open} open
            </div>
          )}
          <ul>
            {openShelters.map((s) => {
              const pct = s.capacity_total
                ? Math.round((100 * (s.occupancy || 0)) / s.capacity_total)
                : 100
              return (
                <li key={s.id} className="rshelter">
                  <div className="rshelter__name">{s.name}</div>
                  <div className="rshelter__bar">
                    <div
                      className={`rshelter__fill${pct >= 85 ? ' is-tight' : ''}`}
                      style={{ width: `${Math.min(100, pct)}%` }}
                    />
                  </div>
                  <div className="rshelter__meta">
                    {s.free} free / {s.capacity_total}
                    {s.has_medical && <span className="tagmed">medical</span>}
                  </div>
                </li>
              )
            })}
          </ul>
          {openShelters.length === 0 && <p className="empty">No open shelters.</p>}
        </div>
      )}
    </aside>
  )
}
