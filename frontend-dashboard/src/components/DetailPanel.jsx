import { useStore } from '../store'

const TERM_LABELS = {
  reported: 'What people reported',
  corroboration: 'Independent corroboration',
  hazard: 'Hazard lethality',
  population: 'Exposed population',
  official: 'Official CAP overlap',
}

// Must match WEIGHTS in services/scoring.py.
const WEIGHTS = {
  reported: 0.35,
  corroboration: 0.2,
  hazard: 0.15,
  population: 0.15,
  official: 0.15,
}

/**
 * Why this incident scores what it scores.
 *
 * A black box that outputs 87 is useless to a Collector who has to justify the
 * decision afterwards, so the five weighted terms are rendered as contributed
 * points rather than hidden behind the total.
 */
function SeverityBreakdown({ breakdown }) {
  if (!breakdown) return null
  return (
    <div className="breakdown">
      {Object.entries(TERM_LABELS).map(([key, label]) => {
        const raw = breakdown[key] ?? 0
        const contribution = raw * (WEIGHTS[key] ?? 0) * 100
        return (
          <div className="breakdown__row" key={key}>
            <span className="breakdown__label">{label}</span>
            <span className="breakdown__bar">
              <span style={{ width: `${Math.min(100, raw * 100)}%` }} />
            </span>
            <span className="breakdown__value">+{contribution.toFixed(1)}</span>
          </div>
        )
      })}
      {breakdown.escalations?.length > 0 && (
        <div className="escalations">
          {breakdown.escalations.map((tag) => (
            <span className="badge badge--warn" key={tag}>
              {tag.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function DetailPanel() {
  const detail = useStore((s) => s.incidentDetail)
  const selectedId = useStore((s) => s.selectedIncidentId)
  const commitAssignment = useStore((s) => s.commitAssignment)
  const resolveIncident = useStore((s) => s.resolveIncident)
  const selectIncident = useStore((s) => s.selectIncident)

  if (selectedId == null) {
    return (
      <aside className="detail">
        <div className="panel__title">Incident detail</div>
        <p className="empty">Select an incident on the map or in the queue.</p>
      </aside>
    )
  }

  if (!detail) {
    return (
      <aside className="detail">
        <div className="panel__title">Incident detail</div>
        <p className="empty">Loading…</p>
      </aside>
    )
  }

  const assignment = detail.assignment
  const photos = detail.reports.filter((r) => r.photo_url)

  return (
    <aside className="detail">
      <div className="panel__title">
        Incident #{detail.id}
        <button className="linkish" onClick={() => selectIncident(null)}>
          close
        </button>
      </div>

      <div className="detail__head">
        <div className="detail__score">{detail.severity_score.toFixed(1)}</div>
        <div>
          <div className="detail__hazard">{detail.hazard_type.replace(/_/g, ' ')}</div>
          <div className="detail__meta">
            {detail.report_count} reports · {detail.people_affected_est} people ·{' '}
            {detail.status}
          </div>
          {/* §6 — who is affected. This changes what a team brings, not just
              how fast they drive, so it sits with the headline facts rather
              than buried in the report list below. */}
          <div className="vulntags">
            {detail.has_children && <span className="vulntag">children</span>}
            {detail.has_elderly && <span className="vulntag">elderly</span>}
            {detail.has_injured && <span className="vulntag vulntag--urgent">injured</span>}
            {detail.has_disabled && <span className="vulntag">disability</span>}
          </div>
        </div>
      </div>

      <div className="panel__subtitle">Severity — why</div>
      <SeverityBreakdown breakdown={detail.severity_breakdown} />

      <div className="panel__subtitle">Recommended dispatch</div>
      {assignment ? (
        <div className="assignment">
          <div className="assignment__name">{assignment.resource_name}</div>
          <div className="assignment__meta">
            ETA {Math.round(assignment.eta_seconds / 60)} min · {assignment.status}
          </div>
          {assignment.status !== 'committed' ? (
            <button className="primary" onClick={() => commitAssignment(assignment.id)}>
              Commit dispatch
            </button>
          ) : (
            <button onClick={() => resolveIncident(detail.id)}>Mark resolved</button>
          )}
        </div>
      ) : (
        <p className="empty">
          No unit currently satisfies this incident's capability and capacity
          constraints.
        </p>
      )}

      {/* §"relief supplies" — the second allocation problem. Rescue is solved by
          Hungarian assignment above; this is min-cost flow over truck stock.
          The outstanding need is shown next to the plan so an operator can see
          whether the plan actually closes the gap. */}
      {detail.supply_need &&
        (detail.supply_need.water_l > 0 ||
          detail.supply_need.food_kg > 0 ||
          detail.supply_plan?.length > 0) && (
          <>
            <div className="panel__subtitle">Relief supplies</div>
            <div className="supneed">
              <span>
                Water <strong>{detail.supply_need.water_l.toLocaleString()} L</strong> needed
              </span>
              <span className="muted">
                {detail.supply_need.water_delivered_l.toLocaleString()} L delivered
              </span>
            </div>
            <div className="supneed">
              <span>
                Food <strong>{detail.supply_need.food_kg.toLocaleString()} kg</strong> needed
              </span>
              <span className="muted">
                {detail.supply_need.food_delivered_kg.toLocaleString()} kg delivered
              </span>
            </div>
            <ul className="evac">
              {detail.supply_plan?.map((leg) => (
                <li key={leg.id} className="evac__leg">
                  <div className="evac__line">
                    <strong>
                      {leg.water_l ? `${leg.water_l} L water` : `${leg.food_kg} kg food`}
                    </strong>{' '}
                    ← {leg.resource_name}{' '}
                    <span className="muted">({Math.round(leg.eta_seconds / 60)} min)</span>
                  </div>
                  <div className="evac__meta">
                    <span className="muted">
                      on truck: {leg.stock_water_l?.toLocaleString()} L ·{' '}
                      {leg.stock_food_kg?.toLocaleString()} kg
                    </span>
                    {leg.status === 'committed' ? (
                      <span className="evac__done">delivered ✓</span>
                    ) : (
                      <button
                        className="primary primary--sm"
                        onClick={() => commitAssignment(leg.id)}
                      >
                        Dispatch
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}

      {detail.evacuation_plan?.length > 0 && (
        <>
          <div className="panel__subtitle">Evacuation plan</div>
          {/* Committing an evacuation is what actually moves people into a
              shelter and draws down its beds. Without this control the
              min-cost flow produced a plan nobody could action, so shelter
              occupancy sat at zero no matter what the operator did. */}
          <ul className="evac">
            {detail.evacuation_plan.map((leg) => (
              <li key={leg.id ?? leg.shelter_id} className="evac__leg">
                <div className="evac__line">
                  <strong>{leg.people}</strong> → {leg.shelter_name}{' '}
                  <span className="muted">({Math.round(leg.eta_seconds / 60)} min)</span>
                </div>
                <div className="evac__meta">
                  <span className="muted">
                    {leg.beds_free} of {leg.capacity_total} beds free
                  </span>
                  {leg.status === 'committed' ? (
                    <span className="evac__done">placed ✓</span>
                  ) : (
                    <button
                      className="primary primary--sm"
                      onClick={() => commitAssignment(leg.id)}
                    >
                      Place {leg.people}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {photos.length > 0 && (
        <>
          <div className="panel__subtitle">Citizen photos ({photos.length})</div>
          <div className="photos">
            {photos.slice(0, 6).map((r) => (
              <img key={r.id} src={r.photo_url} alt="citizen report" />
            ))}
          </div>
        </>
      )}

      <div className="panel__subtitle">Reports</div>
      <ul className="reports">
        {detail.reports.map((r) => (
          <li key={r.id}>
            <span className="reports__source">{r.source}</span>
            <span className="reports__text">{r.description || '—'}</span>
            <span className="muted">
              trust {r.trust_score.toFixed(2)}
              {r.gps_accuracy_m >= 1000 && ` · ±${(r.gps_accuracy_m / 1000).toFixed(1)}km`}
              {/* The code the citizen holds. Printed on every constituent
                  report so an operator can match a phone call to a pin. */}
              {r.reference_code && (
                <span className="refcode">{r.reference_code}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </aside>
  )
}
