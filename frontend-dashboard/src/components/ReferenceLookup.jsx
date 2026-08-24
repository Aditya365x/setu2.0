import { useState } from 'react'
import { useStore } from '../store'

/**
 * Search by the citizen's reference code.
 *
 * This is the accountability loop, and it is the one thing the operating
 * picture was missing. A citizen is handed a four-character code at submission
 * — read back to them twice on an IVR call, SMS'd on the SMS path, shown on
 * the confirmation screen in the app. It is the only handle they have on their
 * own report.
 *
 * Until this existed the code lived on their phone and nowhere an operator
 * could search, so somebody ringing the control room to ask "I sent 8KPN, has
 * anyone come?" could not be answered. Now it resolves in one lookup to the
 * incident, the assigned unit and the ETA — answerable by a person, out loud,
 * over a radio.
 */
export default function ReferenceLookup() {
  const [code, setCode] = useState('')
  const lookup = useStore((s) => s.lookup)
  const lookupError = useStore((s) => s.lookupError)
  const lookupReference = useStore((s) => s.lookupReference)
  const clearLookup = useStore((s) => s.clearLookup)

  const submit = (e) => {
    e.preventDefault()
    lookupReference(code)
  }

  const a = lookup?.assignment

  return (
    <div className="reflookup">
      <form className="reflookup__form" onSubmit={submit}>
        <input
          className="reflookup__input"
          value={code}
          maxLength={6}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="REF"
          aria-label="Find a report by its citizen reference code"
        />
        <button type="submit" disabled={!code.trim()}>
          Find
        </button>
        {(lookup || lookupError) && (
          <button
            type="button"
            className="reflookup__clear"
            onClick={() => {
              setCode('')
              clearLookup()
            }}
          >
            ✕
          </button>
        )}
      </form>

      {lookupError && <div className="reflookup__err">{lookupError}</div>}

      {lookup && (
        <div className="reflookup__card">
          <div className="reflookup__row">
            <span className="reflookup__ref">{lookup.reference_code}</span>
            <span className="reflookup__hazard">
              {lookup.hazard_type.replace(/_/g, ' ')}
            </span>
            <span className="reflookup__src">{lookup.source}</span>
          </div>

          <div className="reflookup__row reflookup__row--meta">
            {lookup.incident_id ? (
              <>incident #{lookup.incident_id} · sev {lookup.severity_score?.toFixed(1)}</>
            ) : (
              <>not yet clustered</>
            )}
            {' · '}
            {lookup.people_reported ?? 0} people
          </div>

          {/* The answer to the only question the caller is actually asking. */}
          <div className={`reflookup__answer${a ? ' is-assigned' : ''}`}>
            {a ? (
              <>
                {a.status === 'committed' ? 'DISPATCHED' : 'PROPOSED'} ·{' '}
                {a.resource_name} · ETA {Math.round(a.eta_seconds / 60)} min
              </>
            ) : (
              'No unit assigned yet'
            )}
          </div>

          {/* A report outside the district is accepted and queued but no unit
              can reach it — the spatial pre-filter excludes it from the solver.
              That must be visible, not inferred from an empty dispatch. */}
          {lookup.in_district === false && (
            <div className="reflookup__warn">
              Outside district boundary — no unit can be dispatched
            </div>
          )}
        </div>
      )}
    </div>
  )
}
