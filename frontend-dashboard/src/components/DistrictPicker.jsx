import { useMemo } from 'react'
import { useStore } from '../store'

/**
 * District selector for the east-coast corridor.
 *
 * SETU is district-scoped by design — disaster coordination is a district-level
 * authority, and a Collector in Ganjam cannot dispatch a boat in Balasore. So
 * this is not a "show me everything" control: it switches which district's
 * operating picture you are working, one at a time.
 *
 * Grouped by state and captioned with live load, because the question an
 * operator actually has when switching is "where is it bad right now", not
 * "which districts exist". A district with critical incidents is marked so it
 * is visible without opening it.
 */
export default function DistrictPicker() {
  const districts = useStore((s) => s.districts)
  const districtId = useStore((s) => s.districtId)
  const setDistrict = useStore((s) => s.setDistrict)

  const grouped = useMemo(() => {
    const by = new Map()
    for (const d of districts) {
      if (!by.has(d.state)) by.set(d.state, [])
      by.get(d.state).push(d)
    }
    return [...by.entries()]
  }, [districts])

  const current = districts.find((d) => d.id === districtId)

  if (!districts.length) return null

  return (
    <label className="dpick">
      <span className="dpick__label">District</span>
      <select
        className="dpick__select"
        value={districtId}
        onChange={(e) => setDistrict(Number(e.target.value))}
        aria-label="Select district"
      >
        {grouped.map(([state, list]) => (
          <optgroup key={state} label={state}>
            {list.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {d.open_incidents > 0 ? ` — ${d.open_incidents} open` : ''}
                {d.critical > 0 ? ` (${d.critical} critical)` : ''}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {current && (
        <span className="dpick__state">{current.state}</span>
      )}
    </label>
  )
}
