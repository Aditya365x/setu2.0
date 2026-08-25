import { useEffect, useState } from 'react'
import { useStore } from '../store'

function severityClass(score) {
  if (score >= 90) return 'sev sev--critical'
  if (score >= 75) return 'sev sev--high'
  if (score >= 60) return 'sev sev--elevated'
  if (score >= 40) return 'sev sev--moderate'
  return 'sev sev--low'
}

/** SLA countdown. Makes the picture feel operational rather than decorative. */
function useCountdown(deadline) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  if (!deadline) return null
  const remaining = new Date(deadline).getTime() - now
  const overdue = remaining < 0
  const abs = Math.abs(remaining)
  const mins = Math.floor(abs / 60000)
  const secs = Math.floor((abs % 60000) / 1000)
  return {
    overdue,
    label: `${overdue ? '+' : ''}${mins}:${String(secs).padStart(2, '0')}`,
  }
}

function IncidentRow({ incident, selected, onSelect, assigned }) {
  const sla = useCountdown(incident.sla_deadline)
  return (
    <button
      className={`incident${selected ? ' is-selected' : ''}`}
      onClick={() => onSelect(incident.id)}
    >
      <div className="incident__head">
        <span className={severityClass(incident.severity_score)}>
          {incident.severity_score.toFixed(1)}
        </span>
        <span className="incident__id">#{incident.id}</span>
        <span className="incident__hazard">{incident.hazard_type.replace(/_/g, ' ')}</span>
        {!assigned && incident.severity_score >= 70 && (
          <span className="badge badge--danger">unassigned</span>
        )}
      </div>
      {/* The citizen-facing reference codes. An incident is a CLUSTER of
          reports, so it carries several — and when somebody rings the control
          room quoting theirs, the operator has to be able to find this row by
          it. Anonymous rows make that impossible. */}
      {incident.reference_codes?.length > 0 && (
        <div className="incident__refs">
          {incident.reference_codes.slice(0, 3).map((code) => (
            <span key={code} className="refcode">{code}</span>
          ))}
          {incident.reference_codes.length > 3 && (
            <span className="incident__refmore">
              +{incident.reference_codes.length - 3}
            </span>
          )}
        </div>
      )}
      <div className="incident__meta">
        {incident.report_count} report{incident.report_count === 1 ? '' : 's'} ·{' '}
        {incident.people_affected_est} people
        {sla && (
          <span className={`sla${sla.overdue ? ' sla--breached' : ''}`}>
            SLA {sla.label}
          </span>
        )}
      </div>
    </button>
  )
}

export default function IncidentQueue() {
  const incidents = useStore((s) => s.incidents)
  const assignments = useStore((s) => s.assignments)
  const quarantine = useStore((s) => s.quarantine)
  const selectedIncidentId = useStore((s) => s.selectedIncidentId)
  const selectIncident = useStore((s) => s.selectIncident)
  const releaseQuarantined = useStore((s) => s.releaseQuarantined)
  const [showQuarantine, setShowQuarantine] = useState(false)

  const assignedIds = new Set(assignments.map((a) => a.incident_id))
  const active = incidents.filter(
    (i) => i.status !== 'resolved' && i.status !== 'false_alarm',
  )

  return (
    <aside className="queue">
      <div className="panel__title">
        Incident queue
        <span className="panel__hint">severity ↓</span>
      </div>

      <div className="queue__list">
        {active.length === 0 && (
          <p className="empty">
            No open incidents. Run <code>make demo</code> to start the Cyclone
            Landfall scenario.
          </p>
        )}
        {active.map((incident) => (
          <IncidentRow
            key={incident.id}
            incident={incident}
            selected={incident.id === selectedIncidentId}
            onSelect={selectIncident}
            assigned={assignedIds.has(incident.id)}
          />
        ))}
      </div>

      {/* Low-trust reports are visible and reviewable — never silently
          dropped, never auto-dispatched. */}
      <div className="quarantine">
        <button className="quarantine__toggle" onClick={() => setShowQuarantine((v) => !v)}>
          Quarantine ({quarantine.length})
          <span className="panel__hint">low trust · not dispatched</span>
        </button>
        {showQuarantine && (
          <div className="quarantine__list">
            {quarantine.length === 0 && <p className="empty">Nothing quarantined.</p>}
            {quarantine.map((report) => (
              <div key={report.id} className="quarantine__item">
                <div>
                  <strong>{report.hazard_type.replace(/_/g, ' ')}</strong> · trust{' '}
                  {report.trust_score.toFixed(2)} · {report.source}
                  <div className="quarantine__text">
                    {report.description || report.raw_text}
                  </div>
                </div>
                <button onClick={() => releaseQuarantined(report.id)}>Release</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
