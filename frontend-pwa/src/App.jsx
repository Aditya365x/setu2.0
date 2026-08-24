import { useEffect, useMemo, useState } from 'react'
import * as outbox from './outbox'
import { GEO_STATE, OutOfDistrictError, geocodePincode, watchLocation } from './location'
import { STRINGS } from './i18n'
import {
  AlertsScreen,
  ContactsScreen,
  SheltersScreen,
  TrackScreen,
} from './screens'

// §6 — who is affected, not just how many. A rescue team sizes its response
// differently for a child or a stretcher case, and making the operator infer
// that from free text is how it gets missed.
const VULNERABLE = [
  ['has_children', 'vChildren'],
  ['has_elderly', 'vElderly'],
  ['has_injured', 'vInjured'],
  ['has_disabled', 'vDisabled'],
]

const HAZARDS = [
  { key: 'flood', icon: '🌊' },
  { key: 'stranded', icon: '🆘' },
  { key: 'building_collapse', icon: '🏚️' },
  { key: 'medical', icon: '🚑' },
  { key: 'fire', icon: '🔥' },
  { key: 'landslide', icon: '⛰️' },
  { key: 'power_line', icon: '⚡' },
  { key: 'other', icon: '❓' },
]

export default function App() {
  const [lang, setLang] = useState('en')
  const t = useMemo(() => STRINGS[lang], [lang])

  const [hazard, setHazard] = useState(null)
  const [severity, setSeverity] = useState(3)
  const [people, setPeople] = useState('')
  const [description, setDescription] = useState('')
  const [photo, setPhoto] = useState(null)
  const [position, setPosition] = useState(null)
  const [geoState, setGeoState] = useState(GEO_STATE.LOCATING)
  const [pincode, setPincode] = useState('')
  const [pincodeError, setPincodeError] = useState(null)
  // §4.1 — the app opens on a menu, not on the form. REPORT EMERGENCY is still
  // one tap from launch; the other four are things people look for when they
  // are not the one in trouble.
  const [view, setView] = useState('home')
  const [vulnerable, setVulnerable] = useState({})
  // Bumped by the "Use my GPS" button to restart the watcher. A first attempt
  // that timed out indoors very often succeeds on a second try by a window.
  const [gpsNonce, setGpsNonce] = useState(0)
  const [resolving, setResolving] = useState(false)
  const [queued, setQueued] = useState([])
  const [submitted, setSubmitted] = useState(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState(null)
  const [online, setOnline] = useState(navigator.onLine)

  const refreshQueue = () => outbox.pending().then(setQueued).catch(() => {})

  // Ask for location immediately: by the time someone has chosen a hazard the
  // GPS should already have a fix, so submission takes seconds not minutes.
  useEffect(
    () =>
      watchLocation(
        (fix) => {
          // A manually entered pincode is ~3 km. Never let a late, coarse GPS
          // fix quietly overwrite a better answer the user already gave us.
          setPosition((current) =>
            current && current.source !== 'gps' && fix.accuracy > current.accuracy
              ? current
              : fix,
          )
          setGeoState(GEO_STATE.LOCKED)
        },
        (reason) => setGeoState(reason),
      ),
    [gpsNonce],
  )

  const retryGps = () => {
    setGeoState(GEO_STATE.LOCATING)
    setGpsNonce((n) => n + 1)
  }

  const useManualPincode = async () => {
    if (pincode.trim().length < 6) return
    setResolving(true)
    setPincodeError(null)
    try {
      setPosition(await geocodePincode(pincode.trim()))
    } catch (err) {
      // A PIN from another district is a real answer, not a failure to hide:
      // tell the reporter which district this deployment covers so they know
      // to call instead of retyping.
      setPincodeError(
        err instanceof OutOfDistrictError ? err.message : t.pincodeFailed,
      )
    }
    setResolving(false)
  }

  useEffect(() => {
    refreshQueue()
    const goOnline = () => {
      setOnline(true)
      outbox.flush().then(refreshQueue)
    }
    const goOffline = () => setOnline(false)
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
    }
  }, [])

  const submit = async () => {
    if (!hazard || !position || busy) return
    setBusy(true)
    setFailure(null)

    // try/finally around the whole thing. Nothing that happens below is allowed
    // to leave the button stuck reading "Sending…" — a spinner that never ends
    // is indistinguishable from a broken app to someone who needs help now.
    try {
      const record = {
        client_report_uuid: outbox.newId(),
        client_ts: new Date().toISOString(),
        lat: position.lat,
        lng: position.lng,
        gps_accuracy_m: position.accuracy,
        hazard_type: hazard,
        severity_raw: severity,
        description,
        people_reported: people ? Number(people) : undefined,
        ...vulnerable,
        photo,
        synced: false,
      }

      // 1. Persist FIRST, always. Everything after this can fail safely.
      //    If even this fails there is no safe copy, so say so rather than
      //    pretending the report is on its way.
      try {
        await outbox.put(record)
      } catch {
        setFailure(t.storageFailed)
        return
      }
      await refreshQueue()

      try {
        const result = await outbox.send(record)
        setSubmitted({ ...result, offline: false })
      } catch {
        // 2. Queued. Background Sync flushes it the moment a tower returns.
        if ('serviceWorker' in navigator && 'SyncManager' in window) {
          try {
            const reg = await navigator.serviceWorker.ready
            await reg.sync.register('flush-outbox')
          } catch { /* the online listener above is the fallback */ }
        }
        setSubmitted({ offline: true, reference_code: null })
      }

      await refreshQueue()
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    setSubmitted(null)
    setHazard(null)
    setSeverity(3)
    setPeople('')
    setDescription('')
    setPhoto(null)
    setVulnerable({})
    setFailure(null)
  }

  if (submitted) {
    return (
      <div className="screen screen--done">
        <div className="done__mark">{submitted.offline ? '⏳' : '✓'}</div>
        <h1>{submitted.offline ? t.queuedTitle : t.sentTitle}</h1>
        <p>{submitted.offline ? t.queuedBody : t.sentBody}</p>
        {submitted.reference_code && (
          <div className="ref">
            {t.reference}
            <strong>{submitted.reference_code}</strong>
          </div>
        )}
        <button className="big" onClick={reset}>{t.reportAnother}</button>
        {submitted.reference_code && (
          <button
            className="ghost"
            onClick={() => {
              reset()
              setView('track')
            }}
          >
            {t.trackTitle}
          </button>
        )}
      </div>
    )
  }

  const secondaryProps = { t, onBack: () => setView('home'), position }
  if (view === 'track')
    return <TrackScreen {...secondaryProps} recent={queued} />
  if (view === 'shelters') return <SheltersScreen {...secondaryProps} />
  if (view === 'alerts') return <AlertsScreen {...secondaryProps} />
  if (view === 'contacts') return <ContactsScreen {...secondaryProps} />

  if (view === 'home') {
    return (
      <div className="screen screen--home">
        <header className="head">
          <div>
            <div className="head__brand">SETU</div>
            <div className="head__sub">{t.homeTitle}</div>
          </div>
          <div className="langs">
            {Object.keys(STRINGS).map((code) => (
              <button
                key={code}
                className={code === lang ? 'is-active' : ''}
                onClick={() => setLang(code)}
              >
                {STRINGS[code].label}
              </button>
            ))}
          </div>
        </header>

        {!online && <div className="banner banner--offline">{t.offlineBanner}</div>}
        {queued.length > 0 && (
          <div className="banner">{t.queuedCount(queued.length)}</div>
        )}

        {/* Deliberately enormous. In an emergency this is the only control
            that matters, and it should be hittable without aiming. */}
        <button className="panic" onClick={() => setView('report')}>
          {t.reportEmergency}
        </button>

        <nav className="homenav">
          <button onClick={() => setView('alerts')}>{t.viewAlerts}</button>
          <button onClick={() => setView('track')}>{t.myReports}</button>
          <button onClick={() => setView('shelters')}>{t.nearbyShelters}</button>
          <button onClick={() => setView('contacts')}>{t.emergencyContacts}</button>
        </nav>

        <p className="privacy">{t.privacy}</p>
      </div>
    )
  }

  return (
    <div className="screen">
      <header className="head">
        <div>
          <div className="head__brand">SETU</div>
          <div className="head__sub">{t.tagline}</div>
        </div>
        <div className="langs">
          {Object.keys(STRINGS).map((code) => (
            <button
              key={code}
              className={code === lang ? 'is-active' : ''}
              onClick={() => setLang(code)}
            >
              {STRINGS[code].label}
            </button>
          ))}
        </div>
      </header>

      {failure && <div className="banner banner--offline">{failure}</div>}
      {!online && <div className="banner banner--offline">{t.offlineBanner}</div>}
      {queued.length > 0 && (
        <div className="banner">{t.queuedCount(queued.length)}</div>
      )}

      <section>
        <h2>{t.whatHappened}</h2>
        <div className="hazards">
          {HAZARDS.map((h) => (
            <button
              key={h.key}
              className={`hazard${hazard === h.key ? ' is-active' : ''}`}
              onClick={() => setHazard(h.key)}
            >
              <span className="hazard__icon">{h.icon}</span>
              {t.hazards[h.key]}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2>{t.howSevere}</h2>
        <div className="severity">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              className={`sev${severity === n ? ' is-active' : ''}`}
              onClick={() => setSeverity(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <div className="scale">
          <span>{t.minor}</span>
          <span>{t.lifeThreatening}</span>
        </div>
      </section>

      <section>
        <h2>{t.howMany}</h2>
        <input
          className="field"
          type="number"
          inputMode="numeric"
          min="0"
          value={people}
          onChange={(e) => setPeople(e.target.value)}
          placeholder="0"
        />
      </section>

      <section>
        <h2>{t.whoNeedsHelp}</h2>
        <div className="chips">
          {VULNERABLE.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${vulnerable[key] ? ' is-active' : ''}`}
              aria-pressed={!!vulnerable[key]}
              onClick={() =>
                setVulnerable((v) => ({ ...v, [key]: !v[key] }))
              }
            >
              {t[label]}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2>{t.details}</h2>
        <textarea
          className="field"
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t.detailsPlaceholder}
        />
      </section>

      <section>
        <label className="photo">
          <input
            type="file"
            accept="image/*"
            capture="environment"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (file) setPhoto(await outbox.compressImage(file))
            }}
          />
          {photo ? t.photoAttached : t.addPhoto}
        </label>
      </section>

      {/* Location. BOTH ways in, always, side by side.

          This used to hide the PIN input while GPS was still searching and hide
          it again once GPS locked — so a phone with slow GPS showed "Finding
          your location…" and nothing else, and a phone with good GPS gave the
          reporter no way to correct a wrong fix. Neither is acceptable: the
          person reporting knows where they are better than the handset does.

          A report is never blocked on GPS. The optimizer handles coarse
          positions correctly (lower trust, wider clustering radius), so a 3 km
          fix is genuinely useful and a missing report is not. */}
      <section className="loc">
        <h2>{t.yourLocation}</h2>

        <div className={`loc__status loc__status--${position ? (position.source === 'gps' ? 'gps' : 'coarse') : geoState === GEO_STATE.LOCATING ? 'wait' : 'err'}`}>
          {position ? (
            <>
              <div className="loc__headline">
                {position.source === 'gps' ? t.locationLocked : t.locationApprox}{' '}
                ±{Math.round(position.accuracy) >= 1000
                  ? `${(position.accuracy / 1000).toFixed(1)} km`
                  : `${Math.round(position.accuracy)} m`}
                {position.name ? ` · ${position.name}` : ''}
              </div>
              {/* The actual coordinates, readable. This is what goes on the
                  operator's map, so the reporter should be able to see it and
                  read it out over a phone if the data connection dies. */}
              <div className="loc__coords">
                {position.lat.toFixed(5)}, {position.lng.toFixed(5)}
              </div>
            </>
          ) : geoState === GEO_STATE.LOCATING ? (
            <div className="loc__headline">{t.locating}</div>
          ) : (
            <div className="loc__headline">{t.geoReason[geoState]}</div>
          )}
        </div>

        {/* Option A — the handset. Always offered, and re-tappable: a first
            attempt that timed out indoors often succeeds by a window. */}
        <button
          className="loc__gps"
          onClick={retryGps}
          disabled={geoState === GEO_STATE.LOCATING}
        >
          {geoState === GEO_STATE.LOCATING
            ? t.locating
            : position && position.source === 'gps'
              ? t.gpsRefresh
              : t.useGps}
        </button>

        <div className="loc__or">{t.orUsePin}</div>

        {/* Option B — a PIN code. Always visible, even with a good GPS fix,
            because the reporter may know the fix is wrong. */}
        <div className="fallback__row">
          <input
            className="field"
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={pincode}
            onChange={(e) => {
              setPincode(e.target.value.replace(/\D/g, ''))
              setPincodeError(null)
            }}
            placeholder={t.pincodePlaceholder}
          />
          <button onClick={useManualPincode} disabled={pincode.length < 6 || resolving}>
            {resolving ? '…' : t.usePincode}
          </button>
        </div>
        {pincodeError && <p className="fallback__error">{pincodeError}</p>}

        {/* Once we know where they are, the other thing they need is where to
            walk to. One tap, without losing the half-filled form. */}
        {position && (
          <button className="loc__shelters" onClick={() => setView('shelters')}>
            {t.nearbyShelters} →
          </button>
        )}
      </section>

      <button
        className="big submit"
        disabled={!hazard || !position || busy}
        onClick={submit}
      >
        {busy ? t.sending : t.send}
      </button>

      <p className="fineprint">{t.privacy}</p>
    </div>
  )
}
