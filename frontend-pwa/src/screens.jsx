/**
 * The secondary citizen screens (§11, §12, §13, §35).
 *
 * Kept out of App.jsx deliberately: the report form is the thing that must
 * never break, and it should not share a file with four screens that are only
 * reached from the home menu.
 *
 * All four assume they may be opened with no network. Each one says what it
 * does not know rather than spinning — a citizen staring at a spinner during a
 * cyclone has no way to tell "loading" from "broken".
 */

import { useEffect, useState } from 'react'
import { alertsForLocation, nearbyShelters, trackReport } from './location'

export function BackBar({ title, onBack, t }) {
  return (
    <header className="subhead">
      <button className="subhead__back" onClick={onBack}>
        ‹ {t.back}
      </button>
      <h1 className="subhead__title">{title}</h1>
    </header>
  )
}

/* ── §11 — track a report ───────────────────────────────────────────────── */

export function TrackScreen({ t, onBack, initialRef = '', recent = [] }) {
  const [ref, setRef] = useState(initialRef)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const check = async (code) => {
    const value = (code ?? ref).trim().toUpperCase()
    if (!value) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const data = await trackReport(value)
      if (!data) setError(t.trackNotFound)
      else setResult(data)
    } catch {
      setError(t.trackNotFound)
    }
    setBusy(false)
  }

  useEffect(() => {
    if (initialRef) check(initialRef)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialRef])

  return (
    <div className="screen">
      <BackBar title={t.trackTitle} onBack={onBack} t={t} />

      <section>
        <div className="fallback__row">
          <input
            className="field"
            type="text"
            maxLength={6}
            value={ref}
            onChange={(e) => setRef(e.target.value.toUpperCase())}
            placeholder={t.trackPlaceholder}
          />
          <button onClick={() => check()} disabled={busy || !ref.trim()}>
            {busy ? '…' : t.trackButton}
          </button>
        </div>
        {error && <p className="fallback__error">{error}</p>}
      </section>

      {result && (
        <section>
          {result.under_review && (
            <div className="banner">{t.trackUnderReview}</div>
          )}
          {result.eta_minutes != null && (
            <div className="banner banner--good">
              {t.trackEta(result.eta_minutes)}
            </div>
          )}
          <ol className="timeline">
            {result.timeline.map((step) => (
              <li
                key={step.key}
                className={
                  'timeline__step' +
                  (step.done ? ' is-done' : '') +
                  (step.current ? ' is-current' : '')
                }
              >
                <span className="timeline__mark">{step.done ? '✓' : '○'}</span>
                <span className="timeline__label">{step.label}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* Reports made from this phone, read straight out of the outbox — so
          this list works with no network at all. */}
      {recent.length > 0 && (
        <section>
          <h2>{t.myReports}</h2>
          <ul className="list">
            {recent.map((r) => (
              <li key={r.client_report_uuid}>
                <button
                  className="list__row"
                  onClick={() => {
                    setRef(r.reference_code || '')
                    if (r.reference_code) check(r.reference_code)
                  }}
                >
                  <span className="list__main">
                    {r.hazard_type} · {new Date(r.client_ts).toLocaleString()}
                  </span>
                  <span className="list__meta">
                    {r.reference_code || '⏳'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {recent.length === 0 && !result && <p className="muted">{t.noReports}</p>}
    </div>
  )
}

/* ── §12 — nearby shelters ──────────────────────────────────────────────── */

const SHELTER_LABEL = {
  open: (t) => t.shelterOpen,
  almost_full: (t) => t.shelterAlmostFull,
  full: (t) => t.shelterFull,
}

export function SheltersScreen({ t, onBack, position }) {
  const [shelters, setShelters] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!position) return
    nearbyShelters(position.lat, position.lng, 6)
      .then(setShelters)
      .catch(() => setFailed(true))
  }, [position])

  return (
    <div className="screen">
      <BackBar title={t.sheltersTitle} onBack={onBack} t={t} />
      {!position && <p className="muted">{t.needLocationFirst}</p>}
      {failed && <p className="fallback__error">{t.noAlerts}</p>}
      {shelters && (
        <ul className="list">
          {shelters.map((s) => (
            <li key={s.id} className={`shelter shelter--${s.occupancy_label}`}>
              <div className="shelter__name">{s.name}</div>
              <div className="shelter__meta">
                <span>{s.distance_km} km</span>
                <span className="shelter__state">
                  {SHELTER_LABEL[s.occupancy_label](t)}
                </span>
              </div>
              <div className="shelter__beds">
                {t.shelterBeds(s.available)} · {s.available}/{s.capacity_total}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/* ── §13 — alerts for this location ─────────────────────────────────────── */

export function AlertsScreen({ t, onBack, position }) {
  const [alerts, setAlerts] = useState(null)

  useEffect(() => {
    if (!position) return
    alertsForLocation(position.lat, position.lng)
      .then(setAlerts)
      .catch(() => setAlerts([]))
  }, [position])

  return (
    <div className="screen">
      <BackBar title={t.alertsTitle} onBack={onBack} t={t} />
      {!position && <p className="muted">{t.needLocationFirst}</p>}
      {alerts && alerts.length === 0 && <p className="muted">{t.noAlerts}</p>}
      {alerts &&
        alerts.map((a) => (
          <div
            key={a.cap_identifier}
            className={`alertcard alertcard--${(a.severity || 'minor').toLowerCase()}`}
          >
            <div className="alertcard__head">
              {a.event} · {a.severity}
            </div>
            {a.headline && <p className="alertcard__line">{a.headline}</p>}
            {/* The instruction is the only part that tells somebody what to
                DO, so it is never truncated or hidden behind a tap. */}
            {a.instruction && (
              <p className="alertcard__do">{a.instruction}</p>
            )}
            <div className="alertcard__meta">
              {a.source_agency} · until{' '}
              {a.expires_at ? new Date(a.expires_at).toLocaleString() : '—'}
            </div>
          </div>
        ))}
    </div>
  )
}

/* ── §35 — emergency contacts ───────────────────────────────────────────── */

// Official national and Odisha state numbers. Hardcoded on purpose: this
// screen has to work with no network, no database and no seed.
const CONTACTS = [
  ['112', 'All emergencies'],
  ['1077', 'District control room'],
  ['1070', 'State control room'],
  ['108', 'Ambulance'],
  ['101', 'Fire'],
  ['100', 'Police'],
  ['1078', 'NDMA helpline'],
]

export function ContactsScreen({ t, onBack }) {
  return (
    <div className="screen">
      <BackBar title={t.contactsTitle} onBack={onBack} t={t} />
      <p className="muted">{t.contactsNote}</p>
      <ul className="list">
        {CONTACTS.map(([number, label]) => (
          <li key={number}>
            <a className="list__row list__row--call" href={`tel:${number}`}>
              <span className="list__main">{label}</span>
              <span className="list__meta list__meta--num">{number}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}
