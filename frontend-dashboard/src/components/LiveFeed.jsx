import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { apiUrl } from '../api'

/**
 * The corridor feed: what is happening in every district, scrolling.
 *
 * A Collector works one district, but the district upstream is where the
 * mutual-aid request comes from and where their own units may be sent. This is
 * the peripheral vision the single-district dashboard deliberately does not
 * give them — sixteen boards' worth of state, ranked worst first, moving slowly
 * enough to read.
 *
 * Three kinds of item, and they are different claims:
 *
 *   OUTLOOK   — hazard likelihood derived from live rainfall, wind and terrain.
 *               A translation of published IMD thresholds, not a forecast, and
 *               every item carries the reason so it can be argued with.
 *   LIVE      — incidents open right now. Not a prediction at all: a count of
 *               people who have asked for help.
 *   ALERT     — an official CAP warning in force. Outranks both.
 *
 * ── Scrolling ─────────────────────────────────────────────────────────────
 *
 * Drifts on its own, and yields the moment you touch it.
 *
 * The first version animated a CSS transform inside an `overflow: hidden` box.
 * That loops beautifully and is completely unusable: a transformed track has no
 * scrollable overflow, so a wheel, a trackpad or a finger did nothing at all.
 * An operator who wants to read the fourth item down could only wait for it to
 * come round again.
 *
 * So it is NATIVE scrolling, nudged by a rAF loop. The container is a real
 * scroll area — wheel, touch, drag, keyboard and the scrollbar all work — and
 * the loop simply advances `scrollTop` a few pixels a second when nobody is
 * interacting. Any interaction pauses the drift and it resumes a couple of
 * seconds after you stop, so reading never fights the animation.
 *
 * The list is still rendered twice: when the drift passes the halfway point we
 * subtract exactly half the scroll height, which lands on the identical item in
 * the second copy. The jump is invisible and the feed is endless in both
 * directions.
 */

const BAND_ORDER = { danger: 0, warning: 1, watch: 2, normal: 3, unknown: 4 }

const LEVEL_TONE = {
  imminent: 'danger',
  likely: 'warning',
  possible: 'watch',
}

const HAZARD_LABEL = {
  flood: 'Flood',
  landslide: 'Landslide',
  cyclone_damage: 'Cyclone damage',
  storm_surge: 'Storm surge',
}

/** Flatten the corridor into a ranked list of readable lines. */
function buildItems(conditions) {
  const items = []

  for (const d of conditions) {
    const name = d.district_name || `District ${d.district_id}`

    for (const a of d.alerts || []) {
      items.push({
        key: `alert-${d.district_id}-${a.event}`,
        districtId: d.district_id,
        kind: 'ALERT',
        tone: 'danger',
        district: name,
        state: d.state,
        headline: `${a.event} · ${a.severity}`,
        detail: a.headline || a.instruction || '',
      })
    }

    for (const h of d.outlook || []) {
      if (h.level === 'unlikely') continue
      items.push({
        key: `out-${d.district_id}-${h.hazard}`,
        districtId: d.district_id,
        kind: 'OUTLOOK',
        tone: LEVEL_TONE[h.level] || 'watch',
        district: name,
        state: d.state,
        headline: `${HAZARD_LABEL[h.hazard] || h.hazard} ${h.level}`,
        detail: (h.why || []).join(' · '),
      })
    }

    const live = (d.active_hazards || []).reduce(
      (n, h) => n + Number(h.incidents || 0),
      0,
    )
    if (live > 0) {
      const worst = (d.active_hazards || [])[0]
      items.push({
        key: `live-${d.district_id}`,
        districtId: d.district_id,
        kind: 'LIVE',
        tone: d.people_affected > 50 ? 'warning' : 'watch',
        district: name,
        state: d.state,
        headline: `${live} open incident${live === 1 ? '' : 's'}`,
        detail:
          `${d.people_affected} people affected` +
          (worst ? ` · worst: ${worst.hazard} ${worst.worst_severity}` : ''),
      })
    }

    // A district with nothing happening is still information — it is where
    // spare units can come from.
    if (!live && !(d.outlook || []).some((h) => h.level !== 'unlikely')) {
      items.push({
        key: `clear-${d.district_id}`,
        districtId: d.district_id,
        kind: 'CLEAR',
        tone: 'calm',
        district: name,
        state: d.state,
        headline: 'No active incidents',
        detail: d.weather?.available
          ? `${Math.round(d.weather.temperature_c)}°C · wind ${Math.round(d.weather.wind_kmh)} km/h`
          : 'conditions unavailable',
      })
    }
  }

  const toneRank = { danger: 0, warning: 1, watch: 2, calm: 3 }
  items.sort((a, b) => (toneRank[a.tone] ?? 9) - (toneRank[b.tone] ?? 9))
  return items
}

export default function LiveFeed() {
  const [open, setOpen] = useState(false)
  const [conditions, setConditions] = useState([])
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const setDistrict = useStore((s) => s.setDistrict)

  const viewportRef = useRef(null)
  const pausedRef = useRef(false)
  const resumeTimer = useRef(null)
  // scrollTop is coerced to an integer by some browsers, so a sub-pixel-per-
  // frame drift would round to zero and never move. Accumulate the fraction
  // here and only apply whole pixels.
  const carryRef = useRef(0)

  const pause = useCallback(() => {
    pausedRef.current = true
    clearTimeout(resumeTimer.current)
  }, [])

  // Resume only after the reader has stopped. Snapping back into motion the
  // instant a finger lifts is what makes auto-scrolling feeds infuriating.
  const resumeSoon = useCallback((delay = 2600) => {
    clearTimeout(resumeTimer.current)
    resumeTimer.current = setTimeout(() => {
      pausedRef.current = false
    }, delay)
  }, [])

  useEffect(() => () => clearTimeout(resumeTimer.current), [])

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      setLoading(true)
      try {
        const res = await fetch(apiUrl('/api/v1/conditions'))
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        if (!cancelled) {
          setConditions(data)
          setFailed(false)
        }
      } catch {
        if (!cancelled) setFailed(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    // Weather is cached server-side for 15 minutes, so polling faster than this
    // would just re-read the same cache. Incident counts move faster, but the
    // WebSocket already drives the board the operator is actually working.
    const id = setInterval(load, 90_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const items = useMemo(() => buildItems(conditions), [conditions])

  // Pixels per second. Slow enough to read a two-line item as it passes; a
  // ticker you have to chase is worse than no ticker.
  const DRIFT_PX_PER_SEC = 20

  useEffect(() => {
    const el = viewportRef.current
    if (!el || !items.length) return

    // Somebody who has asked for less motion gets a plain scroll area.
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    let raf = 0
    let last = performance.now()

    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.1) // clamp after a tab switch
      last = now

      if (!pausedRef.current && el.scrollHeight > el.clientHeight + 4) {
        carryRef.current += DRIFT_PX_PER_SEC * dt
        const whole = Math.floor(carryRef.current)
        if (whole > 0) {
          carryRef.current -= whole
          el.scrollTop += whole
          // The list is duplicated, so half the scroll height is one full
          // cycle. Subtracting it lands on the identical item in copy two.
          const half = el.scrollHeight / 2
          if (el.scrollTop >= half) el.scrollTop -= half
        }
      }
      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [items.length])

  const worst = useMemo(() => {
    if (!conditions.length) return null
    return conditions.reduce(
      (acc, d) =>
        (BAND_ORDER[d.risk_band] ?? 9) < (BAND_ORDER[acc?.risk_band] ?? 9) ? d : acc,
      conditions[0],
    )
  }, [conditions])

  const alertCount = items.filter((i) => i.tone === 'danger' || i.tone === 'warning').length

  return (
    <>
      <button
        className={`feedbtn${alertCount ? ' has-alerts' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Live conditions across the corridor"
      >
        Corridor feed
        {alertCount > 0 && <span className="feedbtn__count">{alertCount}</span>}
      </button>

      <aside className={`feed${open ? ' is-open' : ''}`} aria-hidden={!open}>
        <header className="feed__head">
          <div>
            <h2>Corridor feed</h2>
            <p className="feed__sub">
              {conditions.length} districts
              {worst?.risk_band && worst.risk_band !== 'normal'
                ? ` · worst: ${worst.district_name} (${worst.risk_band})`
                : ' · all normal'}
            </p>
          </div>
          <button className="feed__close" onClick={() => setOpen(false)}>
            ✕
          </button>
        </header>

        {failed && <p className="feed__err">Conditions unavailable — showing nothing rather than stale guesses.</p>}
        {loading && !items.length && <p className="feed__muted">Loading…</p>}

        {/* A real scroll area. The list is rendered TWICE so the drift can
            wrap invisibly at the halfway point, and so a reader scrolling by
            hand never hits an end. */}
        <div
          ref={viewportRef}
          className="feed__viewport"
          onMouseEnter={pause}
          onMouseLeave={() => resumeSoon(600)}
          onWheel={() => {
            pause()
            resumeSoon()
          }}
          onTouchStart={pause}
          onTouchEnd={() => resumeSoon()}
          onPointerDown={pause}
          onKeyDown={pause}
          tabIndex={0}
          role="feed"
          aria-label="Live conditions across the corridor"
        >
          <div className="feed__track">
            {[...items, ...items].map((it, idx) => (
              <button
                key={`${it.key}-${idx}`}
                className={`feeditem feeditem--${it.tone}`}
                onClick={() => {
                  setDistrict(it.districtId)
                  setOpen(false)
                }}
              >
                <div className="feeditem__top">
                  <span className={`feeditem__kind feeditem__kind--${it.tone}`}>
                    {it.kind}
                  </span>
                  <span className="feeditem__district">{it.district}</span>
                  <span className="feeditem__state">{it.state}</span>
                </div>
                <div className="feeditem__headline">{it.headline}</div>
                {it.detail && <div className="feeditem__detail">{it.detail}</div>}
              </button>
            ))}
          </div>
        </div>

      </aside>
    </>
  )
}
